"""Offline chip rendering test on a tiny synthetic scene."""
from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("PIL")
pytest.importorskip("rasterio")
from affine import Affine
from PIL import Image
from shapely.geometry import box

from planet.chips import render as cr
from planet.extract import parcel_stats as ps


def _scene(n=80):
    rng = np.random.default_rng(0)
    dn = {b: rng.integers(300, 900, (n, n)).astype("uint16") for b in ("red", "green", "blue")}
    dn["nir"] = np.full((n, n), 3000, dtype="uint16")
    dn["nir"][:, : n // 2] = 700
    dn["scl"] = np.full((n, n), 4, dtype="uint16")
    dn["scl"][:5, :5] = 9  # a cloud corner
    grid = ps.Grid("EPSG:32643", Affine(10.0, 0, 600000.0, 0, -10.0, 970000.0), n, n)
    return dn, grid


def test_render_chips(tmp_path):
    dn, grid = _scene()
    geom = box(600300.0, 969500.0, 600400.0, 969600.0)
    res = cr.render_chips(dn, grid, geom, {"scene_id": "S2X_TEST_20250101_0_L2A", "date": "2025-01-01",
                                           "parcel_uid": "V|1"}, tmp_path, "2025-01-01")
    for kind in ("truecolor", "ndvi"):
        img = Image.open(res[kind])
        assert img.size == (256, 256) and img.mode == "RGB"
        assert img.text["scene_id"] == "S2X_TEST_20250101_0_L2A"
        meta = json.loads(img.text["nilasaatchi"])
        assert meta["kind"] == kind and meta["caveats"]
    a = np.asarray(Image.open(res["truecolor"]))
    assert ((a[..., 0] == 255) & (a[..., 1] == 255) & (a[..., 2] == 0)).sum() > 100  # yellow outline drawn
    nd = np.asarray(Image.open(res["ndvi"]))
    assert (nd == 0).all(axis=-1).sum() > 100  # black outline


def test_chip_window_min_side_and_clip():
    _, grid = _scene(80)
    r0, c0, nr, nc = cr.chip_window(box(600000.0, 969990.0, 600010.0, 970000.0), grid)
    assert (nr, nc) == (40, 40) and r0 == 0 and c0 == 0  # 400 m minimum, clipped at the corner


def test_ndvi_palette_invalid_grey():
    nd = np.array([[-0.5, 0.25, 0.9, 0.5]])
    valid = np.array([[True, True, True, False]])
    rgb = cr.ndvi_rgb(nd, valid)
    assert tuple(rgb[0, 0]) == cr.NDVI_STOPS[0][1] and tuple(rgb[0, 2]) == cr.NDVI_STOPS[-1][1]
    assert tuple(rgb[0, 3]) == cr.INVALID_RGB


def test_safe_name():
    assert cr.safe_name("Melathattaparai|236/2A") == "Melathattaparai__236-2A"
