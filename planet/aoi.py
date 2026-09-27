"""Park AOI, parcel keys and the shared per-scene AOI grid.

* ``aoi_bbox()``   park boundary bbox expanded by ``AOI_BUFFER_M`` (2 km), lon/lat — STAC search box.
* ``aoi_grid()``   the production extraction grid (D-015): scene UTM CRS (EPSG:32643 for 43PHK), 10 m,
                   snapped to the 60 m S2 lattice, covering ``aoi_bbox()``. The SCL-only inventory pass and
                   P3 extraction use the same grid, so they share ``data/s2/<scene>/aoi_<gridkey>.tif``.
* ``load_parcels_uid()``  FMB polygons + ``parcel_uid = '<canonical village>|<KIDE>'`` (D-020).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import geopandas as gpd

    from planet.extract.parcel_stats import Grid

REPO = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = REPO / "Dataset" / "Geospatial_Layer" / "Park_Boundary.geojson"
FMB_PATH = REPO / "Dataset" / "Geospatial_Layer" / "Park_fmb_Map.geojson"
AOI_BUFFER_M = 2000.0
UTM_EPSG = 32643  # tile 43PHK

CANONICAL_VILLAGES = ("Allikulam", "Keelathattaparai", "Melathattaparai", "Umarikottai", "Peroorani",
                      "Ramasamypuram", "South Silukanpatti")
_ALIASES = {
    "keel thattaparai": "Keelathattaparai", "mela thattaparai": "Melathattaparai",
    "melathataparai": "Melathattaparai", "perurani": "Peroorani",
}


def canonical_village(name: str) -> str:
    """Map an FMB ``vil_name`` to its canonical spelling (land-domain-knowledge section 7).
    Unknown names raise: never guess a village."""
    s = " ".join(str(name).split())
    for c in CANONICAL_VILLAGES:
        if s.lower() == c.lower():
            return c
    if s.lower() in _ALIASES:
        return _ALIASES[s.lower()]
    raise ValueError(f"unknown village name {name!r}")


def parcel_uid(village: str, kide: str) -> str:
    return f"{canonical_village(village)}|{str(kide).strip()}"


def load_boundary() -> gpd.GeoDataFrame:
    import geopandas as gpd

    g = gpd.read_file(BOUNDARY_PATH)
    return g.set_crs("EPSG:4326") if g.crs is None else g.to_crs("EPSG:4326")


@lru_cache(maxsize=4)
def aoi_bbox(buffer_m: float = AOI_BUFFER_M) -> tuple[float, float, float, float]:
    """Park bbox grown by ``buffer_m`` (metres, applied in UTM), returned in lon/lat."""
    from rasterio.warp import transform_bounds

    b = load_boundary().total_bounds
    minx, miny, maxx, maxy = transform_bounds("EPSG:4326", f"EPSG:{UTM_EPSG}", *b, densify_pts=21)
    grown = (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)
    out = transform_bounds(f"EPSG:{UTM_EPSG}", "EPSG:4326", *grown, densify_pts=21)
    return tuple(round(v, 6) for v in out)  # type: ignore[return-value]


def aoi_grid(epsg: int = UTM_EPSG, buffer_m: float = AOI_BUFFER_M) -> Grid:
    from planet.extract.parcel_stats import native_grid

    return native_grid(aoi_bbox(buffer_m), epsg)


def load_parcels_uid(path: Path | str = FMB_PATH) -> gpd.GeoDataFrame:
    """FMB parcels (``pid`` 1..N in file order, see ``parcel_stats.load_parcels``) with ``parcel_uid``."""
    from planet.extract.parcel_stats import load_parcels

    gdf = load_parcels(path)
    gdf["parcel_uid"] = [parcel_uid(v, k) for v, k in zip(gdf["vil_name"], gdf["KIDE"])]
    dup = gdf["parcel_uid"].duplicated(keep=False)
    if dup.any():
        raise ValueError(f"duplicate parcel_uid: {sorted(gdf.loc[dup, 'parcel_uid'].unique())[:5]}")
    return gdf
