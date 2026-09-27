"""JRC Global Surface Water occurrence (T1.5): public GCS/HTTP tiles, 10x10 degree, EPSG:4326.
Tiles are named by their NW corner: lon rounds down to a multiple of 10 (E/W), lat rounds up to
a multiple of 10 (N/S) -- e.g. our AOI (lon ~78E, lat ~8.8N) falls in `occurrence_70E_10Nv1_4_2021.tif`
(0-10N, 70-80E)."""
from __future__ import annotations

import math
from pathlib import Path

from .cog import mosaic_and_clip, write_cog
from .fetch import fetch

BASE_URL = "https://storage.googleapis.com/global-surface-water/downloads2021/occurrence"


def _tile_name(lat0: int, lon0: int) -> str:
    ns = f"{lat0}N" if lat0 >= 0 else f"{abs(lat0)}S"
    ew = f"{lon0}E" if lon0 >= 0 else f"{abs(lon0)}W"
    return f"occurrence_{ew}_{ns}v1_4_2021"


def tiles_for_bounds(bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    minx, miny, maxx, maxy = bounds
    lon0s = range(int(math.floor(minx / 10) * 10), int(math.floor(maxx / 10) * 10) + 1, 10)
    lat0s = range(int(math.ceil(miny / 10) * 10), int(math.ceil(maxy / 10) * 10) + 1, 10)
    return [(lat0, lon0) for lat0 in lat0s for lon0 in lon0s]


def fetch_jrc_tiles(bounds: tuple[float, float, float, float]) -> list[Path]:
    paths = []
    for lat0, lon0 in tiles_for_bounds(bounds):
        name = _tile_name(lat0, lon0)
        url = f"{BASE_URL}/{name}.tif"
        paths.append(fetch(url, dest_name=f"{name}.tif"))
    return paths


def build_water_occurrence(bounds: tuple[float, float, float, float], out_dir: Path) -> dict:
    tile_paths = fetch_jrc_tiles(bounds)
    merged = mosaic_and_clip(tile_paths, bounds)
    cog = write_cog(merged, out_dir / "water_occurrence.tif", nodata=255)
    return {"water_occurrence_cog": cog, "tiles": [str(p) for p in tile_paths]}
