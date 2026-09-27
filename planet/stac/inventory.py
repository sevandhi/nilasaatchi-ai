"""Sentinel-2 L2A scene inventory for the park AOI (``make s2-inventory``).

Steps (all idempotent; re-runs hit the per-scene cache and upsert):
1. STAC search over the park bbox + 2 km, 2019-01-01 -> today, no cloud filter (SCL decides per
   pixel). Earth Search first, Planetary Computer mirror if it fails. Processing-baseline duplicates
   (``_0/_1/_2``) collapse to the highest suffix; the dropped ids go to ``collapsed_ids``.
2. AOI usability from SCL only: one windowed read of the SCL band per scene onto the shared AOI grid
   (``planet.aoi.aoi_grid``, EPSG:32643 10 m), cached in ``data/s2/<scene>/aoi_<gridkey>.tif`` so the P3
   extraction reuses it. Per scene: valid fraction (SCL 4/5/6/7) inside the park polygon, nodata /
   cloud / shadow / cirrus fractions, and per-parcel valid pixel counts (-5 m buffer) ->
   ``data/s2/scl_parcel_valid.parquet``.
3. Upsert into ``s2_scene`` (columns discovered at run time: ``aoi_valid_frac``/``aoi_cloud_frac`` columns,
   everything else in the ``meta`` jsonb, 0004). Snapshot to ``data/s2/inventory.parquet``.
4. ESA WorldCover 2021 weak-label fractions per parcel (``planet.extract.worldcover``).

The AOI valid fraction is a *scene selection* signal only; per-parcel support is decided again in
P3 (``low_support`` < 10 valid px). Nothing here is a land-use conclusion.
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import numpy as np

from planet import aoi, tabio
from planet.extract import parcel_stats as ps
from planet.stac import search as st

log = logging.getLogger("planet.stac.inventory")

START = "2019-01-01"
OUT_DIR = ps.CACHE_DIR
INVENTORY_PARQUET = OUT_DIR / "inventory.parquet"
PARCEL_VALID_PARQUET = OUT_DIR / "scl_parcel_valid.parquet"

SCL_NODATA = (0,)
SCL_DEFECTIVE = (1,)
SCL_SHADOW = (2, 3)  # 2 dark area/topographic shadow, 3 cloud shadow
SCL_CLOUD = (8, 9)
SCL_CIRRUS = (10,)
CLOUD_BUCKETS = ((0, 10), (10, 20), (20, 50), (50, 80), (80, 101))
VALID_BUCKETS = (0.0, 0.2, 0.4, 0.6, 0.8, 0.95)
META_COLUMNS = ("meta", "metadata", "properties", "props", "extra", "aoi_stats")
SCL_VERSION = "scl-v1"


# --------------------------------------------------------------------------- scene rows
def storage_href(href: str) -> str:
    """Drop query strings (Planetary Computer SAS tokens expire; re-sign at read time)."""
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")) if parts.query else href


def scene_row(rec: st.SceneRecord, usable: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One ``s2_scene`` row (column-agnostic dict) for a deduplicated scene record."""
    meta: dict[str, Any] = {"source": rec.source, "epsg": rec.epsg, "stac": dict(rec.meta)}
    if rec.source == "planetary-computer":
        meta["href_signing"] = "planetary_computer.sign"
    row = {
        "id": rec.id,
        "datetime": rec.datetime,
        "date": rec.date,
        "tile": rec.tile,
        "cloud": None if rec.cloud is None else float(rec.cloud),
        "baseline": rec.processing_baseline,
        "hrefs": {b: storage_href(h) for b, h in rec.hrefs.items()},
        "dn_offset": int(rec.dn_offset),
        "collapsed_ids": list(rec.duplicates),
        "source": rec.source,
        "epsg": rec.epsg,
    }
    if usable:
        row.update({k: usable[k] for k in ("aoi_valid_frac", "aoi_cloud_frac", "aoi_cache_path") if k in usable})
        meta["aoi"] = {k: v for k, v in usable.items() if k not in ("aoi_cache_path",)}
    row["meta"] = meta
    return row


