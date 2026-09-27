"""T3.2 — per-parcel Sentinel-2 observations for every usable scene (``make s2-extract``).

For each ``s2_scene`` with ``aoi_valid_frac >= 0.2`` (D-026) on the shared AOI grid (``planet.aoi``,
EPSG:32643, 10 m):

1. fetch B02/B03/B04/B08/B11 (20 m -> 10 m bilinear) + SCL (nearest) into the per-scene cache
   ``data/s2/<scene>/aoi_<gridkey>.tif`` (the SCL band is already there from the P1 inventory);
2. per parcel median NDVI / NDWI / BSI / NDBI over SCL-valid pixels (4/5/6/7; cloud shadow, cloud,
   cirrus and snow excluded), ``n_px``, ``n_total``, ``valid_frac``, ``low_support`` (n_px < 10);
3. write ``data/s2/<scene>/obs_<version>.parquet`` — the per-scene done marker — plus a radiometry
   summary (dark-object percentiles) used for the dn_offset spot check;
4. assemble ``data/s2/parcel_obs.parquet`` and upsert ``parcel_obs`` (idempotent, keyed on
   ``(parcel_uid, scene_id)``).

Parcel footprints (D-015, D-026): the FMB polygon buffered 5 m inward (``buf5``). Parcels with fewer
than 10 pixels inside the buffer are *structurally* low-support; they are measured on the unbuffered
outline (``unbuffered``; ``all_touched`` if no pixel centre falls inside) and flagged ``mixed_pixel`` —
their values mix neighbouring land covers and downstream confidence is capped.

Pixel sets are computed per parcel (not by burning one label raster), so the 366 overlapping FMB
parcel pairs each keep their full footprint.

Every row carries its ``scene_id``; the asset hrefs live on ``s2_scene`` — that is the evidence.
Values are *signals needing field verification* (undetected cloud/haze, mixed pixels, weed flush).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from planet import aoi, tabio
from planet.extract import parcel_stats as ps
from planet.stac.search import SceneRecord

log = logging.getLogger("planet.extract")

EXTRACT_VERSION = "obs-v1"
MIN_AOI_VALID = 0.2
INDICES = ("ndvi", "ndwi", "bsi", "ndbi")
OBS_PARQUET = ps.CACHE_DIR / "parcel_obs.parquet"
OBS_COLUMNS = ("parcel_uid", "scene_id", "date", "ndvi", "ndwi", "bsi", "ndbi", "valid_frac", "n_px",
               "n_total", "low_support", "mixed_pixel", "geom_mode")


# --------------------------------------------------------------------------- parcel footprints
@dataclass
class ParcelPixels:
    """Flat pixel indices per parcel on one grid. ``idx``/``lab`` are parallel (a pixel may belong to
    several overlapping parcels); ``lab`` is the row number into ``uids``."""
    uids: np.ndarray        # (P,) parcel_uid
    mode: np.ndarray        # (P,) buf5 | unbuffered | all_touched
    n_total: np.ndarray     # (P,) pixels per footprint
    idx: np.ndarray         # (K,) flat pixel index into grid
    lab: np.ndarray         # (K,) parcel row

    @property
    def mixed(self) -> np.ndarray:
        return self.mode != "buf5"


def _pixels_of(geom: Any, grid: ps.Grid, all_touched: bool = False) -> np.ndarray:
    """Flat indices of grid pixels covered by ``geom`` (centre-in unless ``all_touched``)."""
    from rasterio.features import geometry_mask
    from rasterio.windows import Window, from_bounds

    if geom is None or geom.is_empty:
        return np.empty(0, dtype=np.int64)
    win = from_bounds(*geom.bounds, transform=grid.transform)
    c0, r0 = int(np.floor(win.col_off)) - 1, int(np.floor(win.row_off)) - 1
    c1, r1 = int(np.ceil(win.col_off + win.width)) + 1, int(np.ceil(win.row_off + win.height)) + 1
    c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, grid.width), min(r1, grid.height)
    if c1 <= c0 or r1 <= r0:
        return np.empty(0, dtype=np.int64)
    w = Window(c0, r0, c1 - c0, r1 - r0)
    from rasterio.windows import transform as wtransform

    inside = ~geometry_mask([geom], out_shape=(r1 - r0, c1 - c0), transform=wtransform(w, grid.transform),
                            all_touched=all_touched)
    rr, cc = np.nonzero(inside)
    return ((rr + r0) * grid.width + (cc + c0)).astype(np.int64)


def parcel_pixels(parcels: Any, grid: ps.Grid, buffer_m: float = ps.DEFAULT_BUFFER_M,
                  min_px: int = ps.LOW_SUPPORT_PX) -> ParcelPixels:
    """Footprint per parcel: -5 m buffer; structurally low-support (< ``min_px``) parcels fall back to
    the unbuffered outline, then to all-touched (D-026)."""
    g = parcels.to_crs(grid.crs)
    uids, modes, totals, idxs, labs = [], [], [], [], []
    for row, (uid, geom) in enumerate(zip(g["parcel_uid"], g.geometry)):
        buf = geom.buffer(buffer_m) if buffer_m else geom
        px = _pixels_of(buf, grid)
        mode = "buf5"
        if len(px) < min_px:
            px, mode = _pixels_of(geom, grid), "unbuffered"
            if len(px) == 0:
                px, mode = _pixels_of(geom, grid, all_touched=True), "all_touched"
        uids.append(uid)
        modes.append(mode)
        totals.append(len(px))
        idxs.append(px)
        labs.append(np.full(len(px), row, dtype=np.int32))
    return ParcelPixels(np.array(uids, dtype=object), np.array(modes, dtype=object),
                        np.array(totals, dtype=np.int32), np.concatenate(idxs), np.concatenate(labs))


# --------------------------------------------------------------------------- per-scene stats
def obs_from_arrays(dn: dict[str, np.ndarray], dn_offset: int, pix: ParcelPixels) -> Any:
    """Per-parcel observation table from raw DN arrays on the grid (pure; no I/O)."""
    import pandas as pd

    refl = {b: ps.reflectance(dn[b], dn_offset).ravel() for b in ps.SPECTRAL}
    ok = ps.valid_mask(dn["scl"]).ravel()
    for b in refl.values():
        ok &= np.isfinite(b)
    sel = ok[pix.idx]
    lab = pix.lab[sel]
    idx = pix.idx[sel]
    n = len(pix.uids)
    out = pd.DataFrame({"parcel_uid": pix.uids, "n_total": pix.n_total,
                        "n_px": np.bincount(lab, minlength=n).astype(np.int32)})
    sub = {b: v[idx] for b, v in refl.items()}
    for name in INDICES:
        out[name] = ps.grouped_median(lab, ps.compute_index(name, sub), n - 1)[:n].astype("float32")
    out["valid_frac"] = np.where(out.n_total > 0, out.n_px / out.n_total.clip(lower=1), 0.0).astype("float32")
    out["low_support"] = out.n_px < ps.LOW_SUPPORT_PX
    out["mixed_pixel"] = pix.mixed
    out["geom_mode"] = pix.mode
    return out


def radiometry(dn: dict[str, np.ndarray], park_mask: np.ndarray) -> dict[str, Any]:
    """Raw-DN percentiles over SCL-valid park pixels (for the dn_offset check)."""
    ok = ps.valid_mask(dn["scl"]) & park_mask
    res: dict[str, Any] = {"n_valid_px": int(ok.sum())}
    for b in ps.SPECTRAL:
        v = dn[b][ok & (dn[b] > 0)]
        res[b] = {f"p{q}": float(np.percentile(v, q)) for q in (0.5, 1, 5, 50)} if v.size else None
    return res


# --------------------------------------------------------------------------- worker
_W: dict[str, Any] = {}


def _init_worker(grid: ps.Grid, pix: ParcelPixels, park_mask: np.ndarray, cache_dir: str) -> None:
    logging.basicConfig(level=logging.WARNING)
    _W.update(grid=grid, pix=pix, park=park_mask, cache=Path(cache_dir))


def obs_path(scene_id: str, cache_dir: Path = ps.CACHE_DIR) -> Path:
    return Path(cache_dir) / scene_id / f"obs_{EXTRACT_VERSION}.parquet"


def process_scene(rec: SceneRecord, refresh: bool = False) -> tuple[str, int, float]:
    """Fetch (cached) + per-parcel stats for one scene; returns (scene_id, rows, seconds)."""
    t0 = time.monotonic()
    grid, pix, cache = _W["grid"], _W["pix"], _W["cache"]
    out = obs_path(rec.id, cache)
    if out.exists() and not refresh:
        return rec.id, -1, 0.0
    for attempt in range(4):  # transient S3 errors (empty replies, truncated ranges) are common
        try:
            dn = ps.read_scene(rec, grid, bands=ps.CACHE_BANDS, cache_dir=cache, workers=6)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    df = obs_from_arrays(dn, rec.dn_offset, pix)
    df.insert(1, "scene_id", rec.id)
    df.insert(2, "date", rec.date.isoformat())
    rad = radiometry(dn, _W["park"])
    rad.update(scene_id=rec.id, date=rec.date.isoformat(), dn_offset=rec.dn_offset,
               baseline=rec.processing_baseline, extract_version=EXTRACT_VERSION)
    (out.parent / "radiometry.json").write_text(json.dumps(rad, indent=1))
    tmp = out.with_name(out.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    return rec.id, len(df), time.monotonic() - t0


# --------------------------------------------------------------------------- scenes from the DB
def dsn() -> str:
    from dotenv import load_dotenv

    load_dotenv(aoi.REPO / ".env")
    d = os.environ.get("DATABASE_URL")
    if not d:
        raise SystemExit("DATABASE_URL not set")
    return d


def load_scenes(conn: Any, min_valid: float = MIN_AOI_VALID, epsg: int = aoi.UTM_EPSG) -> list[SceneRecord]:
    rows = conn.execute(
        "SELECT id, datetime, tile, cloud, hrefs, source, baseline, dn_offset, epsg FROM s2_scene "
        "WHERE aoi_valid_frac >= %s AND coalesce(epsg, %s) = %s ORDER BY datetime",
        (min_valid, epsg, epsg)).fetchall()
    recs = []
    for sid, dt, tile, cloud, hrefs, source, baseline, off, ep in rows:
        recs.append(SceneRecord(id=sid, datetime=dt, tile=tile, cloud=cloud, hrefs=dict(hrefs),
                                source=source or "earth-search", processing_baseline=baseline,
                                dn_offset=int(off or 0), epsg=ep))
    return recs


def signed(rec: SceneRecord) -> SceneRecord:
    from planet.stac.inventory import usable_signed

    return usable_signed(rec)


# --------------------------------------------------------------------------- assemble + upsert
def assemble(scene_ids: Sequence[str], cache_dir: Path = ps.CACHE_DIR) -> Any:
    import pandas as pd

    parts = [pd.read_parquet(p) for s in scene_ids if (p := obs_path(s, cache_dir)).exists()]
    if not parts:
        return pd.DataFrame(columns=list(OBS_COLUMNS))
    df = pd.concat(parts, ignore_index=True)
    return df[list(OBS_COLUMNS)].sort_values(["parcel_uid", "date"]).reset_index(drop=True)


def upsert_obs(conn: Any, df: Any) -> int:
    """COPY into a temp table, then INSERT .. ON CONFLICT (parcel_uid, scene_id) DO UPDATE."""
    cols = list(OBS_COLUMNS) + ["extract_version"]
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE _obs (LIKE parcel_obs INCLUDING DEFAULTS) ON COMMIT DROP")
        with conn.cursor().copy(f"COPY _obs ({', '.join(cols)}) FROM STDIN") as cp:
            for r in df.itertuples(index=False):
                vals = [None if (isinstance(v, float) and not np.isfinite(v)) else v for v in r]
                cp.write_row([*vals, EXTRACT_VERSION])
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("parcel_uid", "scene_id"))
        cur = conn.execute(
            f"INSERT INTO parcel_obs ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _obs "
            f"ON CONFLICT (parcel_uid, scene_id) WHERE parcel_uid IS NOT NULL DO UPDATE SET {upd}")
        return cur.rowcount or 0


# --------------------------------------------------------------------------- reporting
def coverage_report(df: Any) -> list[str]:
    import pandas as pd

    from planet.features import seasons as se

    lines = []
    per = df.groupby("parcel_uid").agg(mode=("geom_mode", "first"), n_total=("n_total", "first"))
    lines.append(f"parcels {len(per)}; geom_mode {per['mode'].value_counts().to_dict()}; mixed_pixel "
                 f"{int((per['mode'] != 'buf5').sum())}; structurally < 10 px even unbuffered "
                 f"{int((per.n_total < ps.LOW_SUPPORT_PX).sum())}")
    ok = df[~df.low_support]
    lines.append(f"parcel_obs rows {len(df)}; with n_px >= 10: {len(ok)}; low_support rows {int(df.low_support.sum())}")
    d = pd.to_datetime(df["date"])
    cfg = se.load_config()
    lab = se.assign(d, cfg)
    usable = df.assign(season=lab["season"].to_numpy(), ag_year=lab["ag_year"].to_numpy())
    usable = usable[usable.n_px >= 1]
    usable = usable[(~usable.low_support) | usable.mixed_pixel]
    for s in ("kharif", "rabi", "summer"):
        g = usable[usable.season == s].groupby(["parcel_uid", "ag_year"]).size()
        full = g.unstack(fill_value=0).reindex(per.index, fill_value=0)
        q = full.stack().quantile([0.1, 0.5, 0.9])
        lines.append(f"obs per parcel-season [{s}] (usable, all ag-years pooled): p10 {q[0.1]:.0f} / "
                     f"p50 {q[0.5]:.0f} / p90 {q[0.9]:.0f}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T3.2 per-parcel S2 extraction")
    ap.add_argument("--limit", type=int, default=None, help="process only the N most recent scenes")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--min-valid", type=float, default=MIN_AOI_VALID)
    ap.add_argument("--refresh", action="store_true", help="recompute per-scene obs (cache reused)")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t0 = time.monotonic()
    import psycopg

    with psycopg.connect(dsn()) as conn:
        recs = load_scenes(conn, args.min_valid)
    if args.limit:
        recs = sorted(recs, key=lambda r: r.datetime, reverse=True)[: args.limit]
    grid = aoi.aoi_grid()
    parcels = aoi.load_parcels_uid()
    pix = parcel_pixels(parcels, grid)
    from rasterio.features import rasterize

    park = aoi.load_boundary().to_crs(grid.crs)
    park_mask = rasterize(((g, 1) for g in park.geometry), out_shape=grid.shape, transform=grid.transform,
                          fill=0, dtype="uint8").astype(bool)
    print(f"scenes {len(recs)} (aoi_valid_frac >= {args.min_valid}); grid {grid.key()} {grid.shape}; parcels "
          f"{len(pix.uids)} (mixed_pixel {int(pix.mixed.sum())})", flush=True)
    done = failed = skipped = 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker,
                             initargs=(grid, pix, park_mask, str(ps.CACHE_DIR))) as ex:
        futs = {ex.submit(process_scene, signed(r), args.refresh): r for r in recs}
        for i, f in enumerate(as_completed(futs), 1):
            r = futs[f]
            try:
                _, n, _sec = f.result()
                skipped += n < 0
                done += n >= 0
            except Exception as exc:  # noqa: BLE001 - keep going; rerun resumes
                failed += 1
                log.warning("scene %s failed: %s", r.id, exc)
            if i % 20 == 0 or i == len(recs):
                print(f"  {i}/{len(recs)} scenes (new {done}, cached {skipped}, failed {failed}) "
                      f"{time.monotonic() - t0:.0f} s", flush=True)
    df = assemble([r.id for r in recs])
    if not args.limit:
        tabio.write_table(df, OBS_PARQUET)
    if not args.no_db and len(df):
        import psycopg

        with psycopg.connect(dsn()) as conn:
            n = upsert_obs(conn, df)
            tot = conn.execute("SELECT count(*), count(DISTINCT scene_id) FROM parcel_obs "
                               "WHERE parcel_uid IS NOT NULL").fetchone()
        print(f"parcel_obs upserted {n}; table now {tot[0]} rows over {tot[1]} scenes")
    for line in coverage_report(df):
        print(line)
    print(f"failed scenes {failed}; runtime {time.monotonic() - t0:.0f} s ({time.strftime("%H:%M")})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
