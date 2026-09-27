"""Incremental Sentinel-2 refresh (``make s2-refresh``): fetch scenes newer than the latest ``s2_scene`` row and
update only what they change. An incremental layer over the existing P3 code; nothing is re-implemented.

``uv run python -m planet.refresh [--max-scenes N] [--dry-run] --json``

stdout contract (consumed by the API job runner; everything else goes to stderr):

    STAGE <inventory|extract|features|classify> <running|done|skipped|failed> <free-text detail>
    ...
    RESULT {"new_scenes": int, "latest_scene_date": "YYYY-MM-DD", "previous_latest_scene_date": "YYYY-MM-DD",
            "parcels_updated": int, "seasons_updated": int, "dry_run": bool}

Exit code 0 on success (including "no new scenes"), 1 when a stage failed (its ``STAGE <name> failed <error>`` line
is printed), 2 when another refresh holds the lock.

Stages
1. **inventory** - Earth Search STAC (Planetary Computer fallback) over the same AOI bbox, collection and
   (absent) cloud filter as ``planet.stac.inventory``, from ``latest scene date - lookback`` (default 10 d, catches
   late-ingested acquisitions) to today. New = acquisitions whose (date, tile) is not in ``s2_scene`` yet; a newer
   processing baseline of a known date is *not* re-ingested (reported only). Oldest first, capped by
   ``--max-scenes``. SCL usability (``inventory.compute_usability``) then ``inventory.upsert``. A scene whose SCL
   read fails is not written, so the next run retries it. ``--dry-run`` stops after the STAC query (no writes, no
   SCL reads) and reports how many new acquisitions exist.
2. **extract** - per-parcel indices (``planet.extract.run``: same grid, -5 m buffer, mixed-pixel fallback) for
   every usable scene (``aoi_valid_frac >= 0.2``) that has no ``parcel_obs`` rows yet, and the same for the
   control cells / ``control_obs`` (``planet.controls.extract``). Per-scene parquet markers make it resumable.
3. **features** - ``planet.features.build`` re-smooths the series of the parcels touched by the new scenes (plus
   their 300 m neighbours, for ``nbr_amp_med_300m``) with the stored Whittaker lambda (no re-tuning). Only the
   windows ending on/after ``first new obs date - HALO_DAYS`` are in scope (Whittaker lambda=1000 has a ~+-25 d
   footprint; the 45 d gap rule and the archive-end ``max_gap_days`` reach back <= 45 d), and of those only rows
   whose feature payload actually changed are upserted. Rows outside the scope that *would* differ are counted
   and reported (diagnostic), never written.
4. **classify** - the existing trained student (``landuse_lgbm_v1``, loaded, not retrained; no teacher/VLM calls)
   re-predicts every season row of the affected parcel x ag-years (the annual context feeds all seasons of a
   year) and the annual aggregate; only rows whose state/p/route/needs_field changed are written. Event windows
   (``planet.events``) are recomputed for parcels whose states changed. The control-relative layer (plough
   signal, z-scores, DiD) is recomputed with the existing full ``planet.controls.relative`` run whenever new
   usable scenes were extracted, because z-scores are relative to all control cells of a season (~1-2 min).

``seasons_updated`` counts parcel-season rows (parcel x ag_year x season, incl. annual) whose features or state
were rewritten; ``parcels_updated`` the distinct parcels among them.

Idempotence / resume: a journal (``data/s2/refresh_state.json``) is written *before* any DB write and records the
batch (scene ids, previous latest date, completed stages, pending state-change parcels). A re-run after a crash
resumes the open batch (and merges any newer scenes into it); all writes are upserts keyed on natural keys, so
nothing is duplicated. A completed batch is appended to ``data/s2/refresh_history.jsonl``.

Outputs are signals needing field verification (clouds/haze after SCL masking, mixed pixels, NE-monsoon weed flush).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("planet.refresh")

STAGES = ("inventory", "extract", "features", "classify")
DEFAULT_LOOKBACK_DAYS = 10
HALO_DAYS = 60
MIN_AOI_VALID = 0.2
FLOAT_TOL = 1e-4
NO_NEW = "no new scenes"


def _cache_dir() -> Path:
    from planet.extract import parcel_stats as ps

    return Path(ps.CACHE_DIR)


# ================================================================================================ output
def emit(stage: str, status: str, detail: str = "") -> None:
    """One protocol line on stdout (single line, flushed)."""
    detail = " ".join(str(detail).split())
    print(f"STAGE {stage} {status} {detail}".rstrip(), file=sys.__stdout__, flush=True)


def emit_result(res: dict[str, Any]) -> None:
    print("RESULT " + json.dumps(res), file=sys.__stdout__, flush=True)


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


# ================================================================================================ pure helpers
def select_new(recs: Iterable[Any], known_ids: set[str], known_keys: set[tuple[str, str]], epsg: int
               ) -> tuple[list[Any], list[str], list[str]]:
    """Split STAC records into (new, reprocessed_known_dates, other_crs), new sorted oldest first.

    ``known_keys`` = {(iso date, tile)} already in ``s2_scene``. A different id for a known (date, tile) is a newer
    processing baseline of an acquisition we already have: it is reported, not ingested (ingesting it would
    double-count the date while parcel_obs still references the old baseline)."""
    new, repro, other = [], [], []
    for r in sorted(recs, key=lambda x: x.datetime):
        if r.id in known_ids:
            continue
        if (r.date.isoformat(), r.tile) in known_keys:
            repro.append(r.id)
            continue
        if (r.epsg or epsg) != epsg:
            other.append(r.id)
            continue
        new.append(r)
    return new, repro, other


def scope_cutoff(affected_from: dt.date, halo_days: int = HALO_DAYS) -> dt.date:
    return affected_from - dt.timedelta(days=halo_days)


def window_end(ag_year: int, season: str, cfg: Any, _cache: dict | None = None) -> dt.date:
    from planet.features import seasons as se

    key = (int(ag_year), season)
    if _cache is not None and key in _cache:
        return _cache[key]
    end = se.window(int(ag_year), season, cfg)[1].date()
    if _cache is not None:
        _cache[key] = end
    return end


def in_scope(df: Any, affected_from: dt.date, cfg: Any, halo_days: int = HALO_DAYS) -> Any:
    """Boolean Series: window (ag_year, season) ends on/after ``affected_from - halo_days``."""
    import pandas as pd

    cut = scope_cutoff(affected_from, halo_days)
    cache: dict = {}
    return pd.Series([window_end(y, s, cfg, cache) >= cut for y, s in zip(df.ag_year, df.season, strict=True)],
                     index=df.index, dtype=bool)


def affected_years(pairs: Iterable[tuple[int, str]], affected_from: dt.date, cfg: Any,
                   halo_days: int = HALO_DAYS) -> set[int]:
    cut = scope_cutoff(affected_from, halo_days)
    cache: dict = {}
    return {int(y) for y, s in pairs if window_end(y, s, cfg, cache) >= cut}


def feature_payload(row: dict[str, Any]) -> dict[str, Any]:
    """The JSON payload ``planet.features.build.upsert_features`` stores (without ``_meta``)."""
    from planet.features import build as fb

    return {k: fb._json_safe(row[k]) for k in fb.FEATURE_KEYS if k in row}


def _norm_caveats(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    if v is not None and not isinstance(v, list):
        return list(v)
    return v


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) <= FLOAT_TOL
    return a == b


def payload_changed(new: dict[str, Any], old: dict[str, Any] | None) -> bool:
    if old is None:
        return True
    old = {k: v for k, v in old.items() if k != "_meta"}
    keys = set(new) | set(old)
    for k in keys:
        a, b = new.get(k), old.get(k)
        if k == "caveats":
            if _norm_caveats(a) != _norm_caveats(b):
                return True
        elif not _same(a, b):
            return True
    return False


def state_changed(new: dict[str, Any], old: dict[str, Any] | None) -> bool:
    """Compare the classification outcome (state, p_state to 3 dp, route, needs_field)."""
    import math

    if old is None:
        return True
    if new.get("state") != old.get("state") or new.get("route") != old.get("route"):
        return True
    if bool(new.get("needs_field")) != bool(old.get("needs_field")):
        return True
    a, b = new.get("p_state"), old.get("p_state")
    a = None if a is None or (isinstance(a, float) and math.isnan(a)) else round(float(a), 3)
    b = None if b is None or (isinstance(b, float) and math.isnan(b)) else round(float(b), 3)
    return a != b if (a is None or b is None) else abs(a - b) > 1.5e-3


def row_key(uid: str, ag_year: Any, season: str) -> str:
    return f"{uid}#{int(ag_year)}#{season}"


def merge_snapshot(old: Any, new: Any, keys: Sequence[str], drop_mask: Any | None = None) -> Any:
    """Replace rows of ``old`` whose ``keys`` appear in ``new`` (or where ``drop_mask`` is true) by ``new``."""
    import pandas as pd

    if old is None or len(old) == 0:
        return new.reset_index(drop=True)
    if drop_mask is None:
        k_new = pd.MultiIndex.from_frame(new[list(keys)].astype(str)) if len(new) else pd.MultiIndex.from_tuples([])
        k_old = pd.MultiIndex.from_frame(old[list(keys)].astype(str))
        drop_mask = k_old.isin(k_new)
    kept = old[~pd.Series(drop_mask, index=old.index).astype(bool)]
    return pd.concat([kept, new], ignore_index=True) if len(new) else kept.reset_index(drop=True)


# ================================================================================================ journal
class Journal:
    """Crash-safe batch record (atomic JSON writes)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, Any] | None = None
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except ValueError:
                log.warning("unreadable journal %s; starting a new batch", self.path)

    @property
    def open(self) -> bool:
        return self.data is not None

    def start(self, previous_latest: dt.date | None) -> None:
        self.data = {"version": 1, "started_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                     "previous_latest": _iso(previous_latest), "scenes": [], "inventoried": [],
                     "extracted": {}, "controls_extracted": [], "stages_done": [],
                     "features_keys": [], "state_keys": [], "pending_state_parcels": []}

    def get(self, k: str, default: Any = None) -> Any:
        return (self.data or {}).get(k, default)

    def set(self, **kw: Any) -> None:
        assert self.data is not None
        self.data.update(kw)
        self.save()

    def add(self, key: str, values: Iterable[Any]) -> None:
        assert self.data is not None
        cur = list(self.data.get(key, []))
        seen = set(cur)
        cur.extend(v for v in values if v not in seen and not seen.add(v))
        self.data[key] = cur
        self.save()

    def stage_done(self, stage: str) -> None:
        self.add("stages_done", [stage])

    def is_done(self, stage: str) -> bool:
        return stage in self.get("stages_done", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, default=str))
        os.replace(tmp, self.path)

    def close(self, result: dict[str, Any]) -> None:
        if self.data is None:
            return
        rec = {**{k: v for k, v in self.data.items() if k not in ("features_keys", "state_keys")},
               "finished_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "result": result,
               "n_features_keys": len(self.get("features_keys", [])), "n_state_keys": len(self.get("state_keys", []))}
        hist = self.path.with_name("refresh_history.jsonl")
        with hist.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.path.unlink(missing_ok=True)
        self.data = None


