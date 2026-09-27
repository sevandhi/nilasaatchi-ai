"""Offline tests for planet.extract.parcel_stats on synthetic arrays and tiny GeoTIFFs."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from affine import Affine
from pyproj import Transformer

from planet.extract import parcel_stats as ps
from planet.stac.search import SceneRecord

UTM = "EPSG:32643"
X0, Y0 = 600000.0, 969000.0  # multiples of 60 m, inside zone 43N near the park


# ---------------------------------------------------------------- pure array maths
def test_reflectance_offset_and_nodata():
    dn = np.array([[0, 1000, 1500]], dtype="uint16")
    r = ps.reflectance(dn, dn_offset=1000)
    assert np.isnan(r[0, 0]) and r[0, 1] == pytest.approx(0.0) and r[0, 2] == pytest.approx(0.05)
    assert ps.reflectance(dn)[0, 2] == pytest.approx(0.15)


def test_indices():
    r = {"red": np.array([0.05]), "nir": np.array([0.30]), "green": np.array([0.08]),
         "blue": np.array([0.04]), "swir16": np.array([0.20])}
    assert ps.compute_index("ndvi", r)[0] == pytest.approx(0.25 / 0.35, abs=1e-5)
    assert ps.compute_index("ndwi", r)[0] == pytest.approx(-0.22 / 0.38, abs=1e-5)
    assert ps.compute_index("ndbi", r)[0] == pytest.approx(-0.10 / 0.50, abs=1e-5)
    assert ps.compute_index("bsi", r)[0] == pytest.approx((0.25 - 0.34) / 0.59, abs=1e-5)
    with pytest.raises(ValueError):
        ps.compute_index("evi", r)


def test_valid_mask_classes():
    scl = np.arange(12)
    assert ps.valid_mask(scl).nonzero()[0].tolist() == [4, 5, 6, 7]


def test_grouped_median_matches_numpy():
    rng = np.random.default_rng(0)
    labels = rng.integers(0, 30, 5000)
    vals = rng.normal(size=5000)
    vals[::97] = np.nan
    got = ps.grouped_median(labels, vals, 31)
    for k in range(32):
        v = vals[(labels == k) & np.isfinite(vals)]
        if len(v):
            assert got[k] == pytest.approx(np.median(v))
        else:
            assert np.isnan(got[k])


def test_parcel_stats_counts_and_support():
    labels = np.array([[1, 1, 1, 2], [1, 1, 2, 2], [0, 0, 3, 3]])
    ndvi = np.array([[0.1, 0.2, 0.3, 0.9], [0.4, 9.9, 0.5, 0.7], [0.0, 0.0, 0.2, 0.2]])
    valid = np.ones_like(labels, bool)
    valid[1, 1] = False  # cloud over parcel 1's outlier
    df = ps.parcel_stats(labels, {"ndvi": ndvi}, valid, n_labels=4).set_index("pid")
    assert df.index.tolist() == [1, 2, 3]  # pid 4 absent from grid -> no row
    assert df.loc[1, "n_total"] == 5 and df.loc[1, "n_px"] == 4 and df.loc[1, "valid_frac"] == 0.8
    assert df.loc[1, "ndvi"] == pytest.approx(0.25)
    assert df.loc[2, "ndvi"] == pytest.approx(0.7)
    assert df.low_support.all()


# ---------------------------------------------------------------- grids
def test_legacy_grid_matches_gdal_warp_size():
    g = ps.legacy_grid((77.985, 8.755, 78.045, 8.815))
    assert g.shape == (600, 600) and g.crs == "EPSG:4326"
    assert g.bounds == pytest.approx((77.985, 8.755, 78.045, 8.815))


def test_native_grid_snaps_to_60m():
    g = ps.native_grid((77.985, 8.755, 78.045, 8.815), 32643)
    minx, miny, maxx, maxy = g.bounds
    assert all(abs(v / 60 - round(v / 60)) < 1e-9 for v in (minx, miny, maxx, maxy))
    assert g.transform.a == 10 and g.crs == UTM and g.key() == ps.native_grid((77.985, 8.755, 78.045, 8.815), 32643).key()


# ---------------------------------------------------------------- synthetic scene + parcels
def _write_tif(path, arr, res):
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype="uint16", crs=UTM, transform=Affine(res, 0, X0, 0, -res, Y0), nodata=0) as ds:
        ds.write(arr.astype("uint16"), 1)


def _lonlat_ring(x0, y0, x1, y1):
    tr = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    return [list(tr.transform(x, y)) for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]


@pytest.fixture()
def scene(tmp_path):
    """A 400 m x 400 m synthetic scene: 10 m red/nir (40x40), 20 m SCL (20x20).
    Left half NDVI 0.6 (red 1000, nir 4000), right half NDVI 0.2 (red 2000, nir 3000).
    SCL: vegetation (4) everywhere except a cloud (9) block over the top-left 100 m x 100 m."""
    red = np.full((40, 40), 2000)
    nir = np.full((40, 40), 3000)
    red[:, :20], nir[:, :20] = 1000, 4000
    scl = np.full((20, 20), 4)
    scl[:5, :5] = 9
    hrefs = {}
    for name, arr, res in [("red", red, 10), ("nir", nir, 10), ("scl", scl, 20)]:
        p = tmp_path / f"{name}.tif"
        _write_tif(p, arr, res)
        hrefs[name] = str(p)
    rec = SceneRecord(id="S2X_43PHK_20250101_0_L2A", datetime=datetime(2025, 1, 1, tzinfo=UTC),
                      tile="43PHK", cloud=0.0, hrefs=hrefs, epsg=32643)
    return rec


@pytest.fixture()
def parcels(tmp_path):
    feats = [
        # A1: 200 m x 200 m in the left half, includes the cloud block (100x100 m)
        {"Land_id": "A1", "vil_name": "Allikulam", "survey_no": 1, "sub_div": None, "KIDE": "1",
         "ring": _lonlat_ring(X0, Y0 - 200, X0 + 200, Y0)},
        # 7: 100 m x 200 m in the right half
        {"Land_id": "7", "vil_name": "Peroorani", "survey_no": 7, "sub_div": "2", "KIDE": "7/2",
         "ring": _lonlat_ring(X0 + 250, Y0 - 300, X0 + 350, Y0 - 100)},
        # tiny sliver, 8 m wide: a -5 m buffer empties it -> original geometry kept
        {"Land_id": "S", "vil_name": "Peroorani", "survey_no": 9, "sub_div": None, "KIDE": "9",
         "ring": _lonlat_ring(X0 + 250, Y0 - 390, X0 + 258, Y0 - 330)},
    ]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: v for k, v in f.items() if k != "ring"},
         "geometry": {"type": "MultiPolygon", "coordinates": [[f["ring"]]]}} for f in feats]}
    p = tmp_path / "fmb.geojson"
    p.write_text(json.dumps(fc))
    return ps.load_parcels(p)


def _grid():
    return ps.Grid(UTM, Affine(10, 0, X0, 0, -10, Y0), 40, 40, "native")


def test_load_parcels_sequential_pid(parcels):
    assert parcels.pid.tolist() == [1, 2, 3]
    assert parcels.Land_id.tolist() == ["A1", "7", "S"]


def test_rasterise_and_buffer(parcels):
    g = _grid()
    raw = ps.rasterise(ps.prepare_parcels(parcels, UTM, None), g)
    assert (raw == 1).sum() == 400 and (raw == 2).sum() == 200
    buf_gdf = ps.prepare_parcels(parcels, UTM, -5.0)
    assert buf_gdf.buffered.tolist() == [True, True, False]  # sliver keeps original geometry
    buf = ps.rasterise(buf_gdf, g)
    # 190 m x 190 m and 90 m x 190 m after -5 m; pixel centres on the edge may fall either way
    assert 18 * 18 < (buf == 1).sum() < 400 and 8 * 18 < (buf == 2).sum() < 200
    assert (buf == 3).sum() == (raw == 3).sum() > 0


def test_extract_scene_end_to_end_and_cache(scene, parcels, tmp_path):
    cache = tmp_path / "cache"
    g = _grid()
    df = ps.extract_scene(scene, parcels, g, buffer_m=None, indices=("ndvi",), cache_dir=cache).set_index("Land_id")
    a1 = df.loc["A1"]
    assert a1.n_total == 400 and a1.n_px == 300 and a1.valid_frac == pytest.approx(0.75)
    assert a1.ndvi == pytest.approx(0.6, abs=1e-4) and not a1.low_support
    assert df.loc["7"].ndvi == pytest.approx(0.2, abs=1e-4) and df.loc["7"].n_px == 200
    assert (df.scene_id == scene.id).all() and (df.date == "2025-01-01").all()
    cached = list(cache.glob(f"{scene.id}/aoi_*.tif"))
    assert len(cached) == 1
    # cache hit: sources gone, same answer
    for h in scene.hrefs.values():
        __import__("os").remove(h)
    df2 = ps.extract_scene(scene, parcels, g, buffer_m=None, cache_dir=cache).set_index("Land_id")
    assert df2.loc["A1"].ndvi == pytest.approx(a1.ndvi) and df2.loc["A1"].n_px == 300


def test_extract_scene_buffer_on_by_default(scene, parcels, tmp_path):
    df = ps.extract_scene(scene, parcels, _grid(), cache_dir=tmp_path / "c").set_index("Land_id")
    assert 18 * 18 < df.loc["A1"].n_total < 400


def test_scl_nearest_upsampling(scene, tmp_path):
    dn = ps.read_scene(scene, _grid(), bands=("scl", "red"), cache_dir=tmp_path / "c")
    assert dn["scl"].shape == (40, 40)
    assert set(np.unique(dn["scl"])) == {4, 9}  # nearest: no interpolated classes
    assert (dn["scl"][:10, :10] == 9).all() and (dn["scl"][10:, :] == 4).all()


def test_legacy_warp_path(scene, parcels, tmp_path):
    """Legacy (EPSG:4326) grid goes through the warp path and still recovers the halves' NDVI."""
    minx, miny = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform(X0, Y0 - 400)
    maxx, maxy = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform(X0 + 400, Y0)
    g = ps.legacy_grid((minx, miny, maxx, maxy), res=0.00005)
    df = ps.extract_scene(scene, parcels, g, buffer_m=None, cache_dir=tmp_path / "c").set_index("Land_id")
    assert df.loc["A1"].ndvi == pytest.approx(0.6, abs=0.01)
    assert df.loc["7"].ndvi == pytest.approx(0.2, abs=0.01)
