"""GloFAS flood hazard v2.1, RP100 river-flood depth (T1.5): public JEODPP tiles (no login),
10x10 degree. Our fixed AOI (lon ~78E, lat ~8.8N) falls entirely in tile ID186 (0-10N, 70-80E);
this is not re-derived generically because the JEODPP tile IDs aren't a simple lat/lon formula
(see the CEMS-GLOFAS/flood_hazard/RP100/ directory listing) -- if the AOI ever moves outside
this cell, re-check the listing and update TILE_URL."""
from __future__ import annotations

from pathlib import Path

from .cog import mosaic_and_clip, write_cog
from .fetch import fetch

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/RP100"
TILE_NAME = "ID186_N10_E70_RP100_depth"


def build_flood_rp100(bounds: tuple[float, float, float, float], out_dir: Path) -> dict:
    url = f"{BASE_URL}/{TILE_NAME}.tif"
    tile_path = fetch(url, dest_name=f"{TILE_NAME}.tif")
    merged = mosaic_and_clip([tile_path], bounds)
    cog = write_cog(merged, out_dir / "flood_rp100.tif", nodata=-9999)
    return {"flood_rp100_cog": cog, "tiles": [str(tile_path)]}
