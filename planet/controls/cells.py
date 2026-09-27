"""Control group (D-035): non-acquired farmland "pseudo-parcels" in a ring 2-6 km outside the park.

Candidates are 80 x 80 m cells on a UTM lattice aligned to the 10 m Sentinel-2 grid (EPSG:32643), inside
ring = (park boundary U FMB parcels) buffered 6 km minus the same buffered 2 km. A cell qualifies when:
- ESA WorldCover 2021 (sampled at the 64 pixel centres of the cell) is >= 80 % cropland, and
  tree + built + water + wetland is 0 % (no mixed edge with them);
- it does not touch a mapped waterbody (buffered 30 m);
- it lies > 2 km from any other SIPCOT park point (``ref_layer_sipcot_parks``; the park itself is the one
  at distance 0 and is already excluded by the ring).
Selection: thin to <= 1 cell per 500 m block (spatial independence), then a seeded sample stratified over
8 compass sectors, proportional to availability, N_TARGET cells in total.

Caveats: WorldCover 2021 cropland is a single-date map (~75 % accuracy; cropland/grass confusion); the ring
has the same rainfall regime but may differ in soils, irrigation access and land markets; controls are
signals for comparison, not ground truth. Cells are stored with their WorldCover evidence.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from planet import aoi

CELL_M = 80.0
RING_M = (2000.0, 6000.0)
N_TARGET = 500
BLOCK_M = 500.0
SEED = 20260926
MIN_CROPLAND = 0.8
CELL_VERSION = "controls-v1"


def ring_geometry(conn: Any) -> Any:
    """Ring polygon in EPSG:32643 (shapely)."""
    from shapely import wkb

    g = conn.execute(
        "SELECT ST_AsBinary(ST_Transform(ST_Union(ARRAY[(SELECT ST_Union(geom) FROM ref_layer_park_boundary), "
        "(SELECT ST_Union(geom) FROM parcel)]), %s))", (aoi.UTM_EPSG,)).fetchone()[0]
    core = wkb.loads(bytes(g)).buffer(0)
    return core.buffer(RING_M[1]).difference(core.buffer(RING_M[0])), core


def lattice(ring: Any) -> pd.DataFrame:
    """Cell lower-left corners (UTM) whose whole square lies inside the ring."""
    import shapely
    from shapely.prepared import prep

    minx, miny, maxx, maxy = ring.bounds
    x0, y0 = math.floor(minx / CELL_M) * CELL_M, math.floor(miny / CELL_M) * CELL_M
    xs = np.arange(x0, maxx, CELL_M)
    ys = np.arange(y0, maxy, CELL_M)
    X, Y = np.meshgrid(xs, ys)
    X, Y = X.ravel(), Y.ravel()
    boxes = shapely.box(X, Y, X + CELL_M, Y + CELL_M)
    pr = prep(ring)
    keep = np.array([pr.contains(b) for b in boxes])
    return pd.DataFrame({"x0": X[keep], "y0": Y[keep]})


def worldcover_fractions(cells: pd.DataFrame) -> pd.DataFrame:
    """Class fractions from WorldCover sampled at the 10 m pixel centres of each cell."""
    from pyproj import Transformer

    from planet.extract import worldcover as wc

    k = int(CELL_M // 10)
    offs = (np.arange(k) + 0.5) * 10.0
    ox, oy = np.meshgrid(offs, offs)
    px = (cells.x0.to_numpy()[:, None] + ox.ravel()[None, :]).ravel()
    py = (cells.y0.to_numpy()[:, None] + oy.ravel()[None, :]).ravel()
    lon, lat = Transformer.from_crs(f"EPSG:{aoi.UTM_EPSG}", "EPSG:4326", always_xy=True).transform(px, py)
    bounds = (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))
    classes, transform, _ = wc.read_mosaic(wc.find_sources(), bounds)
    inv = ~transform
    col, row = inv * (lon, lat)
    col = np.clip(np.floor(col).astype(int), 0, classes.shape[1] - 1)
    row = np.clip(np.floor(row).astype(int), 0, classes.shape[0] - 1)
    v = classes[row, col].reshape(len(cells), k * k)
    out = cells.copy()
    valid = (v > 0).sum(axis=1)
    for code, name in wc.CLASSES.items():
        out[f"frac_{name}"] = np.where(valid > 0, (v == code).sum(axis=1) / np.maximum(valid, 1), np.nan)
    out["wc_valid_px"] = valid
    return out


def exclusions(conn: Any, cells: pd.DataFrame, core: Any) -> pd.DataFrame:
    import shapely
    from shapely import wkb

    wat = conn.execute("SELECT ST_AsBinary(ST_Transform(ST_Union(geom), %s)) FROM ref_layer_waterbodies",
                       (aoi.UTM_EPSG,)).fetchone()[0]
    water = wkb.loads(bytes(wat)).buffer(30.0) if wat else None
    pts = [wkb.loads(bytes(r[0])) for r in conn.execute(
        "SELECT ST_AsBinary(ST_Transform(geom, %s)) FROM ref_layer_sipcot_parks", (aoi.UTM_EPSG,)).fetchall()]
    others = [p for p in pts if p.distance(core) > 50.0]
    sip = shapely.union_all([p.buffer(2000.0) for p in others]) if others else None
    boxes = shapely.box(cells.x0, cells.y0, cells.x0 + CELL_M, cells.y0 + CELL_M)
    out = cells.copy()
    out["touches_water"] = shapely.intersects(boxes, water) if water is not None else False
    out["near_other_sipcot"] = shapely.intersects(boxes, sip) if sip is not None else False
    cx, cy = cells.x0 + CELL_M / 2, cells.y0 + CELL_M / 2
    centre = shapely.points(cx, cy)
    out["dist_km"] = np.round(shapely.distance(centre, core) / 1000.0, 3)
    c = core.centroid
    ang = (np.degrees(np.arctan2(cx - c.x, cy - c.y)) + 360.0) % 360.0
    out["sector"] = np.array(["N", "NE", "E", "SE", "S", "SW", "W", "NW"])[((ang + 22.5) // 45).astype(int) % 8]
    out["n_other_sipcot_points"] = len(others)
    return out


def select(cells: pd.DataFrame, n: int = N_TARGET, seed: int = SEED) -> pd.DataFrame:
    ok = ((cells.frac_cropland >= MIN_CROPLAND)
          & (cells[["frac_tree", "frac_built", "frac_water", "frac_wetland"]].fillna(0).sum(axis=1) == 0)
          & (cells.wc_valid_px >= 0.9 * (CELL_M / 10) ** 2) & ~cells.touches_water & ~cells.near_other_sipcot)
    c = cells[ok].copy()
    rng = np.random.default_rng(seed)
    c["block"] = (np.floor(c.x0 / BLOCK_M)).astype(int).astype(str) + "_" + (np.floor(c.y0 / BLOCK_M)).astype(int).astype(str)
    c["r"] = rng.random(len(c))
    thin = c.sort_values("r").drop_duplicates("block")
    share = thin.sector.value_counts(normalize=True)
    parts = []
    for sec, g in thin.groupby("sector"):
        parts.append(g.sort_values("r").head(max(1, round(n * share[sec]))))
    sel = pd.concat(parts).sort_values(["sector", "x0", "y0"]).head(n).reset_index(drop=True)
    sel["cell_id"] = [f"C{int(x)}_{int(y)}" for x, y in zip(sel.x0, sel.y0, strict=True)]
    sel.attrs["n_candidates"] = len(cells)
    sel.attrs["n_eligible"] = len(c)
    sel.attrs["n_after_thinning"] = len(thin)
    return sel.drop(columns=["r"])


def to_gdf(sel: pd.DataFrame) -> Any:
    import geopandas as gpd
    import shapely

    g = gpd.GeoDataFrame(sel.copy(), geometry=shapely.box(sel.x0, sel.y0, sel.x0 + CELL_M, sel.y0 + CELL_M),
                         crs=f"EPSG:{aoi.UTM_EPSG}")
    g["parcel_uid"] = g["cell_id"]  # the extraction/feature code keys on parcel_uid
    return g