# ================================================================================================ real I/O
class Ops:
    """Real implementation over PostGIS + the public STAC. Tests substitute a fake with the same methods."""

    def __init__(self, workers: int = 4, source: str = "auto"):
        self.workers = workers
        self.source = source
        self._conn = None

    # ---- db
    def conn(self) -> Any:
        if self._conn is None or self._conn.closed:
            import psycopg

            from planet.extract.run import dsn

            # autocommit: library code uses conn.transaction() blocks, which must be real transactions
            self._conn = psycopg.connect(dsn(), autocommit=True)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def latest_scene_date(self) -> dt.date | None:
        v = self.conn().execute("SELECT max(datetime) FROM s2_scene").fetchone()[0]
        return v.astimezone(dt.UTC).date() if v else None

    def known(self, since: dt.date) -> tuple[set[str], set[tuple[str, str]]]:
        rows = self.conn().execute(
            "SELECT id, datetime, tile, duplicates FROM s2_scene WHERE datetime >= %s",
            (dt.datetime.combine(since - dt.timedelta(days=1), dt.time(), dt.UTC),)).fetchall()
        ids: set[str] = set()
        keys: set[tuple[str, str]] = set()
        for sid, t, tile, dups in rows:
            ids.add(sid)
            ids.update(dups or [])
            keys.add((t.astimezone(dt.UTC).date().isoformat(), tile))
        return ids, keys

    # ---- inventory
    def search(self, start: dt.date, end: dt.date) -> list[Any]:
        from planet.stac import inventory as inv

        return inv.collect(self.source, end=end, start=start)

    def usability(self, recs: Sequence[Any]) -> tuple[dict[str, dict], Any]:
        from planet.stac import inventory as inv

        with contextlib.redirect_stdout(sys.stderr):
            return inv.compute_usability(list(recs), self.workers, None)

    def upsert_scenes(self, recs: Sequence[Any], usable: dict[str, dict], mat: Any) -> int:
        import pandas as pd

        from planet import tabio
        from planet.stac import inventory as inv

        rows = [inv.scene_row(r, usable[r.id]) for r in recs if r.id in usable]
        if not rows:
            return 0
        res = inv.upsert(self.conn(), rows)
        # snapshots: append (replace by id) so the P1 artefacts stay complete
        if inv.INVENTORY_PARQUET.exists():
            old = tabio.read_table(inv.INVENTORY_PARQUET)
            new = pd.DataFrame([{**{k: v for k, v in r.items() if k not in ("hrefs", "meta", "collapsed_ids")},
                                 "hrefs": json.dumps(r["hrefs"]), "collapsed_ids": json.dumps(r["collapsed_ids"]),
                                 "meta": json.dumps(r["meta"], default=str)} for r in rows])
            tabio.write_table(merge_snapshot(old, new, ["id"]), inv.INVENTORY_PARQUET)
        if mat is not None and inv.PARCEL_VALID_PARQUET.exists():
            old = tabio.read_table(inv.PARCEL_VALID_PARQUET)
            new = mat[mat.scene_id.isin([r["id"] for r in rows])]
            tabio.write_table(merge_snapshot(old, new, ["parcel_uid", "scene_id"]), inv.PARCEL_VALID_PARQUET)
        return int(res["upserted"])

    # ---- extract
    def _scenes_without(self, table: str, key: str = "scene_id") -> list[Any]:
        from planet.extract import run as er

        conn = self.conn()
        recs = er.load_scenes(conn, MIN_AOI_VALID)
        where = " WHERE parcel_uid IS NOT NULL" if table == "parcel_obs" else ""
        have = {r[0] for r in conn.execute(f"SELECT DISTINCT {key} FROM {table}{where}").fetchall()}
        return [r for r in recs if r.id not in have]

    def pending_extract(self) -> list[Any]:
        return self._scenes_without("parcel_obs")

    def pending_controls(self) -> list[Any]:
        from planet.controls import extract as cx

        if not cx.CELLS_PARQUET.exists():
            return []
        return self._scenes_without("control_obs")

    @staticmethod
    def _pool_run(fn: Callable, init: Callable, initargs: tuple, recs: Sequence[Any], workers: int
                  ) -> tuple[list[str], dict[str, str]]:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        from planet.extract import run as er

        ok, failed = [], {}
        with ProcessPoolExecutor(max_workers=max(1, min(workers, len(recs))), initializer=init,
                                 initargs=initargs) as ex:
            futs = {ex.submit(fn, er.signed(r)): r.id for r in recs}
            for f in as_completed(futs):
                try:
                    f.result()
                    ok.append(futs[f])
                except Exception as exc:  # noqa: BLE001 - scene stays pending; the rerun retries it
                    failed[futs[f]] = f"{type(exc).__name__}: {exc}"
        return ok, failed

    def extract(self, recs: Sequence[Any]) -> dict[str, Any]:
        from rasterio.features import rasterize

        from planet import aoi, tabio
        from planet.extract import parcel_stats as ps
        from planet.extract import run as er

        grid = aoi.aoi_grid()
        pix = er.parcel_pixels(aoi.load_parcels_uid(), grid)
        park = aoi.load_boundary().to_crs(grid.crs)
        park_mask = rasterize(((g, 1) for g in park.geometry), out_shape=grid.shape, transform=grid.transform,
                              fill=0, dtype="uint8").astype(bool)
        ok, failed = self._pool_run(er.process_scene, er._init_worker, (grid, pix, park_mask, str(ps.CACHE_DIR)),
                                    recs, self.workers)
        df = er.assemble(ok)
        n = er.upsert_obs(self.conn(), df) if len(df) else 0
        if len(df) and er.OBS_PARQUET.exists():
            old = tabio.read_table(er.OBS_PARQUET)
            m = merge_snapshot(old, df, ["parcel_uid", "scene_id"], drop_mask=old.scene_id.isin(ok))
            tabio.write_table(m.sort_values(["parcel_uid", "date"]).reset_index(drop=True), er.OBS_PARQUET)
        dates = {r.id: r.date.isoformat() for r in recs if r.id in ok}
        return {"ok": ok, "failed": failed, "rows": n, "dates": dates,
                "dn_offset_flagged": [r.id for r in recs if r.id in ok and r.dn_offset]}

    def extract_controls(self, recs: Sequence[Any]) -> dict[str, Any]:
        import pandas as pd

        from planet.controls import extract as cx
        from planet.extract import run as er

        if not recs:
            return {"ok": [], "failed": {}, "rows": 0}
        g = cx.load_cells()
        grid = cx.control_grid(g)
        pix = er.parcel_pixels(g, grid)
        ok, failed = self._pool_run(cx.process_scene, cx._init, (grid, pix), recs, self.workers)
        parts = [pd.read_parquet(cx.obs_path(s)) for s in ok if cx.obs_path(s).exists()]
        n = 0
        if parts:
            obs = pd.concat(parts, ignore_index=True).rename(columns={"parcel_uid": "cell_id"})
            n = cx.upsert_obs(self.conn(), obs)
            if cx.OBS_PARQUET.exists():
                old = pd.read_parquet(cx.OBS_PARQUET)
                merge_snapshot(old, obs, ["cell_id", "scene_id"], drop_mask=old.scene_id.isin(ok)).to_parquet(
                    cx.OBS_PARQUET, index=False)
        return {"ok": ok, "failed": failed, "rows": n}

    # ---- features
    @staticmethod
    def _lambda(cfg: Any) -> float:
        from planet.features import build as fb

        v = cfg.get("smoothing", "lambda", default="auto")
        if str(v) != "auto":
            return float(v)
        if not fb.LAMBDA_JSON.exists():
            raise RuntimeError(f"{fb.LAMBDA_JSON} missing: run `make s2-features` once (lambda is not re-tuned here)")
        return float(json.loads(fb.LAMBDA_JSON.read_text())["lambda"])

    def _db_features(self) -> dict[str, dict]:
        rows = self.conn().execute("SELECT parcel_uid, ag_year, season, features FROM parcel_season "
                                   "WHERE parcel_uid IS NOT NULL").fetchall()
        return {row_key(u, y, s): f for u, y, s, f in rows}

    def features(self, affected_from: dt.date, scene_ids: Sequence[str]) -> dict[str, Any]:
        import pandas as pd

        from planet import aoi, tabio
        from planet.features import build as fb
        from planet.features import seasons as se

        cfg = se.load_config()
        lam = self._lambda(cfg)
        obs = fb.load_obs(False)
        parcels = aoi.load_parcels_uid()
        touched = set(obs.loc[obs.scene_id.isin(list(scene_ids)), "parcel_uid"])
        if not touched:
            return {"touched": [], "written_keys": [], "rows_in_scope": 0, "outside_changed": 0, "lambda": lam}
        nbrs = fb.neighbour_pairs(parcels, float(cfg.get("neighbour_radius_m", default=300)))
        build_set = set(touched)
        for u in touched:
            build_set.update(nbrs.get(u, []))
        with contextlib.redirect_stdout(sys.stderr):
            df, ser, info = fb.build(obs[obs.parcel_uid.isin(build_set)], cfg, lam, parcels)
        df = df[df.parcel_uid.isin(touched)].reset_index(drop=True)
        scope = in_scope(df, affected_from, cfg)
        old = self._db_features()
        recs = df.to_dict("records")
        changed = pd.Series([payload_changed(feature_payload(r), old.get(row_key(r["parcel_uid"], r["ag_year"],
                                                                                 r["season"]))) for r in recs],
                            index=df.index, dtype=bool)
        write = df[scope & changed]
        meta = {"features_version": fb.FEATURES_VERSION, "seasons": cfg.version, "lambda": lam,
                "refresh": {"affected_from": affected_from.isoformat(), "halo_days": HALO_DAYS}}
        n = fb.upsert_features(self.conn(), write, meta) if len(write) else 0
        # snapshots used by student/controls/app: replace in-scope rows of the touched parcels
        cut = pd.Timestamp(scope_cutoff(affected_from))
        new_rows = df[scope].assign(caveats=df[scope].caveats.map(json.dumps))
        if fb.FEATURES_PARQUET.exists():
            tabio.write_table(merge_snapshot(tabio.read_table(fb.FEATURES_PARQUET), new_rows,
                                             ["parcel_uid", "ag_year", "season"]), fb.FEATURES_PARQUET)
        if len(ser) and fb.SMOOTHED_PARQUET.exists():
            old_s = tabio.read_table(fb.SMOOTHED_PARQUET)
            ser_t = ser[ser.parcel_uid.isin(touched)]
            ser_t = ser_t[pd.to_datetime(ser_t.date) >= cut]
            drop = old_s.parcel_uid.isin(touched).to_numpy() & (pd.to_datetime(old_s.date) >= cut).to_numpy()
            m = merge_snapshot(old_s, ser_t, ["parcel_uid", "date"], drop_mask=drop)
            tabio.write_table(m.sort_values(["parcel_uid", "date"]).reset_index(drop=True), fb.SMOOTHED_PARQUET)
        return {"touched": sorted(touched), "built": len(build_set), "rows_in_scope": int(scope.sum()),
                "written_keys": [row_key(r.parcel_uid, r.ag_year, r.season) for r in write.itertuples()],
                "rows_written": n, "outside_changed": int((~scope & changed).sum()), "lambda": lam,
                "archive": info.get("archive")}

    # ---- classify
    def predict_states(self, parcels: Sequence[str], affected_from: dt.date) -> dict[str, Any]:
        import pandas as pd

        from planet import tabio
        from planet.classify import student as st
        from planet.extract.worldcover import OUT_PATH as WC_PATH
        from planet.features import build as fb
        from planet.features import seasons as se

        cfg = se.load_config()
        feats = tabio.read_table(fb.FEATURES_PARQUET)
        feats = feats[feats.parcel_uid.isin(list(parcels))]
        years = affected_years(zip(feats.ag_year, feats.season, strict=True), affected_from, cfg)
        feats = feats[feats.ag_year.isin(years)]
        wc = pd.read_parquet(WC_PATH) if WC_PATH.exists() else None
        mdl, cal = st.load_model()
        pred = st.predict_frame(st.feature_frame(feats, wc), mdl, cal)
        seasons = st.route(pred, st.second_opinions(self.conn()))
        rows = pd.concat([seasons, st.annual_state(seasons)], ignore_index=True)
        old = {row_key(u, y, s): {"state": a, "p_state": p, "route": r, "needs_field": nf}
               for u, y, s, a, p, r, nf in self.conn().execute(
                   "SELECT parcel_uid, ag_year, season, state, p_state, route, needs_field FROM parcel_season "
                   "WHERE parcel_uid = ANY(%s) AND ag_year = ANY(%s)", (list(parcels), sorted(years))).fetchall()}
        recs = rows.to_dict("records")
        ch = [state_changed(r, old.get(row_key(r["parcel_uid"], r["ag_year"], r["season"]))) for r in recs]
        changed = rows[pd.Series(ch, index=rows.index, dtype=bool)]
        return {"rows": rows, "changed": changed, "years": sorted(years), "model": st.MODEL_VERSION}

    def write_states(self, rows: Any, changed: Any) -> int:
        from planet.classify import student as st

        n = st.write_states(self.conn(), changed) if len(changed) else 0
        if st.PRED_PARQUET.exists():
            import pandas as pd

            old = pd.read_parquet(st.PRED_PARQUET)
            new = rows.drop(columns=["probs", "second_opinion", "decided_by"], errors="ignore")
            merge_snapshot(old, new, ["parcel_uid", "ag_year", "season"]).to_parquet(st.PRED_PARQUET, index=False)
        return n

    def events(self, parcels: Sequence[str]) -> int:
        import pandas as pd

        from planet.events import windows as ew

        if not parcels:
            return 0
        fbk = ew.load_fallbacks()
        states = ew.load_states(self.conn(), False)
        states = states[states.parcel_uid.isin(list(parcels))]
        df = ew.compute(states, fbk)
        n = ew.upsert(self.conn(), df, fbk.get("version", "events"))
        if ew.EVENTS_PARQUET.exists():
            snap = df.drop(columns=["seasons"]).assign(
                date_basis=df.date_basis.map(json.dumps), state_counts=df.state_counts.map(json.dumps),
                state_share=df.state_share.map(json.dumps), caveats=df.caveats.map(json.dumps))
            old = pd.read_parquet(ew.EVENTS_PARQUET)
            merge_snapshot(old, snap, ["parcel_uid", "window_name"],
                           drop_mask=old.parcel_uid.isin(list(parcels))).to_parquet(ew.EVENTS_PARQUET, index=False)
        return n

    def controls_did(self) -> dict[str, Any]:
        from planet.controls import extract as cx
        from planet.controls import relative as rel

        if not cx.OBS_PARQUET.exists():
            return {"skipped": "no control obs snapshot"}
        with contextlib.redirect_stdout(sys.stderr):
            rel.run_features(self.conn())
            did = rel.run_did(self.conn())
        return {"did_rows": len(did)}