# --------------------------------------------------------------------------- SCL usability
def scl_stats(scl: np.ndarray, park_mask: np.ndarray, labels: np.ndarray, n_labels: int) -> tuple[dict[str, Any], np.ndarray]:
    """Scene-level SCL fractions inside ``park_mask`` plus per-parcel valid pixel counts.

    Returns ``(stats, n_valid)`` where ``n_valid[pid]`` = valid SCL pixels of parcel ``pid`` (index 0 =
    background). Fractions are over all park pixels, so nodata (swath edge) counts as unusable.
    """
    park = park_mask.astype(bool)
    n_park = int(park.sum())
    vals = scl[park]
    valid = ps.valid_mask(scl)

    def frac(classes: Iterable[int]) -> float:
        return float(np.isin(vals, list(classes)).sum() / n_park) if n_park else 0.0

    n_valid = np.bincount(labels[valid & (labels > 0)], minlength=n_labels + 1)
    fg = labels > 0
    stats = {
        "aoi_valid_frac": round(frac(ps.VALID_SCL), 4),
        "aoi_nodata_frac": round(frac(SCL_NODATA), 4),
        "aoi_cloud_frac": round(frac(SCL_CLOUD), 4),
        "aoi_cirrus_frac": round(frac(SCL_CIRRUS), 4),
        "aoi_shadow_frac": round(frac(SCL_SHADOW), 4),
        "aoi_defective_frac": round(frac(SCL_DEFECTIVE), 4),
        "aoi_px": n_park,
        "parcels_valid_frac": round(float(valid[fg].mean()) if fg.any() else 0.0, 4),
        "n_parcels_ge10": int((n_valid[1:] >= ps.LOW_SUPPORT_PX).sum()),
        "scl_version": SCL_VERSION,
    }
    return stats, n_valid


def usable_signed(rec: st.SceneRecord) -> st.SceneRecord:
    """Sign Planetary Computer hrefs for reading (no-op for Earth Search)."""
    if rec.source != "planetary-computer":
        return rec
    import dataclasses

    import planetary_computer

    return dataclasses.replace(rec, hrefs={b: planetary_computer.sign(h) for b, h in rec.hrefs.items()})


def scene_usability(rec: st.SceneRecord, grid: ps.Grid, park_mask: np.ndarray, labels: np.ndarray,
                    n_labels: int, cache_dir: Path = OUT_DIR) -> tuple[dict[str, Any], np.ndarray]:
    dn = ps.read_scene(usable_signed(rec), grid, bands=("scl",), cache_dir=cache_dir, workers=1)
    stats, n_valid = scl_stats(dn["scl"], park_mask, labels, n_labels)
    stats["aoi_cache_path"] = str(ps._cache_path(rec.id, grid, Path(cache_dir)).relative_to(aoi.REPO))
    stats["grid_key"] = grid.key()
    return stats, n_valid


# --------------------------------------------------------------------------- DB upsert
def table_columns(conn: Any, table: str = "s2_scene") -> dict[str, str]:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s", (table,)).fetchall()
    return {r[0]: r[1] for r in rows}


_ALIASES = {"baseline": ("baseline", "processing_baseline"), "collapsed_ids": ("collapsed_ids", "duplicates"),
            "datetime": ("datetime", "acquired", "acquired_at"), "cloud": ("cloud", "cloud_cover"),
            "date": ("date", "acq_date")}


