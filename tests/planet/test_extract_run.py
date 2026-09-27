"""Offline tests for planet.extract.run: parcel footprints (D-026) and per-parcel obs maths."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("rasterio")
import geopandas as gpd
from affine import Affine
from shapely.geometry import box

from planet.extract import parcel_stats as ps
from planet.extract import run as er

X0, Y0 = 600000.0, 970000.0
GRID = ps.Grid("EPSG:32643", Affine(10.0, 0, X0, 0, -10.0, Y0), 40, 40)


def _parcels():
    geoms = [box(X0 + 20, Y0 - 220, X0 + 220, Y0 - 20),   # 20x20 px -> buffered 19x19-ish
             box(X0 + 100, Y0 - 120, X0 + 300, Y0 - 20),  # overlaps the first
             box(X0 + 302, Y0 - 330, X0 + 328, Y0 - 302),  # ~2.6 x 2.8 px: structurally small
             box(X0 + 351, Y0 - 354, X0 + 354, Y0 - 351)]  # no pixel centre inside
    return gpd.GeoDataFrame({"parcel_uid": ["A", "B", "C", "D"]}, geometry=geoms, crs="EPSG:32643")


def test_footprints_modes_and_overlap():
    pix = er.parcel_pixels(_parcels(), GRID)
    assert pix.mode.tolist() == ["buf5", "buf5", "unbuffered", "all_touched"]
    assert pix.mixed.tolist() == [False, False, True, True]
    assert 19 * 19 <= pix.n_total[0] <= 20 * 20 and 19 * 9 <= pix.n_total[1] <= 20 * 10  # centre-on-edge ties
    # overlapping pixels belong to both parcels (no burn-order loss)
    a = set(pix.idx[pix.lab == 0]); b = set(pix.idx[pix.lab == 1])
    assert len(a & b) > 0
    assert pix.n_total[2] == 9 and pix.n_total[3] >= 1


def test_obs_from_arrays():
    pix = er.parcel_pixels(_parcels(), GRID)
    n = GRID.shape
    dn = {"red": np.full(n, 500, "uint16"), "green": np.full(n, 800, "uint16"), "blue": np.full(n, 400, "uint16"),
          "nir": np.full(n, 3000, "uint16"), "swir16": np.full(n, 2000, "uint16"), "scl": np.full(n, 4, "uint16")}
    dn["scl"][:, 25:] = 9  # right part cloudy
    df = er.obs_from_arrays(dn, 0, pix)
    a = df.set_index("parcel_uid")
    assert a.loc["A", "ndvi"] == pytest.approx(2500 / 3500, abs=1e-5)
    assert a.loc["A", "valid_frac"] == 1.0 and not a.loc["A", "low_support"]
    assert 0 < a.loc["B", "valid_frac"] < 1
    assert a.loc["C", "low_support"] and a.loc["C", "mixed_pixel"] and np.isnan(a.loc["C", "ndvi"])
    # dn_offset shifts reflectance
    df2 = er.obs_from_arrays(dn, 300, pix).set_index("parcel_uid")
    assert df2.loc["A", "ndvi"] == pytest.approx(2500 / (2700 + 200), abs=1e-5)


def test_radiometry_percentiles():
    n = (10, 10)
    dn = {b: np.full(n, 1100, "uint16") for b in ps.SPECTRAL}
    dn["scl"] = np.full(n, 4, "uint16")
    r = er.radiometry(dn, np.ones(n, bool))
    assert r["red"]["p1"] == 1100 and r["n_valid_px"] == 100


def test_verified_offset_rule():
    from planet.extract.dn_check import verified_offset

    assert verified_offset(0, None) == (0, "not flagged")
    assert verified_offset(1000, {"blue": {"p1": 1304.0}})[0] == 1000
    assert verified_offset(1000, {"blue": {"p1": 235.0}})[0] == 0
    assert verified_offset(1000, None)[0] == 1000  # unverified keeps the STAC-derived flag