# ================================================================================================ orchestration
class StageError(RuntimeError):
    def __init__(self, stage: str, msg: str):
        super().__init__(msg)
        self.stage = stage


def _dates_summary(recs: Sequence[Any]) -> str:
    if not recs:
        return ""
    parts = [f"{r.date.isoformat()} cloud {r.cloud:.0f}%" if r.cloud is not None else r.date.isoformat()
             for r in recs[:6]]
    more = f" (+{len(recs) - 6} more)" if len(recs) > 6 else ""
    return "; ".join(parts) + more


def run(args: argparse.Namespace, ops: Any, state_path: Path, today: dt.date | None = None) -> int:
    today = today or dt.datetime.now(dt.UTC).date()
    journal = Journal(state_path)
    latest0 = ops.latest_scene_date()
    previous = (dt.date.fromisoformat(journal.get("previous_latest")) if journal.open and journal.get("previous_latest")
                else latest0)
    feat_keys: set[str] = set(journal.get("features_keys", []))
    state_keys: set[str] = set(journal.get("state_keys", []))
    result = {"new_scenes": 0, "latest_scene_date": _iso(latest0), "previous_latest_scene_date": _iso(previous),
              "parcels_updated": 0, "seasons_updated": 0, "dry_run": bool(args.dry_run)}
    stage = "inventory"
    try:
        # ------------------------------------------------------------------ inventory
        since = (latest0 - dt.timedelta(days=args.lookback_days)) if latest0 else dt.date(2019, 1, 1)
        emit(stage, "running", f"STAC search {since} .. {today} (latest scene {_iso(latest0)}, "
                               f"lookback {args.lookback_days} d)" + (" [resuming open batch]" if journal.open else ""))
        t0 = time.monotonic()
        from planet import aoi

        recs = ops.search(since, today + dt.timedelta(days=1))
        known_ids, known_keys = ops.known(since)
        new_all, repro, other = select_new(recs, known_ids, known_keys, aoi.UTM_EPSG)
        new = new_all[: args.max_scenes] if args.max_scenes else new_all
        extra = []
        if repro:
            extra.append(f"{len(repro)} reprocessed baseline(s) of known dates ignored")
        if other:
            extra.append(f"{len(other)} scene(s) in another UTM zone ignored")
        if args.max_scenes and len(new_all) > len(new):
            extra.append(f"capped at --max-scenes {args.max_scenes} of {len(new_all)} (oldest first; rerun for the rest)")
        tail = ("; " + "; ".join(extra)) if extra else ""
        if args.dry_run:
            latest = max([d for d in [latest0, *(r.date for r in new_all)] if d], default=None)
            emit(stage, "done", f"dry run: {len(new_all)} new acquisition(s) after {_iso(latest0)} in {len(recs)} "
                                f"STAC items {since}..{today}: {_dates_summary(new_all) or 'none'}{tail}; nothing written "
                                f"(usability is scored from SCL only in a real run) ({time.monotonic() - t0:.0f} s)")
            detail = NO_NEW if not new_all else f"dry run: would process {len(new)} new scene(s)"
            if journal.open:
                detail += f"; an interrupted batch is open ({len(journal.get('scenes', []))} scenes) and would resume"
            for s in STAGES[1:]:
                emit(s, "skipped", detail)
            result.update(new_scenes=len(new_all), latest_scene_date=_iso(latest))
            emit_result(result)
            return 0
        n_new_usable = 0
        if new:
            if not journal.open:
                journal.start(previous)
            journal.add("scenes", [r.id for r in new])  # journal first, then the DB write
            for s in STAGES[1:]:
                if journal.is_done(s):
                    journal.data["stages_done"].remove(s)
            journal.save()
            usable, mat = ops.usability(new)
            written = ops.upsert_scenes(new, usable, mat)
            journal.add("inventoried", [r.id for r in new if r.id in usable])
            n_new_usable = sum(1 for r in new if (usable.get(r.id) or {}).get("aoi_valid_frac", 0) >= MIN_AOI_VALID)
            failed = [r.id for r in new if r.id not in usable]
            if failed:
                raise StageError(stage, f"SCL read failed for {len(failed)} scene(s) {failed[:3]} (not written; "
                                        f"{written} written; rerun retries)")
            emit(stage, "done", f"{written} new scene(s) after {_iso(latest0)} written to s2_scene "
                                f"({n_new_usable} usable, aoi_valid_frac >= {MIN_AOI_VALID}): {_dates_summary(new)}"
                                f"{tail} ({time.monotonic() - t0:.0f} s)")
        else:
            emit(stage, "done", f"0 new scenes after {_iso(latest0)} ({len(recs)} STAC items {since}..{today}){tail}"
                                + (" [resuming open batch]" if journal.open else "") + f" ({time.monotonic() - t0:.0f} s)")

        # ------------------------------------------------------------------ extract
        stage = "extract"
        pending = ops.pending_extract()
        pending_c = ops.pending_controls()
        if not journal.open and not pending and not pending_c:
            for s in STAGES[1:]:
                emit(s, "skipped", NO_NEW)
            result["latest_scene_date"] = _iso(ops.latest_scene_date())
            emit_result(result)
            return 0
        if not journal.open:  # backlog in the DB without a journal (e.g. manual inventory run)
            journal.start(previous)
            journal.save()
        if pending or pending_c:
            emit(stage, "running", f"{len(pending)} usable scene(s) without parcel_obs, {len(pending_c)} without "
                                   f"control_obs: {_dates_summary(pending)}")
            t0 = time.monotonic()
            r1 = ops.extract(pending) if pending else {"ok": [], "failed": {}, "rows": 0, "dates": {}}
            journal.set(extracted={**journal.get("extracted", {}), **r1.get("dates", {})})
            r2 = ops.extract_controls(pending_c) if pending_c else {"ok": [], "failed": {}, "rows": 0}
            journal.add("controls_extracted", r2["ok"])
            fails = {**r1["failed"], **r2["failed"]}
            if fails:
                k, v = next(iter(fails.items()))
                raise StageError(stage, f"{len(fails)} scene extraction(s) failed, e.g. {k}: {v} "
                                        f"({len(r1['ok'])} parcel scenes / {len(r2['ok'])} control scenes done; rerun resumes)")
            flag = r1.get("dn_offset_flagged") or []
            note = (f"; {len(flag)} scene(s) flagged dn_offset=1000 (unverified; run planet.extract.dn_check)"
                    if flag else "")
            journal.stage_done(stage)
            emit(stage, "done", f"{len(r1['ok'])} scene(s) -> {r1['rows']} parcel_obs rows; {len(r2['ok'])} scene(s) -> "
                                f"{r2['rows']} control_obs rows{note} ({time.monotonic() - t0:.0f} s)")
        elif not journal.get("extracted"):
            n = len(journal.get("scenes", []))
            for s in STAGES[1:]:
                emit(s, "skipped", f"no new usable scenes ({n} new scene(s), all aoi_valid_frac < {MIN_AOI_VALID})")
            return _finish(journal, ops, result, feat_keys, state_keys)
        else:
            journal.stage_done(stage)
            emit(stage, "done", f"nothing pending ({len(journal.get('extracted', {}))} scene(s) already extracted "
                                f"in this batch)")

        extracted = journal.get("extracted", {})
        if not extracted:
            emit("features", "skipped", "no new parcel observations (only control scenes were pending)")
            emit("classify", "skipped", "no new parcel observations")
            return _finish(journal, ops, result, feat_keys, state_keys)
        affected_from = min(dt.date.fromisoformat(d) for d in extracted.values())

        # ------------------------------------------------------------------ features
        stage = "features"
        if journal.is_done(stage):
            emit(stage, "skipped", "already completed in this batch (resumed)")
        else:
            emit(stage, "running", f"re-smoothing parcels touched by {len(extracted)} scene(s); scope = windows "
                                   f"ending >= {scope_cutoff(affected_from)} (first new obs {affected_from} - {HALO_DAYS} d)")
            t0 = time.monotonic()
            fr = ops.features(affected_from, list(extracted))
            feat_keys.update(fr["written_keys"])
            journal.set(features_keys=sorted(feat_keys), touched=fr["touched"])
            journal.stage_done(stage)
            emit(stage, "done", f"{len(fr['touched'])} parcels re-smoothed (lambda {fr['lambda']:g}, archive "
                                f"{fr.get('archive')}); {fr['rows_in_scope']} parcel-season rows in scope, "
                                f"{len(fr['written_keys'])} changed and upserted; out-of-scope rows that would differ: "
                                f"{fr['outside_changed']} (not written) ({time.monotonic() - t0:.0f} s)")

        # ------------------------------------------------------------------ classify
        stage = "classify"
        touched = journal.get("touched", [])
        emit(stage, "running", f"existing student on {len(touched)} parcels (no retrain, no VLM calls)")
        t0 = time.monotonic()
        pr = ops.predict_states(touched, affected_from)
        chg = pr["changed"]
        ch_parcels = sorted(set(chg.parcel_uid)) if len(chg) else []
        journal.add("pending_state_parcels", ch_parcels)  # before the write: events must follow after a crash
        n_w = ops.write_states(pr["rows"], chg)
        state_keys.update(row_key(r.parcel_uid, r.ag_year, r.season) for r in chg.itertuples())
        journal.set(state_keys=sorted(state_keys))
        ev_parcels = journal.get("pending_state_parcels", [])
        n_ev = ops.events(ev_parcels)
        journal.set(pending_state_parcels=[])
        did_note = "control-relative/DiD: not recomputed (no new control observations)"
        if journal.get("controls_extracted") or journal.get("extracted"):
            t1 = time.monotonic()
            d = ops.controls_did()
            did_note = (f"control-relative + DiD fully recomputed ({d['did_rows']} parcel_did rows, "
                        f"{time.monotonic() - t1:.0f} s)" if "did_rows" in d else f"DiD skipped: {d.get('skipped')}")
        journal.stage_done(stage)
        emit(stage, "done", f"{len(pr['rows'])} parcel-season rows re-predicted ({pr['model']}, ag_years "
                            f"{pr['years']}); {n_w} changed and written; event windows {n_ev} rows for "
                            f"{len(ev_parcels)} parcels; {did_note} ({time.monotonic() - t0:.0f} s)")
        return _finish(journal, ops, result, feat_keys, state_keys)
    except Exception as exc:  # protocol: report the failing stage, exit non-zero
        log.exception("refresh failed in stage %s", stage)
        st_name = exc.stage if isinstance(exc, StageError) else stage
        emit(st_name, "failed", f"{type(exc).__name__}: {exc}" if not isinstance(exc, StageError) else str(exc))
        return 1