def map_row(row: Mapping[str, Any], columns: Mapping[str, str]) -> dict[str, Any]:
    """Map a scene row onto the available columns. Unmapped fields go into the first jsonb metadata
    column (``meta`` since migration 0004); ``hrefs`` only ever holds asset hrefs."""
    if "id" not in columns:
        raise RuntimeError("s2_scene has no 'id' column")
    out: dict[str, Any] = {}
    leftovers: dict[str, Any] = {}
    for key, val in row.items():
        if key == "meta":
            continue
        target = next((c for c in _ALIASES.get(key, (key,)) if c in columns), None)
        if target is None:
            leftovers[key] = val
        else:
            out[target] = val
    meta = dict(row.get("meta") or {})
    meta.update({k: v for k, v in leftovers.items() if k not in ("date",)})
    meta_col = next((c for c in META_COLUMNS if c in columns and columns[c] in ("jsonb", "json")), None)
    if meta_col:
        out[meta_col] = meta
    else:  # hrefs hold only asset hrefs (the evidence) -- never park metadata there (0004)
        log.warning("s2_scene has no jsonb metadata column; dropping %s", sorted(meta))
    return out


def _adapt(val: Any, data_type: str) -> Any:
    from psycopg.types.json import Jsonb

    if data_type in ("jsonb", "json"):
        return Jsonb(val, dumps=lambda o: json.dumps(o, default=str))
    if data_type == "ARRAY":
        return list(val or [])
    if data_type == "date" and isinstance(val, datetime):
        return val.date()
    return val


def upsert_sql(cols: Sequence[str], table: str = "s2_scene") -> str:
    names = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    def rhs(c: str) -> str:
        # a pixel-verified dn_offset (planet.extract.dn_check --apply) survives inventory re-runs
        if table == "s2_scene" and c == "dn_offset":
            return (f"CASE WHEN {table}.meta ? 'dn_offset_check' THEN "
                    f"({table}.meta -> 'dn_offset_check' ->> 'verified')::int ELSE EXCLUDED.dn_offset END")
        if table == "s2_scene" and c == "meta":
            return (f"EXCLUDED.meta || CASE WHEN {table}.meta ? 'dn_offset_check' THEN "
                    f"jsonb_build_object('dn_offset_check', {table}.meta -> 'dn_offset_check') ELSE '{{}}'::jsonb END")
        return f"EXCLUDED.{c}"

    upd = ", ".join(f"{c} = {rhs(c)}" for c in cols if c != "id")
    return f"INSERT INTO {table} ({names}) VALUES ({ph}) ON CONFLICT (id) DO UPDATE SET {upd}"


def upsert(conn: Any, rows: Sequence[Mapping[str, Any]], table: str = "s2_scene") -> dict[str, int]:
    """Idempotent upsert keyed on ``id``. Rows whose id is now a collapsed duplicate of a kept scene
    are deleted (a newer baseline replaced them) unless something references them."""
    columns = table_columns(conn, table)
    if not columns:
        raise RuntimeError(f"table {table} does not exist (run make migrate)")
    n = 0
    with conn.transaction():
        for row in rows:
            mapped = map_row(row, columns)
            cols = list(mapped)
            conn.execute(upsert_sql(cols, table), [_adapt(mapped[c], columns[c]) for c in cols])
            n += 1
    stale = sorted({d for r in rows for d in r.get("collapsed_ids", [])})
    removed = 0
    if stale:
        try:
            with conn.transaction():
                cur = conn.execute(f"DELETE FROM {table} WHERE id = ANY(%s)", (stale,))
                removed = cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001 - FK from parcel_obs etc.
            log.warning("could not delete %d superseded baseline rows: %s", len(stale), exc)
    return {"upserted": n, "removed_superseded": removed, "columns": len(columns)}


def wait_for_table(dsn: str, table: str, timeout_s: float) -> bool:
    import psycopg

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(dsn) as c:
                if c.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                    return True
        except Exception as exc:  # noqa: BLE001
            log.warning("db not reachable: %s", exc)
        if time.monotonic() >= deadline:
            return False
        time.sleep(15)


# --------------------------------------------------------------------------- summaries
def cloud_bucket(c: float | None) -> str:
    if c is None:
        return "unknown"
    for lo, hi in CLOUD_BUCKETS:
        if lo <= c < hi:
            return f"{lo}-{min(hi, 100)}%"
    return "unknown"


