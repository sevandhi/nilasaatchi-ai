"""Offline tests for planet.extract.worldcover (tiny synthetic GeoTIFFs) and planet.tabio."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")
from affine import Affine
from shapely.geometry import box

from planet import tabio
from planet.extract import worldcover as wc

RES = 1 / 12000  # WorldCover lattice


def _tif(path, arr, left, top):
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype="uint8", crs="EPSG:4326", transform=Affine(RES, 0, left, 0, -RES, top),
                       nodata=0) as ds:
        ds.write(arr, 1)
    return str(path)


def test_read_mosaic_joins_tiles_across_78E(tmp_path):
    # west tile ends at 78.0 E, east tile starts there (like N06E075 / N06E078)
    west = _tif(tmp_path / "w.tif", np.full((24, 24), 40, "uint8"), 78.0 - 24 * RES, 8.8)
    east = _tif(tmp_path / "e.tif", np.full((24, 24), 10, "uint8"), 78.0, 8.8)
    arr, t, crs = wc.read_mosaic([west, east], (78.0 - 10 * RES, 8.8 - 10 * RES, 78.0 + 10 * RES, 8.8 - RES),
                                 pad_px=2)
    assert crs == "EPSG:4326"
    inner = arr[2:-2, 2:-2]
    assert set(np.unique(inner)) == {10, 40} and (inner > 0).all()
    col78 = round((78.0 - t.c) / RES)
    assert (arr[3, :col78] == 40).all() and (arr[3, col78:col78 + 5] == 10).all()


def test_read_mosaic_rejects_mismatched_grid(tmp_path):
    a = _tif(tmp_path / "a.tif", np.ones((4, 4), "uint8"), 78.0, 8.8)
    with rasterio.open(tmp_path / "b.tif", "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=Affine(2 * RES, 0, 78.0, 0, -2 * RES, 8.8)) as ds:
        ds.write(np.ones((4, 4), "uint8"), 1)
    with pytest.raises(ValueError):
        wc.read_mosaic([a, str(tmp_path / "b.tif")], (78.0, 8.8 - 3 * RES, 78.0 + 3 * RES, 8.8))


def _grid():
    classes = np.zeros((10, 10), "uint8")
    classes[:, :5] = 40   # cropland west
    classes[:, 5:] = 20   # shrub east
    classes[0, :] = 0     # nodata row
    return classes, Affine(1.0, 0, 0.0, 0, -1.0, 10.0)


def test_parcel_fractions_area_weighted():
    classes, t = _grid()
    parcels = gpd.GeoDataFrame(geometry=[box(3, 2, 7, 6),        # straddles the class edge
                                         box(1.5, 2, 2.5, 3),    # sub-pixel sized, all cropland
                                         box(3, 8.5, 7, 10.0),   # half in the nodata row
                                         box(50, 50, 51, 51)],   # off-raster
                               crs="EPSG:32643")
    fr = wc.parcel_fractions(parcels, classes, t, "EPSG:32643", supersample=4)
    assert fr.loc[0, "frac_cropland"] == pytest.approx(0.5) and fr.loc[0, "frac_shrub"] == pytest.approx(0.5)
    assert fr.loc[0, "n_px"] == pytest.approx(16) and fr.loc[0, "nodata_frac"] == 0
    assert fr.loc[1, "frac_cropland"] == 1.0 and fr.loc[1, "n_px"] == pytest.approx(1.0)
    assert fr.loc[2, "nodata_frac"] == pytest.approx(1 / 1.5, abs=0.01)
    assert fr.loc[2, "frac_cropland"] + fr.loc[2, "frac_shrub"] == pytest.approx(1.0)
    assert fr.loc[3, "n_px"] == 0 and np.isnan(fr.loc[3, "frac_cropland"])


def test_parcel_fractions_supersampling_beats_centre_rule():
    classes, t = _grid()
    small = gpd.GeoDataFrame(geometry=[box(4.6, 3.1, 5.4, 3.9)], crs="EPSG:32643")  # 0.8 x 0.8, spans x=5
    fr = wc.parcel_fractions(small, classes, t, "EPSG:32643", supersample=10)
    assert fr.loc[0, "frac_cropland"] == pytest.approx(0.5, abs=0.13)
    assert fr.loc[0, "n_px"] > 0


def test_weak_labels_mapping():
    cols = {f"frac_{c}": [0.0, 0.0, np.nan] for c in wc.CLASSES.values()}
    fr = pd.DataFrame(cols)
    fr.loc[0, ["frac_tree", "frac_shrub", "frac_cropland"]] = [0.3, 0.3, 0.4]  # 0.6 perennial beats 0.4 crop
    fr.loc[1, ["frac_grass", "frac_bare", "frac_cropland"]] = [0.3, 0.25, 0.45]
    wl = wc.weak_labels(fr)
    assert wl.loc[0, "weak_label"] == "perennial_veg" and wl.loc[0, "weak_label_frac"] == pytest.approx(0.6)
    assert wl.loc[0, "majority_class"] == "cropland"
    assert wl.loc[1, "weak_label"] == "bare_fallow" and wl.loc[1, "state_cropped"] == pytest.approx(0.45)
    assert pd.isna(wl.loc[2, "weak_label"])


def test_every_class_has_a_state():
    assert set(wc.CLASSES.values()) == set(wc.STATE_OF)
    assert set(wc.STATE_OF.values()) <= {"cropped", "irrigated_multi", "perennial_veg", "bare_fallow",
                                         "cleared_or_built", "water"}


def test_find_sources_prefers_local_raster(tmp_path):
    assert wc.find_sources(tmp_path / "missing") == [wc.WC_URL.format(tile=t) for t in wc.WC_TILES]
    (tmp_path / "ESA_WorldCover_clip.tif").write_bytes(b"")
    assert wc.find_sources(tmp_path) == [str(tmp_path / "ESA_WorldCover_clip.tif")]


def test_tabio_csv_fallback_roundtrip(tmp_path, monkeypatch):
    df = pd.DataFrame({"parcel_uid": ["A|1", "B|2"], "frac_tree": [0.1, 0.9]})
    monkeypatch.setattr(tabio, "parquet_available", lambda: False)
    p = tabio.write_table(df, tmp_path / "t.parquet")
    assert p.name == "t.csv.gz" and not (tmp_path / "t.parquet").exists()
    pd.testing.assert_frame_equal(tabio.read_table(tmp_path / "t.parquet"), df)
    with pytest.raises(FileNotFoundError):
        tabio.read_table(tmp_path / "nope.parquet")
