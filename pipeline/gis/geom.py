"""Geometry hygiene and metric helpers (pure shapely/pyproj; mirrored in SQL by the loader).

D-023: reported areas are geodesic on the WGS84 ellipsoid (same as PostGIS ST_Area(geography)).
EPSG:32644 (UTM 44N) is used for distances, buffers and overlays only; its planar areas are ~0.2 %
too large here because the park lies 3° west of the zone's central meridian.
"""
from __future__ import annotations

from functools import lru_cache

import shapely
from pyproj import Geod, Transformer
from shapely.geometry import MultiLineString, MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

METRIC_EPSG = 32644
STORAGE_EPSG = 4326
_GEOD = Geod(ellps="WGS84")


@lru_cache(maxsize=4)
def _to_metric(src_epsg: int = STORAGE_EPSG) -> Transformer:
    return Transformer.from_crs(src_epsg, METRIC_EPSG, always_xy=True)


@lru_cache(maxsize=4)
def _from_metric(dst_epsg: int = STORAGE_EPSG) -> Transformer:
    return Transformer.from_crs(METRIC_EPSG, dst_epsg, always_xy=True)


def strip_z(g: BaseGeometry) -> BaseGeometry:
    return shapely.force_2d(g)


def polygonal_part(g: BaseGeometry) -> MultiPolygon:
    """Keep only polygonal parts (make_valid can emit lines/points on slivers) as a MultiPolygon."""
    if g.is_empty:
        return MultiPolygon()
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "MultiPolygon":
        return g
    polys: list = []
    for part in getattr(g, "geoms", []):
        sub = polygonal_part(part)
        polys.extend(sub.geoms)
    return MultiPolygon(polys)


def clean_polygonal(g: BaseGeometry) -> tuple[MultiPolygon, bool, bool]:
    """Z-strip + make_valid + multi. Returns (geom, had_z, was_invalid)."""
    had_z = bool(g.has_z)
    g2 = strip_z(g)
    was_invalid = not g2.is_valid
    if was_invalid:
        g2 = shapely.make_valid(g2)
    return polygonal_part(g2), had_z, was_invalid


def clean_linear(g: BaseGeometry) -> tuple[MultiLineString, bool]:
    had_z = bool(g.has_z)
    g2 = strip_z(g)
    if g2.geom_type == "LineString":
        g2 = MultiLineString([g2])
    return g2, had_z


def to_metric(g: BaseGeometry, src_epsg: int = STORAGE_EPSG) -> BaseGeometry:
    return transform(_to_metric(src_epsg).transform, g)


def from_metric(g: BaseGeometry, dst_epsg: int = STORAGE_EPSG) -> BaseGeometry:
    return transform(_from_metric(dst_epsg).transform, g)


def area_ha(g: BaseGeometry) -> float:
    """Geodesic area (WGS84 ellipsoid) of a lon/lat geometry, hectares, 4 dp (D-023)."""
    a, _ = _GEOD.geometry_area_perimeter(polygonal_part(g))
    return round(abs(a) / 1e4, 4)


def area_ha_utm(g: BaseGeometry, src_epsg: int = STORAGE_EPSG) -> float:
    """Planar area in EPSG:32644, hectares, 4 dp. Reference only; not a reported area."""
    return round(to_metric(g, src_epsg).area / 1e4, 4)