def month_table(dates: Iterable[date]) -> dict[int, list[int]]:
    tab: dict[int, list[int]] = collections.defaultdict(lambda: [0] * 12)
    for d in dates:
        tab[d.year][d.month - 1] += 1
    return dict(sorted(tab.items()))


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dates = [r["date"] for r in rows]
    by_year = collections.Counter(d.year for d in dates)
    clouds = collections.Counter(cloud_bucket(r["cloud"]) for r in rows)
    vf = [r.get("aoi_valid_frac") for r in rows]
    have = [v for v in vf if v is not None]
    valid_ge = {f">={t}": sum(v >= t for v in have) for t in VALID_BUCKETS[1:]}
    year_ge = {y: {"total": by_year[y],
                   ">=0.6": sum(1 for r in rows if r["date"].year == y and (r.get("aoi_valid_frac") or 0) >= 0.6),
                   ">=0.8": sum(1 for r in rows if r["date"].year == y and (r.get("aoi_valid_frac") or 0) >= 0.8)}
               for y in sorted(by_year)}
    return {
        "total": len(rows),
        "raw_items": len(rows) + sum(len(r.get("collapsed_ids", [])) for r in rows),
        "collapsed": sum(len(r.get("collapsed_ids", [])) for r in rows),
        "cloud_lt20": sum(1 for r in rows if r["cloud"] is not None and r["cloud"] < 20),
        "by_year": dict(sorted(by_year.items())),
        "by_month": month_table(dates),
        "cloud_buckets": {k: clouds.get(k, 0) for k in [cloud_bucket(lo) for lo, _ in CLOUD_BUCKETS] + ["unknown"]},
        "aoi_valid_scored": len(have),
        "aoi_valid_ge": valid_ge,
        "year_valid": year_ge,
        "sources": dict(collections.Counter(r["source"] for r in rows)),
        "dn_offset": dict(collections.Counter(r["dn_offset"] for r in rows)),
        "baselines": dict(sorted(collections.Counter(str(r["baseline"]) for r in rows).items())),
    }


def obs_per_parcel(mat: Any, min_px: int = ps.LOW_SUPPORT_PX, min_frac: float = 0.0) -> Any:
    """Per parcel: number of scenes with ``n_valid >= min_px`` and ``n_valid/n_total >= min_frac``,
    plus the longest gap (days) between such scenes (incl. edges at the first/last scene date)."""
    import pandas as pd

    m = mat.copy()
    m["date"] = pd.to_datetime(m["date"])
    ok = (m.n_valid >= min_px) & (m.n_valid >= min_frac * m.n_total.clip(lower=1))
    lo, hi = m["date"].min(), m["date"].max()

    span = float((hi - lo).days)

    def gap(s: pd.Series) -> float:
        d = np.unique(np.concatenate([[0.0], (s - lo).dt.days.to_numpy(dtype=float), [span]]))
        return float(np.diff(d).max()) if len(d) > 1 else span

    good = m[ok]
    n = good.groupby("parcel_uid").size().reindex(m.parcel_uid.unique(), fill_value=0)
    g = good.groupby("parcel_uid")["date"].apply(gap).reindex(n.index).fillna(span)
    return pd.DataFrame({"n_obs": n, "max_gap_days": g})


