"""Mosaic + clip-to-AOI-bounds + COG write helpers, shared by dem.py / jrc.py / glofas.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles


def mosaic_and_clip(tile_paths: list[Path], bounds: tuple[float, float, float, float]) -> Path:
    """Merge `tile_paths` (in their native CRS) and clip to `bounds` (in that same CRS).
    Returns a path to a plain (non-COG) intermediate GeoTIFF under the first tile's directory."""
    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        array, transform = merge(srcs, bounds=bounds)
        profile = srcs[0].profile
    finally:
        for s in srcs:
            s.close()
    profile.update(height=array.shape[1], width=array.shape[2], transform=transform,
                    driver="GTiff", compress="deflate")
    out = tile_paths[0].parent / f"_merged_{tile_paths[0].stem}.tif"
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(array)
    return out


def write_cog(src_path: Path, dst_path: Path, nodata: float | None = None) -> Path:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_profile = cog_profiles.get("deflate")
    with rasterio.open(src_path) as src:
        cog_translate(src, str(dst_path), dst_profile, in_memory=False, quiet=True, nodata=nodata)
    return dst_path


def write_array_as_cog(array: np.ndarray, transform, crs, dst_path: Path,
                        nodata: float | None = None) -> Path:
    """Write a single-band in-memory array straight to a COG (used for derived rasters like
    slope, computed with numpy rather than mosaicked from tiles)."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff", "height": array.shape[-2], "width": array.shape[-1],
        "count": 1, "dtype": str(array.dtype), "crs": crs, "transform": transform,
        "nodata": nodata,
    }
    dst_profile = cog_profiles.get("deflate")
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(array if array.ndim == 3 else array[np.newaxis, :, :])
        with mem.open() as src:  # reopen read-only: cog_translate warns/disallows write-mode sources
            cog_translate(src, str(dst_path), dst_profile, in_memory=False, quiet=True)
    return dst_path