def _finish(journal: Journal, ops: Any, result: dict[str, Any], feat_keys: set[str], state_keys: set[str]) -> int:
    keys = feat_keys | state_keys
    result.update(new_scenes=len(journal.get("scenes", [])) if journal.open else 0,
                  latest_scene_date=_iso(ops.latest_scene_date()),
                  parcels_updated=len({k.split("#")[0] for k in keys}), seasons_updated=len(keys))
    journal.close(result)
    emit_result(result)
    return 0


def main(argv: Sequence[str] | None = None, ops: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.refresh", description=__doc__.splitlines()[0])
    ap.add_argument("--max-scenes", type=int, default=None, help="process at most N new scenes (oldest first)")
    ap.add_argument("--dry-run", action="store_true", help="query STAC and report; write nothing")
    ap.add_argument("--json", action="store_true", help="machine output (STAGE lines + final RESULT JSON line)")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help="re-query this many days before the latest scene (late-ingested acquisitions)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--source", default="auto", choices=["auto", "earth-search", "planetary-computer"])
    ap.add_argument("--state-file", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.max_scenes is not None and args.max_scenes < 1:
        ap.error("--max-scenes must be >= 1")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state_path = args.state_file or (_cache_dir() / "refresh_state.json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    own = ops is None
    ops = ops or Ops(workers=args.workers, source=args.source)
    t0 = time.monotonic()
    lock_f = state_path.with_name(state_path.name + ".lock").open("w")
    try:
        if not args.dry_run:
            try:
                fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                emit("inventory", "failed", "another satellite refresh is running (lock held)")
                return 2
        with contextlib.redirect_stdout(sys.stderr):  # library prints never reach the protocol stream
            rc = run(args, ops, state_path)
        if not args.json:
            print(f"refresh finished rc={rc} in {time.monotonic() - t0:.0f} s", file=sys.stderr)
        return rc
    finally:
        lock_f.close()
        if own:
            ops.close()


if __name__ == "__main__":
    sys.exit(main())
