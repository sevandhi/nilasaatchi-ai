"""T1.5 orchestration: fetch/mosaic/clip DEM+slope, JRC water, GloFAS RP100; zonal stats for
every FMB parcel; write data/raster/zonal_stats.parquet and update parcel.raster_stats (if the
`parcel` table -- gis-engineer's -- is present and loaded)."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from psycopg.types.json import Jsonb

from ..catalog.dbutil import get_conn, table_exists
from . import dem, glofas, jrc
from .aoi import BUFFER_M, aoi_bounds, load_fmb_parcels
from .fetch import FetchError
from .zonal import elevation_slope_stats, load_worldcover_lc, water_flood_stats

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "raster"
ZONAL_PARQUET = OUT_DIR / "zonal_stats.parquet"

UTM_CRS = "EPSG:32644"
WGS84 = "EPSG:4326"


def _parcel_uid(row) -> str:
    return f"{row['vil_name']}|{row['KIDE']}"


def _json_safe(value):
    """Recursively replace NaN/NaT (from parquet/pandas) with None -- Postgres rejects the bare
    `NaN` token JSON emits for float('nan')."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def run(limit: int | None = None) -> dict:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bounds = aoi_bounds(BUFFER_M)

    sources: dict[str, dict] = {}
    skipped: dict[str, str] = {}

    try:
        sources["dem"] = dem.build_dem_and_slope(bounds, OUT_DIR)
    except FetchError as exc:
        skipped["dem_slope"] = str(exc)
    try:
        sources["jrc"] = jrc.build_water_occurrence(bounds, OUT_DIR)
    except FetchError as exc:
        skipped["jrc_water"] = str(exc)
    try:
        sources["glofas"] = glofas.build_flood_rp100(bounds, OUT_DIR)
    except FetchError as exc:
        skipped["glofas_rp100"] = str(exc)

    parcels = load_fmb_parcels()
    if limit:
        parcels = parcels.iloc[:limit]
    parcels = parcels.copy()
    parcels["parcel_uid"] = parcels.apply(_parcel_uid, axis=1)

    n = len(parcels)
    elev_slope_rows = [{} for _ in range(n)]
    water_flood_rows = [{} for _ in range(n)]

    if "dem" in sources:
        geoms_utm = parcels.to_crs(UTM_CRS).geometry.tolist()
        elev_slope_rows = elevation_slope_stats(sources["dem"]["dem_utm_cog"],
                                                 sources["dem"]["slope_cog"], geoms_utm)
    if "jrc" in sources:
        geoms_wgs84 = parcels.to_crs(WGS84).geometry.tolist()
        flood_path = sources.get("glofas", {}).get("flood_rp100_cog")
        water_flood_rows = water_flood_stats(sources["jrc"]["water_occurrence_cog"], flood_path,
                                              geoms_wgs84)

    lc_by_uid = load_worldcover_lc(parcels["parcel_uid"].tolist())

    records = []
    for i, (_, prow) in enumerate(parcels.iterrows()):
        uid = prow["parcel_uid"]
        rec = {"parcel_uid": uid, "village": prow["vil_name"], "kide": prow["KIDE"]}
        rec.update(elev_slope_rows[i])
        rec.update(water_flood_rows[i])
        lc = lc_by_uid.get(uid, {})
        rec["lc_majority"] = lc.get("lc_majority")
        rec["lc_hist"] = lc.get("lc_hist")
        records.append(rec)

    df = pd.DataFrame.from_records(records)
    df.to_parquet(ZONAL_PARQUET, index=False)

    n_updated = 0
    with get_conn() as conn:
        if table_exists(conn, "parcel"):
            for rec in records:
                stats = _json_safe({
                    "elev_mean": rec.get("elev_mean"), "elev_max": rec.get("elev_max"),
                    "slope_mean": rec.get("slope_mean"), "slope_p90": rec.get("slope_p90"),
                    "lc_majority": rec.get("lc_majority"), "lc_hist": rec.get("lc_hist"),
                    "water_occ_mean": rec.get("water_occ_mean"),
                    "water_occ_gt25_pct": rec.get("water_occ_gt25_pct"),
                    "flood_rp100_max": rec.get("flood_rp100_max"),
                })
                result = conn.execute(
                    "UPDATE parcel SET raster_stats = %s WHERE parcel_uid = %s",
                    (Jsonb(stats), rec["parcel_uid"]),
                )
                n_updated += result.rowcount
            conn.commit()

    coverage = {
        "elev": sum(1 for r in records if r.get("elev_mean") is not None),
        "slope": sum(1 for r in records if r.get("slope_mean") is not None),
        "water": sum(1 for r in records if r.get("water_occ_mean") is not None),
        "flood": sum(1 for r in records if r.get("flood_rp100_max") is not None),
        "lc": sum(1 for r in records if r.get("lc_majority") is not None),
    }

    return {
        "n_parcels": n,
        "sources_fetched": list(sources.keys()),
        "sources_skipped": skipped,
        "coverage": coverage,
        "parcel_rows_updated": n_updated,
        "zonal_parquet": str(ZONAL_PARQUET),
        "elapsed_s": round(time.time() - t0, 1),
    }
