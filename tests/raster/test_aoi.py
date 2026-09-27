import math

from pipeline.raster.aoi import BUFFER_M, aoi_bounds, aoi_geometry


def test_aoi_geometry_contains_the_park_and_extends_about_15km():
    park_bounds = (77.9955, 8.7642, 78.0465, 8.8089)  # from Dataset/Geospatial_Layer/Park_Boundary.geojson
    aoi = aoi_geometry(BUFFER_M)
    minx, miny, maxx, maxy = aoi.bounds

    assert minx < park_bounds[0]
    assert maxx > park_bounds[2]
    assert miny < park_bounds[1]
    assert maxy > park_bounds[3]

    # ~15 km in degrees of longitude at this latitude (~8.8N): 15000 / (111320 * cos(8.8deg))
    expected_deg = 15_000 / (111_320 * math.cos(math.radians(8.8)))
    west_margin = park_bounds[0] - minx
    assert abs(west_margin - expected_deg) < expected_deg * 0.15  # within 15%


def test_aoi_bounds_matches_aoi_geometry_bounds():
    assert aoi_bounds(BUFFER_M) == aoi_geometry(BUFFER_M).bounds


def test_smaller_buffer_gives_smaller_aoi():
    small = aoi_geometry(1_000)
    large = aoi_geometry(BUFFER_M)
    assert small.area < large.area
