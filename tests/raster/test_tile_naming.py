from pipeline.raster.dem import _tile_name as dem_tile_name
from pipeline.raster.dem import tiles_for_bounds as dem_tiles_for_bounds
from pipeline.raster.jrc import _tile_name as jrc_tile_name
from pipeline.raster.jrc import tiles_for_bounds as jrc_tiles_for_bounds


def test_dem_tile_name_matches_copernicus_convention():
    assert dem_tile_name(8, 77) == "Copernicus_DSM_COG_10_N08_00_E077_00_DEM"
    assert dem_tile_name(8, 78) == "Copernicus_DSM_COG_10_N08_00_E078_00_DEM"


def test_dem_tiles_for_park_aoi_bounds():
    # the real AOI (park + 15 km) straddles 78 degrees E, per land-domain-knowledge §10
    tiles = dem_tiles_for_bounds((77.86, 8.63, 78.18, 8.94))
    assert (8, 77) in tiles
    assert (8, 78) in tiles
    assert len(tiles) == 2


def test_jrc_tile_name_matches_confirmed_url():
    # verified against the live bucket during development: occurrence_70E_10Nv1_4_2021.tif
    assert jrc_tile_name(10, 70) == "occurrence_70E_10Nv1_4_2021"


def test_jrc_tiles_for_park_aoi_bounds_rounds_to_10_degree_cell():
    tiles = jrc_tiles_for_bounds((77.86, 8.63, 78.18, 8.94))
    assert tiles == [(10, 70)]