def obs_report(mat: Any) -> list[str]:
    """Human-readable expected-usable-observation lines for P3 from the SCL parcel matrix."""
    import pandas as pd

    lines = []
    n_tot = mat.groupby("parcel_uid").n_total.first()
    small = int((n_tot < ps.LOW_SUPPORT_PX).sum())
    lines.append(f"parcels: {len(n_tot)}; structurally low_support (< {ps.LOW_SUPPORT_PX} px inside -5 m buffer): "
                 f"{small}; median pixels/parcel {n_tot.median():.0f}")
    for label, frac in ((f">= {ps.LOW_SUPPORT_PX} valid px", 0.0), ("valid_frac >= 0.8 and >= 10 px", 0.8)):
        o = obs_per_parcel(mat, min_frac=frac)
        q = o.quantile([0.1, 0.5, 0.9])
        lines.append(f"usable obs/parcel [{label}]: p10 {q.n_obs[0.1]:.0f} / p50 {q.n_obs[0.5]:.0f} / "
                     f"p90 {q.n_obs[0.9]:.0f}; max gap p50 {q.max_gap_days[0.5]:.0f} d / p90 "
                     f"{q.max_gap_days[0.9]:.0f} d; never usable {(o.n_obs == 0).sum()}")
    m = mat.assign(year=pd.to_datetime(mat["date"]).dt.year)
    m = m[m.n_valid >= ps.LOW_SUPPORT_PX]
    per = m.groupby(["year", "parcel_uid"]).size().unstack(0).reindex(n_tot.index).fillna(0)
    lines.append("usable obs/parcel by year (p10/p50/p90): " + ", ".join(
        f"{y}: {per[y].quantile(0.1):.0f}/{per[y].median():.0f}/{per[y].quantile(0.9):.0f}" for y in per.columns))
    return lines


def print_report(s: Mapping[str, Any], obs: Sequence[str] | None, elapsed: float) -> None:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    print(f"\nS2 L2A inventory: {s['total']} unique acquisitions ({s['raw_items']} raw items, "
          f"{s['collapsed']} baseline duplicates collapsed); {s['cloud_lt20']} with scene cloud < 20%")
    print(f"sources {s['sources']}  dn_offset {s['dn_offset']}  baselines {s['baselines']}")
    print("\nyear  " + " ".join(f"{m:>3}" for m in months) + "  total  AOI>=0.6  AOI>=0.8")
    for y, row in s["by_month"].items():
        yv = s["year_valid"][y]
        print(f"{y}  " + " ".join(f"{c:>3}" for c in row) + f"  {yv['total']:>5}  {yv['>=0.6']:>8}  {yv['>=0.8']:>8}")
    print("\nscene cloud buckets: " + ", ".join(f"{k}: {v}" for k, v in s["cloud_buckets"].items()))
    print(f"AOI (park) SCL-valid fraction, {s['aoi_valid_scored']} scenes scored: "
          + ", ".join(f"{k}: {v}" for k, v in s["aoi_valid_ge"].items()))
    if obs is not None:
        for line in obs:
            print(line)
    print(f"runtime {elapsed:.0f} s\n")


# --------------------------------------------------------------------------- orchestration
def collect(source: str, end: date | None = None, start: date | str | None = None) -> list[st.SceneRecord]:
    """STAC search over the AOI bbox (no cloud filter, baseline-deduplicated). ``start`` defaults to
    ``START`` (the full archive); ``planet.refresh`` passes a recent date for incremental runs."""
    end = end or datetime.now(UTC).date()
    return st.search_scenes(aoi.aoi_bbox(), str(start or START), end.isoformat(), source=source)


