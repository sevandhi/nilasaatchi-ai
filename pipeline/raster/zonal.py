"""Per-parcel zonal statistics (T1.5) from the clipped rasters, plus the WorldCover land-cover
fractions the eo-engineer already computed (data/s2/worldcover_fractions.parquet) -- we do not
recompute WorldCover ourselves, per the P1 raster follow-up (D-026)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

REPO_ROOT = Path(__file__).resolve().parents[2]
WORLDCOVER_FRACTIONS_PARQUET = REPO_ROOT / "data" / "s2" / "worldcover_fractions.parquet"

LC_FRACTION_COLS = [
    "frac_tree", "frac_shrub", "frac_grass", "frac_cropland", "frac_built", "frac_bare",
    "frac_snow", "frac_water", "frac_wetland", "frac_mangrove", "frac_moss",
]


def zonal_stat_one(src: rasterio.DatasetReader, geom: BaseGeometry, nodata_override=None) -> np.ndarray:
    """Valid (non-nodata, finite) pixel values under `geom` (already in `src`'s CRS), or an
    empty array if the geometry doesn't overlap any valid pixel."""
    nodata = nodata_override if nodata_override is not None else src.nodata
    try:
        # all_touched=True: many FMB subdivisions are smaller than one 30 m DEM/JRC pixel, and
        # the default "pixel centre inside polygon" rule would silently return zero pixels for
        # them even though they clearly overlap the raster.
        out_image, _ = mask(src, [mapping(geom)], crop=True, filled=True, all_touched=True,
                             nodata=nodata if nodata is not None else np.nan)
    except ValueError:
        return np.array([])
    arr = out_image[0].astype("float64")
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    return arr[np.isfinite(arr)]


def elevation_slope_stats(dem_utm_path: Path, slope_path: Path, geoms_utm: list[BaseGeometry]) -> list[dict]:
    rows = []
    with rasterio.open(dem_utm_path) as dem_src, rasterio.open(slope_path) as slope_src:
        for geom in geoms_utm:
            elev = zonal_stat_one(dem_src, geom)
            slope = zonal_stat_one(slope_src, geom)
            rows.append({
                "elev_mean": float(np.mean(elev)) if elev.size else None,
                "elev_max": float(np.max(elev)) if elev.size else None,
                "slope_mean": float(np.mean(slope)) if slope.size else None,
                "slope_p90": float(np.percentile(slope, 90)) if slope.size else None,
            })
    return rows


def water_flood_stats(water_occ_path: Path, flood_path: Path | None,
                       geoms_wgs84: list[BaseGeometry]) -> list[dict]:
    rows = []
    with rasterio.open(water_occ_path) as water_src:
        flood_src = rasterio.open(flood_path) if flood_path else None
        try:
            for geom in geoms_wgs84:
                occ = zonal_stat_one(water_src, geom)
                # JRC occurrence: 0-100 (% of water observations); 255 (or the file's own
                # nodata) marks land/ocean masked-out pixels, already dropped by zonal_stat_one.
                occ = occ[(occ >= 0) & (occ <= 100)]
                row = {
                    "water_occ_mean": float(np.mean(occ)) if occ.size else None,
                    "water_occ_gt25_pct": float(np.mean(occ > 25) * 100) if occ.size else None,
                }
                if flood_src is not None:
                    flood = zonal_stat_one(flood_src, geom)
                    row["flood_rp100_max"] = float(np.max(flood)) if flood.size else None
                else:
                    row["flood_rp100_max"] = None
                rows.append(row)
        finally:
            if flood_src is not None:
                flood_src.close()
    return rows


def load_worldcover_lc(parcel_uids: list[str]) -> dict[str, dict]:
    if not WORLDCOVER_FRACTIONS_PARQUET.exists():
        return {}
    df = pd.read_parquet(WORLDCOVER_FRACTIONS_PARQUET)
    df = df.set_index("parcel_uid")
    out: dict[str, dict] = {}
    for uid in parcel_uids:
        if uid not in df.index:
            continue
        row = df.loc[uid]
        out[uid] = {
            "lc_majority": row.get("majority_class"),
            "lc_hist": {c.replace("frac_", ""): float(row[c]) for c in LC_FRACTION_COLS if c in row},
        }
    return out
