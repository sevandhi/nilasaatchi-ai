"""Copernicus DEM GLO-30 (T1.5): anonymous S3/HTTPS tiles, mosaicked and clipped to the AOI,
plus a numpy-on-UTM slope derivative."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .cog import mosaic_and_clip, write_array_as_cog, write_cog
from .fetch import fetch

BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
UTM_CRS = "EPSG:32644"


def _tile_name(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00_DEM"


def tiles_for_bounds(bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    minx, miny, maxx, maxy = bounds
    lats = range(math.floor(miny), math.floor(maxy) + 1)
    lons = range(math.floor(minx), math.floor(maxx) + 1)
    return [(lat, lon) for lat in lats for lon in lons]


def fetch_dem_tiles(bounds: tuple[float, float, float, float]) -> list[Path]:
    paths = []
    for lat, lon in tiles_for_bounds(bounds):
        name = _tile_name(lat, lon)
        url = f"{BASE_URL}/{name}/{name}.tif"
        paths.append(fetch(url, dest_name=f"{name}.tif"))
    return paths


def build_dem_and_slope(bounds: tuple[float, float, float, float], out_dir: Path) -> dict:
    """Returns {"dem_cog": Path, "slope_cog": Path, "tiles": [...]}"""
    tile_paths = fetch_dem_tiles(bounds)
    merged = mosaic_and_clip(tile_paths, bounds)
    dem_cog = write_cog(merged, out_dir / "dem.tif", nodata=0)

    # slope: reproject the clipped DEM to UTM 44N (equal-distance-ish at this scale) and take a
    # numpy gradient, per phase1-data-foundation T1.5 ("gdaldem slope ... or with numpy on the
    # UTM-reprojected DEM").
    with rasterio.open(dem_cog) as src:
        transform, width, height = calculate_default_transform(
            src.crs, UTM_CRS, src.width, src.height, *src.bounds, resolution=30,
        )
        dem_utm = np.zeros((height, width), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1), destination=dem_utm,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=UTM_CRS, resampling=Resampling.bilinear,
        )
        dem_utm_path = write_array_as_cog(dem_utm, transform, UTM_CRS, out_dir / "dem_utm.tif")

    dy, dx = np.gradient(dem_utm, 30.0, 30.0)
    slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2))).astype(np.float32)
    slope_cog = write_array_as_cog(slope_deg, transform, UTM_CRS, out_dir / "slope.tif")

    return {"dem_cog": dem_cog, "dem_utm_cog": dem_utm_path, "slope_cog": slope_cog,
            "tiles": [str(p) for p in tile_paths]}