def compute_usability(recs: Sequence[st.SceneRecord], workers: int, limit: int | None) -> tuple[dict[str, dict], Any]:
    import pandas as pd
    from rasterio.features import rasterize

    epsgs = collections.Counter(r.epsg or aoi.UTM_EPSG for r in recs)
    epsg = epsgs.most_common(1)[0][0] if epsgs else aoi.UTM_EPSG
    grid = aoi.aoi_grid(epsg)
    park = aoi.load_boundary().to_crs(grid.crs)
    park_mask = rasterize(((g, 1) for g in park.geometry), out_shape=grid.shape, transform=grid.transform,
                          fill=0, dtype="uint8").astype(bool)
    parcels = aoi.load_parcels_uid()
    labels = ps.rasterise(ps.prepare_parcels(parcels, grid.crs, ps.DEFAULT_BUFFER_M), grid)
    n_labels = int(parcels.pid.max())
    n_total = np.bincount(labels.ravel(), minlength=n_labels + 1)
    todo = [r for r in recs if (r.epsg or epsg) == epsg]
    if limit:
        todo = sorted(todo, key=lambda r: r.datetime, reverse=True)[:limit]
    out: dict[str, dict] = {}
    parts = []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scene_usability, r, grid, park_mask, labels, n_labels): r for r in todo}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                stats, n_valid = f.result()
            except Exception as exc:  # noqa: BLE001 - keep going; scene stays unscored
                log.warning("SCL read failed for %s: %s", r.id, exc)
                continue
            out[r.id] = stats
            parts.append(pd.DataFrame({"pid": np.arange(1, n_labels + 1), "n_total": n_total[1:],
                                       "n_valid": n_valid[1:], "scene_id": r.id, "date": r.date.isoformat()}))
            if i % 50 == 0:
                print(f"  SCL {i}/{len(todo)} scenes ({time.monotonic() - t0:.0f} s)", flush=True)
    if not parts:
        return out, None
    mat = pd.concat(parts, ignore_index=True)
    mat = mat.merge(parcels[["pid", "parcel_uid"]], on="pid").drop(columns="pid")
    mat = mat[["parcel_uid", "scene_id", "date", "n_total", "n_valid"]].sort_values(["date", "parcel_uid"])
    return out, mat.reset_index(drop=True)


def write_snapshot(rows: Sequence[Mapping[str, Any]], path: Path = INVENTORY_PARQUET) -> None:
    import pandas as pd

    df = pd.DataFrame([{**{k: v for k, v in r.items() if k not in ("hrefs", "meta", "collapsed_ids")},
                        "hrefs": json.dumps(r["hrefs"]), "collapsed_ids": json.dumps(r["collapsed_ids"]),
                        "meta": json.dumps(r["meta"], default=str)} for r in rows])
    tabio.write_table(df, path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="auto", choices=["auto", "earth-search", "planetary-computer"])
    ap.add_argument("--limit", type=int, default=None, help="score only the N most recent scenes (smoke runs)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--no-scl", action="store_true", help="skip the SCL usability pass")
    ap.add_argument("--no-db", action="store_true", help="do not write s2_scene")
    ap.add_argument("--wait-db", type=float, default=0, help="seconds to wait for s2_scene to exist")
    ap.add_argument("--no-worldcover", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    t0 = time.monotonic()

    recs = collect(args.source)
    print(f"STAC: {len(recs)} unique acquisitions over AOI bbox {aoi.aoi_bbox()} from {START}")
    usable: dict[str, dict] = {}
    mat = None
    if not args.no_scl:
        usable, mat = compute_usability(recs, args.workers, args.limit)
        if mat is not None:
            # never clobber the full matrix with a smoke run
            tabio.write_table(mat, PARCEL_VALID_PARQUET.with_name("scl_parcel_valid.limit.parquet")
                              if args.limit else PARCEL_VALID_PARQUET)
    rows = [scene_row(r, usable.get(r.id)) for r in recs]
    if not args.limit:
        write_snapshot(rows)

    if not args.no_db:
        from dotenv import load_dotenv

        load_dotenv(aoi.REPO / ".env")
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            print("DATABASE_URL not set; skipping s2_scene upsert", file=sys.stderr)
        elif not wait_for_table(dsn, "s2_scene", args.wait_db):
            print("s2_scene does not exist yet (make migrate); skipped DB upsert, snapshot kept in "
                  f"{INVENTORY_PARQUET.relative_to(aoi.REPO)}", file=sys.stderr)
        else:
            import psycopg

            with psycopg.connect(dsn) as conn:
                res = upsert(conn, rows)
                n_db = conn.execute("SELECT count(*) FROM s2_scene").fetchone()[0]
            print(f"s2_scene: {res}; rows now {n_db}")

    obs = obs_report(mat) if mat is not None else None
    print_report(summarise(rows), obs, time.monotonic() - t0)

    if not args.no_worldcover:
        from planet.extract import worldcover

        worldcover.main([])
    return 0


if __name__ == "__main__":
    sys.exit(main())
