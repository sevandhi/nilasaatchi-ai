"""T3.5 satellite chips: ``satellite_chip(parcel_uid, date)`` -> true-colour + NDVI PNGs.

* 256 x 256 PNG around the parcel (square window = max(2.5 x parcel extent, 400 m), clipped to the AOI
  grid), 10 m pixels upsampled with nearest-neighbour so each Sentinel-2 pixel stays visible.
* True colour: B04/B03/B02 with a per-band 2-98 % stretch over SCL-valid pixels in the window.
* NDVI: diverging brown-white-green palette on [-0.1, 0.9]; SCL-invalid pixels (cloud, shadow, cirrus)
  drawn grey.
* Parcel outline drawn (yellow on true colour, black on NDVI).
* PNG text metadata: scene_id, date, parcel_uid, asset hrefs, stretch, caveats — every chip traces back
  to its source scene. Cached in ``data/s2/chips/<parcel>/<date>_<kind>.png``.

Chips are PUBLIC (satellite imagery + GIS outline, no owner data) and may go to a visual teacher.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date as Date
from pathlib import Path
from typing import Any

import numpy as np

from planet import aoi
from planet.extract import parcel_stats as ps

CHIP_DIR = ps.CACHE_DIR / "chips"
SIZE = 256
MIN_SIDE_M = 400.0
CHIP_VERSION = "chip-v1"
CAVEATS = ("signal needing field verification", "clouds/haze may remain after SCL masking",
           "10 m pixels mix land cover at parcel edges")
# diverging NDVI palette (value, RGB)
NDVI_STOPS = ((-0.1, (140, 81, 10)), (0.1, (216, 179, 101)), (0.25, (246, 232, 195)),
              (0.35, (199, 234, 229)), (0.55, (90, 180, 172)), (0.9, (1, 102, 94)))
INVALID_RGB = (160, 160, 160)


def safe_name(uid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", uid.replace("|", "__").replace("/", "-"))


def ndvi_rgb(ndvi: np.ndarray, valid: np.ndarray) -> np.ndarray:
    xs = np.array([s[0] for s in NDVI_STOPS])
    cols = np.array([s[1] for s in NDVI_STOPS], dtype=float)
    v = np.clip(np.nan_to_num(ndvi, nan=xs[0]), xs[0], xs[-1])
    rgb = np.stack([np.interp(v, xs, cols[:, k]) for k in range(3)], axis=-1)
    rgb[~valid | ~np.isfinite(ndvi)] = INVALID_RGB
    return rgb.astype(np.uint8)


def stretch(band: np.ndarray, valid: np.ndarray, lo: float = 2, hi: float = 98) -> tuple[np.ndarray, tuple[float, float]]:
    vals = band[valid & np.isfinite(band)]
    if vals.size < 10:
        vals = band[np.isfinite(band)]
    if vals.size == 0:
        return np.zeros(band.shape, dtype=np.uint8), (0.0, 0.0)
    a, b = np.percentile(vals, [lo, hi])
    b = b if b > a else a + 1e-6
    out = np.clip((np.nan_to_num(band, nan=a) - a) / (b - a), 0, 1) * 255
    return out.astype(np.uint8), (float(a), float(b))


def chip_window(geom_utm: Any, grid: ps.Grid, min_side_m: float = MIN_SIDE_M) -> tuple[int, int, int, int]:
    """(row0, col0, rows, cols) square pixel window centred on the parcel, clipped to the grid."""
    minx, miny, maxx, maxy = geom_utm.bounds
    side = max(2.5 * max(maxx - minx, maxy - miny), min_side_m)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    t = grid.transform
    npx = int(np.ceil(side / t.a))
    c0 = int(np.floor((cx - t.c) / t.a - npx / 2))
    r0 = int(np.floor((t.f - cy) / -t.e - npx / 2))
    c0 = min(max(c0, 0), max(grid.width - npx, 0))
    r0 = min(max(r0, 0), max(grid.height - npx, 0))
    return r0, c0, min(npx, grid.height), min(npx, grid.width)


def _outline_px(geom_utm: Any, grid: ps.Grid, win: tuple[int, int, int, int], size: int) -> list[list[tuple[float, float]]]:
    r0, c0, nr, nc = win
    t = grid.transform
    rings = []
    polys = getattr(geom_utm, "geoms", [geom_utm])
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)
            col = (xy[:, 0] - t.c) / t.a - c0
            row = (xy[:, 1] - t.f) / t.e - r0
            rings.append([(float(c * size / nc), float(r * size / nr)) for c, r in zip(col, row)])
    return rings


def render_chips(dn: Mapping[str, np.ndarray], grid: ps.Grid, geom_utm: Any, meta: Mapping[str, Any],
                 out_dir: Path, stem: str, dn_offset: int = 0, size: int = SIZE,
                 kinds: Sequence[str] = ("truecolor", "ndvi"),
                 stretch_override: Mapping[str, tuple[float, float]] | None = None,
                 gamma: float = 1.0) -> dict[str, Any]:
    """Render and write chips from raw DN arrays on ``grid``. Returns {kind: path, "meta": {...}}.
    ``stretch_override`` = {band: (lo, hi) reflectance} gives several dates one common stretch."""
    from PIL import Image, ImageDraw
    from PIL.PngImagePlugin import PngInfo

    win = chip_window(geom_utm, grid)
    r0, c0, nr, nc = win
    sl = (slice(r0, r0 + nr), slice(c0, c0 + nc))
    refl = {b: ps.reflectance(dn[b][sl], dn_offset) for b in ("red", "green", "blue", "nir") if b in dn}
    valid = ps.valid_mask(dn["scl"][sl])
    rings = _outline_px(geom_utm, grid, win, size)
    info = {**meta, "chip_version": CHIP_VERSION, "window_px": [r0, c0, nr, nc], "pixel_m": grid.transform.a,
            "crs": grid.crs, "valid_frac_window": round(float(valid.mean()), 3), "caveats": list(CAVEATS)}
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    for kind in kinds:
        if kind == "truecolor":
            chans, st = [], {}
            for b in ("red", "green", "blue"):
                if stretch_override is not None:
                    lo, hi = stretch_override[b]
                    c = np.clip((np.nan_to_num(refl[b], nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma
                    c = (c * 255).astype("uint8")
                    rng = (lo, hi)
                else:
                    c, rng = stretch(refl[b], valid)
                chans.append(c)
                st[b] = [round(float(rng[0]), 4), round(float(rng[1]), 4)]
            arr = np.stack(chans, axis=-1)
            how = "common stretch across the panel dates" if stretch_override is not None else "2-98% per band over SCL-valid pixels"
            colour, extra = (255, 255, 0), {"stretch": how, "stretch_refl": st, "gamma": gamma}
        elif kind == "ndvi":
            nd = ps.compute_index("ndvi", refl)
            arr = ndvi_rgb(nd, valid)
            colour, extra = (0, 0, 0), {"palette": "diverging brown-white-green [-0.1, 0.9]; grey = SCL invalid"}
        else:
            raise ValueError(kind)
        img = Image.fromarray(arr, "RGB").resize((size, size), Image.NEAREST)
        draw = ImageDraw.Draw(img)
        for ring in rings:
            draw.line(ring, fill=colour, width=2)
        png = PngInfo()
        m = {**info, **extra, "kind": kind}
        for k in ("scene_id", "date", "parcel_uid"):
            png.add_text(k, str(m.get(k, "")))
        png.add_text("nilasaatchi", json.dumps(m, default=str))
        path = out_dir / f"{stem}_{kind}.png"
        tmp = path.with_name(path.name + ".tmp")
        img.save(tmp, format="PNG", pnginfo=png)
        tmp.replace(path)
        result[kind] = path
    result["meta"] = info
    return result


# --------------------------------------------------------------------------- DB-backed tool
def _scene_for(conn: Any, uid: str, day: Date, max_days: int) -> Any:
    """Exact-date scene if the parcel has a usable obs on it, else the nearest usable one within ``max_days``."""
    row = conn.execute(
        "SELECT o.scene_id, abs(o.date - %s::date) AS dd FROM parcel_obs o WHERE o.parcel_uid = %s "
        "AND abs(o.date - %s::date) <= %s AND o.valid_frac >= 0.5 AND o.n_px >= 1 "
        "ORDER BY dd, o.valid_frac DESC LIMIT 1", (day, uid, day, max_days)).fetchone()
    if row is None:
        raise LookupError(f"no usable scene for {uid} within {max_days} d of {day}")
    return row[0]


def satellite_chip(parcel_uid: str, date: str | Date, *, max_days: int = 16, refresh: bool = False,
                   conn: Any = None) -> dict[str, Any]:
    """Tool entry point: true-colour + NDVI chips for ``parcel_uid`` on (or nearest usable scene to) ``date``.

    Returns ``{"truecolor": path, "ndvi": path, "scene_id", "date", "requested_date", "meta"}``.
    """
    import pandas as pd

    from planet.extract.run import dsn, load_scenes, signed

    day = pd.Timestamp(date).date()
    own = conn is None
    if own:
        import psycopg

        conn = psycopg.connect(dsn())
    try:
        scene_id = _scene_for(conn, parcel_uid, day, max_days)
        recs = {r.id: r for r in load_scenes(conn, min_valid=0.0)}
    finally:
        if own:
            conn.close()
    rec = recs[scene_id]
    out_dir = CHIP_DIR / safe_name(parcel_uid)
    stem = rec.date.isoformat()
    paths = {k: out_dir / f"{stem}_{k}.png" for k in ("truecolor", "ndvi")}
    base = {"scene_id": rec.id, "date": rec.date.isoformat(), "requested_date": str(day), "parcel_uid": parcel_uid}
    if not refresh and all(p.exists() for p in paths.values()):
        from PIL import Image

        meta = json.loads(Image.open(paths["truecolor"]).text.get("nilasaatchi", "{}"))
        return {**paths, **base, "meta": meta, "cached": True}
    grid = aoi.aoi_grid()
    parcels = aoi.load_parcels_uid()
    g = parcels[parcels.parcel_uid == parcel_uid]
    if g.empty:
        raise KeyError(parcel_uid)
    geom = g.to_crs(grid.crs).geometry.iloc[0]
    dn = ps.read_scene(signed(rec), grid, bands=ps.CACHE_BANDS)
    meta = {**base, "hrefs": {b: rec.hrefs.get(b) for b in ("red", "green", "blue", "nir", "scl")},
            "dn_offset": rec.dn_offset, "baseline": rec.processing_baseline, "source": rec.source}
    res = render_chips(dn, grid, geom, meta, out_dir, stem, rec.dn_offset)
    return {**{k: res[k] for k in ("truecolor", "ndvi")}, **base, "meta": res["meta"], "cached": False}


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="render satellite chips")
    ap.add_argument("parcel_uid", nargs="+")
    ap.add_argument("--date", action="append", required=True)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    for uid in a.parcel_uid:
        for d in a.date:
            r = satellite_chip(uid, d, refresh=a.refresh)
            print(f"{uid} {d} -> {r['scene_id']} ({r['date']}) {r['truecolor']} {r['ndvi']}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
