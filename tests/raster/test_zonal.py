import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from pipeline.raster.zonal import (
    LC_FRACTION_COLS,
    elevation_slope_stats,
    load_worldcover_lc,
    water_flood_stats,
    zonal_stat_one,
)

CRS = "EPSG:32644"


def _make_raster(path, array, transform, nodata=None, crs=CRS):
    profile = {
        "driver": "GTiff", "height": array.shape[0], "width": array.shape[1], "count": 1,
        "dtype": str(array.dtype), "crs": crs, "transform": transform, "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


def test_zonal_stat_one_basic_mean(tmp_path):
    # 10x10 grid, 10 m pixels, origin at (0, 100): values 0..99 row-major
    arr = np.arange(100, dtype="float64").reshape(10, 10)
    transform = from_origin(0, 100, 10, 10)
    path = tmp_path / "r.tif"
    _make_raster(path, arr, transform)

    geom = box(0, 60, 40, 100)  # top-left 4x4 block: rows 0-3, cols 0-3 -> values 0..3,10..13,...
    with rasterio.open(path) as src:
        vals = zonal_stat_one(src, geom)
    assert vals.size > 0
    assert vals.mean() < 50  # top-left block has the smaller values


def test_zonal_stat_one_respects_nodata(tmp_path):
    arr = np.full((5, 5), -9999.0)
    arr[2, 2] = 7.5
    transform = from_origin(0, 50, 10, 10)
    path = tmp_path / "r.tif"
    _make_raster(path, arr, transform, nodata=-9999)

    geom = box(0, 0, 50, 50)
    with rasterio.open(path) as src:
        vals = zonal_stat_one(src, geom)
    assert vals.size == 1
    assert vals[0] == 7.5


def test_zonal_stat_one_small_geometry_uses_all_touched(tmp_path):
    """A polygon much smaller than one pixel must still pick up that pixel (all_touched=True),
    matching the real dataset's small FMB subdivisions."""
    arr = np.array([[3.0]])
    transform = from_origin(0, 10, 10, 10)  # single 10x10 m pixel
    path = tmp_path / "r.tif"
    _make_raster(path, arr, transform)

    tiny = Polygon([(1, 8), (2, 8), (2, 9), (1, 9)])  # 1x1 m sliver inside the pixel
    with rasterio.open(path) as src:
        vals = zonal_stat_one(src, tiny)
    assert vals.size == 1
    assert vals[0] == 3.0


def test_zonal_stat_one_no_overlap_returns_empty(tmp_path):
    arr = np.ones((5, 5))
    transform = from_origin(0, 50, 10, 10)
    path = tmp_path / "r.tif"
    _make_raster(path, arr, transform)

    far_away = box(10_000, 10_000, 10_010, 10_010)
    with rasterio.open(path) as src:
        vals = zonal_stat_one(src, far_away)
    assert vals.size == 0


def test_elevation_slope_stats_end_to_end(tmp_path):
    dem = np.linspace(0, 90, 100).reshape(10, 10)
    slope = np.full((10, 10), 5.0)
    transform = from_origin(0, 100, 10, 10)
    dem_path, slope_path = tmp_path / "dem.tif", tmp_path / "slope.tif"
    _make_raster(dem_path, dem, transform)
    _make_raster(slope_path, slope, transform)

    geoms = [box(0, 60, 40, 100), box(0, 0, 100, 100)]
    rows = elevation_slope_stats(dem_path, slope_path, geoms)
    assert len(rows) == 2
    for row in rows:
        assert row["elev_mean"] is not None
        assert row["slope_mean"] == 5.0
        assert row["slope_p90"] == 5.0


def test_water_flood_stats_flood_optional(tmp_path):
    water = np.full((5, 5), 30.0)
    transform = from_origin(0, 50, 10, 10)
    water_path = tmp_path / "water.tif"
    _make_raster(water_path, water, transform, nodata=255, crs="EPSG:4326")

    rows = water_flood_stats(water_path, None, [box(0, 0, 50, 50)])
    assert rows[0]["water_occ_mean"] == 30.0
    assert rows[0]["water_occ_gt25_pct"] == 100.0
    assert rows[0]["flood_rp100_max"] is None


def test_water_flood_stats_with_flood_raster(tmp_path):
    water = np.full((5, 5), 10.0)
    flood = np.full((5, 5), 2.5)
    transform = from_origin(0, 50, 10, 10)
    water_path, flood_path = tmp_path / "water.tif", tmp_path / "flood.tif"
    _make_raster(water_path, water, transform, crs="EPSG:4326")
    _make_raster(flood_path, flood, transform, nodata=-9999, crs="EPSG:4326")

    rows = water_flood_stats(water_path, flood_path, [box(0, 0, 50, 50)])
    assert rows[0]["flood_rp100_max"] == 2.5


def test_load_worldcover_lc_from_parquet(tmp_path, monkeypatch):
    import pandas as pd

    from pipeline.raster import zonal as zonal_module

    df = pd.DataFrame([
        {"parcel_uid": "Allikulam|1/1", "majority_class": "cropland",
         **{c: 0.1 for c in LC_FRACTION_COLS}},
    ])
    parquet_path = tmp_path / "wc.parquet"
    df.to_parquet(parquet_path)
    monkeypatch.setattr(zonal_module, "WORLDCOVER_FRACTIONS_PARQUET", parquet_path)

    out = load_worldcover_lc(["Allikulam|1/1", "Missing|9/9"])
    assert out["Allikulam|1/1"]["lc_majority"] == "cropland"
    assert out["Allikulam|1/1"]["lc_hist"]["tree"] == 0.1
    assert "Missing|9/9" not in out


def test_load_worldcover_lc_missing_file_returns_empty(monkeypatch, tmp_path):
    from pipeline.raster import zonal as zonal_module

    monkeypatch.setattr(zonal_module, "WORLDCOVER_FRACTIONS_PARQUET", tmp_path / "nope.parquet")
    assert load_worldcover_lc(["Allikulam|1/1"]) == {}
