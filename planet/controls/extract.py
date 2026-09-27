"""Control-cell extraction: the same 307 scenes and the same per-pixel code as the parcels (T3.2).

Grid: native 10 m UTM grid (EPSG:32643) over the control cells' bbox; each scene is read once per band
(windowed COG reads) and cached as ``data/s2/<scene>/aoi_<gridhash>.tif`` next to the parcel-grid cache.
Footprints: 80 m cells with the same -5 m inward buffer (``parcel_pixels``). Per-scene output
``data/s2/<scene>/ctrl_obs_<version>.parquet`` is the done marker (resumable, idempotent).

``python -m planet.controls extract [--limit N] [--workers 8] [--no-db]``
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from planet import aoi
from planet.controls import cells as cc
from planet.extract import parcel_stats as ps
from planet.extract import run as er

log = logging.getLogger("planet.controls")
CTRL_DIR = ps.CACHE_DIR / "controls"
CELLS_PARQUET = CTRL_DIR / "cells.parquet"
OBS_PARQUET = CTRL_DIR / "control_obs.parquet"
EXTRACT_VERSION = f"ctrl-{er.EXTRACT_VERSION}"
_W: dict[str, Any] = {}


def build_cells(conn: Any) -> Any:
    ring, core = cc.ring_geometry(conn)
    cand = cc.exclusions(conn, cc.worldcover_fractions(cc.lattice(ring)), core)
    sel = cc.select(cand)
    CTRL_DIR.mkdir(parents=True, exist_ok=True)
    sel.to_parquet(CELLS_PARQUET, index=False)
    (CTRL_DIR / "selection.json").write_text(json.dumps(
        {**sel.attrs, "n_selected": len(sel), "cell_m": cc.CELL_M, "ring_m": cc.RING_M, "block_m": cc.BLOCK_M,
         "min_cropland": cc.MIN_CROPLAND, "version": cc.CELL_VERSION,
         "sectors": sel.sector.value_counts().to_dict()}, indent=1))
    return cc.to_gdf(sel)


def load_cells() -> Any:
    return cc.to_gdf(pd.read_parquet(CELLS_PARQUET))


def upsert_cells(conn: Any, g: Any) -> int:
    from psycopg.types.json import Jsonb

    ll = g.to_crs("EPSG:4326")
    fr = [c for c in g.columns if c.startswith("frac_")]
    rows = [(r.cell_id, geom.wkt, float(r.x0), float(r.y0), r.sector, float(r.dist_km),
             Jsonb({c: (None if pd.isna(getattr(r, c)) else round(float(getattr(r, c)), 4)) for c in fr}), cc.CELL_VERSION)
            for r, geom in zip(g.itertuples(index=False), ll.geometry, strict=True)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO control_cell (cell_id, geom, x0, y0, sector, dist_km, worldcover, cell_version) "
                        "VALUES (%s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, %s, %s) ON CONFLICT (cell_id) DO UPDATE SET "
                        "geom = EXCLUDED.geom, sector = EXCLUDED.sector, dist_km = EXCLUDED.dist_km, "
                        "worldcover = EXCLUDED.worldcover, cell_version = EXCLUDED.cell_version", rows)
    conn.commit()
    return len(rows)


def control_grid(g: Any) -> ps.Grid:
    b = g.to_crs("EPSG:4326").total_bounds
    pad = 0.003
    return ps.native_grid((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad), aoi.UTM_EPSG)


def obs_path(scene_id: str) -> Path:
    return ps.CACHE_DIR / scene_id / f"ctrl_obs_{er.EXTRACT_VERSION}.parquet"


def _init(grid: ps.Grid, pix: Any) -> None:
    logging.basicConfig(level=logging.WARNING)
    _W.update(grid=grid, pix=pix)


def process_scene(rec: Any) -> tuple[str, int]:
    out = obs_path(rec.id)
    if out.exists():
        return rec.id, -1
    for attempt in range(4):
        try:
            dn = ps.read_scene(rec, _W["grid"], bands=ps.CACHE_BANDS, workers=6)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    df = er.obs_from_arrays(dn, rec.dn_offset, _W["pix"])
    df.insert(1, "scene_id", rec.id)
    df.insert(2, "date", rec.date.isoformat())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    return rec.id, len(df)


def extract(conn: Any, g: Any, workers: int = 8, limit: int | None = None) -> pd.DataFrame:
    grid = control_grid(g)
    pix = er.parcel_pixels(g, grid)
    recs = er.load_scenes(conn, er.MIN_AOI_VALID)
    if limit:
        recs = recs[:limit]
    recs = [er.signed(r) for r in recs]
    print(f"control grid {grid.width}x{grid.height} px; cells {len(g)}; px per cell p50 "
          f"{int(pd.Series(pix.n_total).median())}; scenes {len(recs)}", flush=True)
    done = failed = 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(grid, pix)) as ex:
        futs = {ex.submit(process_scene, r): r.id for r in recs}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.warning("scene %s failed: %s", futs[f], exc)
            if (done + failed) % 20 == 0:
                print(f"  {done + failed}/{len(recs)} scenes (failed {failed}) {time.time() - t0:.0f} s", flush=True)
    parts = [pd.read_parquet(obs_path(r.id)) for r in recs if obs_path(r.id).exists()]
    obs = pd.concat(parts, ignore_index=True).rename(columns={"parcel_uid": "cell_id"})
    obs.to_parquet(OBS_PARQUET, index=False)
    print(f"control obs rows {len(obs)} over {obs.scene_id.nunique()} scenes; failed {failed}", flush=True)
    return obs


def upsert_obs(conn: Any, obs: pd.DataFrame) -> int:
    cols = ["cell_id", "scene_id", "date", "ndvi", "ndwi", "bsi", "ndbi", "valid_frac", "n_px", "n_total",
            "low_support", "geom_mode"]
    rows = [(*[None if (isinstance(v, float) and math.isnan(v)) else v for v in r], EXTRACT_VERSION)
            for r in obs[cols].itertuples(index=False, name=None)]
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*cols[2:], "extract_version"])
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO control_obs ({', '.join(cols)}, extract_version) VALUES "
                        f"({', '.join(['%s'] * (len(cols) + 1))}) ON CONFLICT (cell_id, scene_id) DO UPDATE SET {upd}", rows)
    conn.commit()
    return len(rows)
