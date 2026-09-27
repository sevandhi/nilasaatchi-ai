"""ESA WorldCover 2021 (v200) class fractions per parcel -> ``data/s2/worldcover_fractions.parquet``.

Weak-label priors for P3 (valid for the 2021 seasons only). Source order:
1. the data-engineer's clipped COG under ``data/raster/`` (any file matching ``*worldcover*.tif``), else
2. the ESA COGs read directly from the anonymous S3 bucket: tiles N06E075 **and** N06E078 (3 deg tiles;
   the park straddles 78 E). Both share one EPSG:4326 lattice (1/12000 deg), so they mosaic exactly.

Geometry: each FMB parcel is buffered -5 m in EPSG:32643 (original kept when the buffer empties it),
then projected to the raster CRS. Fractions are area-weighted by sub-sampling every raster pixel
``SUPERSAMPLE`` x ``SUPERSAMPLE`` times, so small parcels are not all-or-nothing. ``n_px`` counts
native-pixel equivalents; ``low_support`` below 10.

Caveats: WorldCover is a 2021 single-product map with ~75% overall accuracy; cropland vs grassland vs
shrub confusion is common in semi-arid Tamil Nadu, and 10 m pixels mix at parcel edges. These are
weak priors needing field verification, never labels in their own right.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from planet import aoi
from planet.extract import parcel_stats as ps

log = logging.getLogger("planet.extract.worldcover")

WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
WC_TILES = ("N06E075", "N06E078")
WC_VERSION = "ESA WorldCover 10m 2021 v200"
RASTER_DIR = aoi.REPO / "data" / "raster"
OUT_PATH = ps.CACHE_DIR / "worldcover_fractions.parquet"
SUPERSAMPLE = 4
LOW_SUPPORT_PX = ps.LOW_SUPPORT_PX

CLASSES: dict[int, str] = {10: "tree", 20: "shrub", 30: "grass", 40: "cropland", 50: "built", 60: "bare",
                           70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}
# Weak-label mapping (phase3 skill T3.4). Shrub -> perennial_veg (Prosopis scrub) and wetland -> water
# are project assumptions to be checked in the qa audit.
STATE_OF: dict[str, str] = {"tree": "perennial_veg", "shrub": "perennial_veg", "mangrove": "perennial_veg",
                            "cropland": "cropped", "built": "cleared_or_built", "water": "water",
                            "wetland": "water", "grass": "bare_fallow", "bare": "bare_fallow",
                            "snow": "bare_fallow", "moss": "bare_fallow"}
CAVEATS = ("worldcover_2021_only", "mixed_pixels_at_edges", "crop_grass_shrub_confusion", "weak_prior_not_label")


def find_sources(raster_dir: Path = RASTER_DIR) -> list[str]:
    local = sorted(p for p in raster_dir.glob("**/*.tif") if "worldcover" in p.name.lower()) if raster_dir.exists() else []
    if local:
        return [str(local[0])]
    return [WC_URL.format(tile=t) for t in WC_TILES]


def read_mosaic(sources: Sequence[str], bounds_lonlat: Sequence[float], pad_px: int = 4) -> tuple[np.ndarray, Any, str]:
    """Read ``bounds_lonlat`` from every source onto the first source's pixel lattice and mosaic
    (first non-zero wins; 0 = WorldCover nodata). Sources must share CRS and resolution."""
    import rasterio
    from affine import Affine
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.Env(**ps.GDAL_ENV), rasterio.open(sources[0]) as ds0:
        crs, t0 = ds0.crs, ds0.transform
    b = transform_bounds("EPSG:4326", crs, *bounds_lonlat, densify_pts=21)
    rx, ry = t0.a, -t0.e
    col0 = math.floor((b[0] - t0.c) / rx) - pad_px
    col1 = math.ceil((b[2] - t0.c) / rx) + pad_px
    row0 = math.floor((t0.f - b[3]) / ry) - pad_px
    row1 = math.ceil((t0.f - b[1]) / ry) + pad_px
    transform = Affine(rx, 0.0, t0.c + col0 * rx, 0.0, -ry, t0.f - row0 * ry)
    shape = (row1 - row0, col1 - col0)
    left, top = transform.c, transform.f
    bounds = (left, top - shape[0] * ry, left + shape[1] * rx, top)
    out = np.zeros(shape, dtype="uint8")
    for src in sources:
        with rasterio.Env(**ps.GDAL_ENV), rasterio.open(src) as ds:
            if ds.crs != crs or not (math.isclose(ds.transform.a, rx) and math.isclose(-ds.transform.e, ry)):
                raise ValueError(f"{src}: CRS/resolution differs from {sources[0]}")
            win = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
            arr = ds.read(1, window=win, out_shape=shape, boundless=True, fill_value=0)
        out = np.where(out == 0, arr, out)
    return out, transform, str(crs)


def parcel_fractions(parcels: Any, classes: np.ndarray, transform: Any, crs: str,
                     supersample: int = SUPERSAMPLE) -> Any:
    """Area-weighted class fractions per parcel geometry (already in ``crs``).

    Returns a DataFrame indexed like ``parcels`` with ``n_px`` (native-pixel equivalents), ``nodata_frac``
    and ``frac_<class>`` for every WorldCover class (fractions over valid, non-zero pixels)."""
    import pandas as pd
    from affine import Affine
    from rasterio.features import geometry_mask

    k = int(supersample)
    sub = np.repeat(np.repeat(classes, k, axis=0), k, axis=1) if k > 1 else classes
    st = transform @ Affine.scale(1.0 / k)
    inv = ~st
    H, W = sub.shape
    codes = np.array(sorted(CLASSES), dtype=int)
    rows = []
    for geom in parcels.geometry:
        rec = {"n_px": 0.0, "nodata_frac": np.nan, **{f"frac_{CLASSES[c]}": np.nan for c in codes}}
        if geom is None or geom.is_empty:
            rows.append(rec)
            continue
        minx, miny, maxx, maxy = geom.bounds
        fc0, fr0 = inv @ (minx, maxy)
        fc1, fr1 = inv @ (maxx, miny)
        r0, c0 = max(math.floor(min(fr0, fr1)), 0), max(math.floor(min(fc0, fc1)), 0)
        r1, c1 = min(math.ceil(max(fr0, fr1)), H), min(math.ceil(max(fc0, fc1)), W)
        if r1 <= r0 or c1 <= c0:
            rows.append(rec)
            continue
        wt = st @ Affine.translation(c0, r0)
        inside = geometry_mask([geom], out_shape=(r1 - r0, c1 - c0), transform=wt, invert=True, all_touched=False)
        vals = sub[r0:r1, c0:c1][inside]
        n = vals.size
        good = vals[vals > 0]
        rec["n_px"] = n / (k * k)
        rec["nodata_frac"] = (1 - good.size / n) if n else np.nan
        if good.size:
            cnt = np.bincount(good, minlength=101)
            for c in codes:
                rec[f"frac_{CLASSES[c]}"] = cnt[c] / good.size
        rows.append(rec)
    return pd.DataFrame(rows, index=parcels.index)


def weak_labels(fr: Any) -> Any:
    """Aggregate class fractions into P3 states; ``weak_label`` = argmax state, ``weak_label_frac`` its share."""
    import pandas as pd

    states = sorted(set(STATE_OF.values()))
    agg = pd.DataFrame({s: sum(fr[f"frac_{c}"].fillna(0) for c, st in STATE_OF.items() if st == s) for s in states},
                       index=fr.index)
    has = fr[[f"frac_{c}" for c in CLASSES.values()]].notna().any(axis=1)
    label = agg.idxmax(axis=1).where(has)
    frac = agg.max(axis=1).where(has)
    maj = fr[[f"frac_{c}" for c in CLASSES.values()]].fillna(-1).idxmax(axis=1).str.replace("frac_", "").where(has)
    return pd.DataFrame({"majority_class": maj, "weak_label": label, "weak_label_frac": frac.round(4),
                         **{f"state_{s}": agg[s].round(4) for s in states}}, index=fr.index)


def compute(sources: Sequence[str] | None = None, buffer_m: float = ps.DEFAULT_BUFFER_M,
            supersample: int = SUPERSAMPLE, limit: int | None = None) -> Any:
    import geopandas as gpd

    sources = list(sources or find_sources())
    parcels = aoi.load_parcels_uid()
    if limit:
        parcels = parcels.head(limit)
    utm = ps.prepare_parcels(parcels, f"EPSG:{aoi.UTM_EPSG}", buffer_m)
    area = utm.geometry.area
    bounds = gpd.GeoSeries(utm.geometry, crs=utm.crs).to_crs("EPSG:4326").total_bounds
    classes, transform, crs = read_mosaic(sources, bounds)
    geo = utm.to_crs(crs)
    fr = parcel_fractions(geo, classes, transform, crs, supersample)
    out = parcels[["parcel_uid", "vil_name", "KIDE", "unit_id", "block_id"]].rename(
        columns={"vil_name": "village", "KIDE": "kide"}).copy()
    out["buffered"] = utm["buffered"].to_numpy()
    out["area_m2"] = area.round(1).to_numpy()
    out = out.join(fr)
    out["n_px"] = out["n_px"].round(2)
    out["low_support"] = out["n_px"] < LOW_SUPPORT_PX
    out = out.join(weak_labels(fr))
    out["buffer_m"] = buffer_m
    out["wc_version"] = WC_VERSION
    out["wc_source"] = ";".join(sources)
    out["caveats"] = ",".join(CAVEATS)
    for c in out.columns:
        if c.startswith("frac_") or c == "nodata_frac":
            out[c] = out[c].astype(float).round(4)
    return out.reset_index(drop=True)


def summarise(df: Any) -> Mapping[str, Any]:
    fr_cols = [f"frac_{c}" for c in CLASSES.values()]
    w = df["area_m2"]
    area_share = {c.replace("frac_", ""): round(float((df[c].fillna(0) * w).sum() / w.sum()), 4) for c in fr_cols}
    return {
        "parcels": len(df),
        "low_support": int(df.low_support.sum()),
        "unbuffered_fallback": int((~df.buffered).sum()),
        "area_weighted_class_share": {k: v for k, v in area_share.items() if v > 0},
        "majority_class_counts": df.majority_class.value_counts().to_dict(),
        "weak_label_counts": df.weak_label.value_counts().to_dict(),
        "weak_label_frac_ge_0.6": int((df.weak_label_frac >= 0.6).sum()),
        "weak_label_by_village": df.groupby("village").weak_label.value_counts().unstack(fill_value=0).to_dict("index"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WorldCover 2021 class fractions per parcel")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="recompute even if the parquet exists")
    ap.add_argument("--source", action="append", help="override raster source(s)")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    out_path = OUT_PATH if not args.limit else OUT_PATH.with_name("worldcover_fractions.limit.parquet")
    from planet import tabio

    fmb_mtime = aoi.FMB_PATH.stat().st_mtime
    cached = tabio.existing_table(out_path)
    if cached and not args.refresh and not args.source and cached.stat().st_mtime > fmb_mtime:
        df = tabio.read_table(out_path)
        print(f"WorldCover fractions: cached {cached.relative_to(aoi.REPO)} ({len(df)} parcels)")
    else:
        df = compute(args.source, limit=args.limit)
        written = tabio.write_table(df, out_path)
        print(f"WorldCover fractions -> {written.relative_to(aoi.REPO)} ({len(df)} parcels, "
              f"{time.monotonic() - t0:.0f} s) from {df.wc_source.iloc[0]}")
    import json

    print(json.dumps(summarise(df), indent=1, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
