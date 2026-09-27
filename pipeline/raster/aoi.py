"""AOI = the park boundary buffered by 15 km (T1.5). Buffering is done in EPSG:32644 (UTM 44N,
per plan.md's "areas and distances in EPSG:32644" rule) and reprojected back to EPSG:4326 for
clipping rasters."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

REPO_ROOT = Path(__file__).resolve().parents[2]
PARK_BOUNDARY_GEOJSON = REPO_ROOT / "Dataset/Geospatial_Layer/Park_Boundary.geojson"
FMB_GEOJSON = REPO_ROOT / "Dataset/Geospatial_Layer/Park_fmb_Map.geojson"

BUFFER_M = 15_000
UTM_CRS = "EPSG:32644"
WGS84 = "EPSG:4326"


def load_park_boundary() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(PARK_BOUNDARY_GEOJSON)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    return gdf.to_crs(WGS84)


def aoi_geometry(buffer_m: int = BUFFER_M) -> BaseGeometry:
    """A single (unioned) AOI polygon in EPSG:4326: the park boundary, buffered by `buffer_m`
    in EPSG:32644."""
    boundary = load_park_boundary()
    buffered_utm = boundary.to_crs(UTM_CRS).buffer(buffer_m)
    unioned = buffered_utm.union_all() if hasattr(buffered_utm, "union_all") else buffered_utm.unary_union
    return gpd.GeoSeries([unioned], crs=UTM_CRS).to_crs(WGS84).iloc[0]


def aoi_bounds(buffer_m: int = BUFFER_M) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) of the AOI in EPSG:4326."""
    return aoi_geometry(buffer_m).bounds


def load_fmb_parcels() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(FMB_GEOJSON)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    return gdf.to_crs(WGS84)
