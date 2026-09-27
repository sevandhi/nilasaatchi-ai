"""dn_offset radiometry spot check (D-026): scenes stored with ``dn_offset = 1000`` against the nearest-date
``dn_offset = 0`` scene.

Checks, over SCL-valid pixels common to both scenes inside the park:
* raw-DN dark-object percentiles (p1) per band — a +1000 DN offset shows up as p1 ~ 1000 higher;
* per-parcel median NDVI with the recorded offset vs with no offset, compared with the neighbour scene
  (median absolute difference, Pearson r) over parcels with >= 10 valid px in both.

Verification rule (from the 2026-09-26 check): a harmonised L2A scene has dark objects (water, shadowed
vegetation) well below 1000 DN in blue; a scene that still carries the +1000 BOA offset cannot. So for a
scene flagged ``dn_offset = 1000`` the offset is confirmed only if the raw blue p1 over SCL-valid park
pixels is >= ``OFFSET_P1_BLUE_MIN``; otherwise it is reset to 0. ``--apply`` writes the verified value to
``s2_scene.dn_offset`` (+ evidence in ``meta.dn_offset_check``) and deletes the per-scene obs markers of
changed scenes so ``python -m planet.extract`` recomputes them from the cache.

``python -m planet.extract.dn_check [--n 3] [--apply]``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

import numpy as np

from planet import aoi
from planet.extract import parcel_stats as ps
from planet.extract import run as er

OFFSET_P1_BLUE_MIN = 1000.0


def verified_offset(flagged: int, radiometry: dict | None) -> tuple[int, str]:
    """(dn_offset, reason) for one scene from its raw-DN radiometry summary."""
    if not flagged:
        return 0, "not flagged"
    blue = (radiometry or {}).get("blue")
    if not blue:
        return flagged, "unverified (no radiometry)"
    p1 = float(blue["p1"])
    if p1 >= OFFSET_P1_BLUE_MIN:
        return flagged, f"confirmed: raw blue p1 {p1:.0f} DN >= {OFFSET_P1_BLUE_MIN:.0f}"
    return 0, f"reset: raw blue p1 {p1:.0f} DN < {OFFSET_P1_BLUE_MIN:.0f} (pixels already harmonised)"


def apply_verification(conn: object) -> list[dict]:
    from psycopg.types.json import Jsonb

    rows = conn.execute("SELECT id, dn_offset, meta FROM s2_scene WHERE dn_offset <> 0 "
                        "OR meta ? 'dn_offset_check'").fetchall()
    changes = []
    for sid, off, meta in rows:
        prev = (meta or {}).get("dn_offset_check", {})
        flagged = int(prev.get("flagged", off))
        rp = ps.CACHE_DIR / sid / "radiometry.json"
        rad = json.loads(rp.read_text()) if rp.exists() else None
        new, reason = verified_offset(flagged, rad)
        ev = {"flagged": flagged, "verified": new, "reason": reason, "rule": "blue_p1_ge_1000"}
        conn.execute("UPDATE s2_scene SET dn_offset = %s, meta = meta || %s WHERE id = %s",
                     (new, Jsonb({"dn_offset_check": ev}), sid))
        if new != off:
            marker = er.obs_path(sid)
            if marker.exists():
                marker.unlink()
        changes.append({"scene": sid, "was": off, "now": new, "reason": reason})
    conn.commit()
    return changes


def compare(rec_off: object, rec_ref: object, grid: ps.Grid, pix: er.ParcelPixels, park: np.ndarray) -> dict:
    a = ps.read_scene(er.signed(rec_off), grid)
    b = ps.read_scene(er.signed(rec_ref), grid)
    both = ps.valid_mask(a["scl"]) & ps.valid_mask(b["scl"]) & park
    res: dict = {"scene": rec_off.id, "ref": rec_ref.id, "days_apart": abs((rec_off.date - rec_ref.date).days),
                 "baseline": rec_off.processing_baseline, "ref_baseline": rec_ref.processing_baseline,
                 "common_valid_px": int(both.sum())}
    for band in ("blue", "red", "nir", "swir16"):
        res[f"p1_{band}"] = [float(np.percentile(a[band][both], 1)), float(np.percentile(b[band][both], 1))]
    ref = er.obs_from_arrays(b, rec_ref.dn_offset, pix).set_index("parcel_uid")
    for label, off in (("with_offset", rec_off.dn_offset), ("no_offset", 0)):
        o = er.obs_from_arrays(a, off, pix).set_index("parcel_uid")
        ok = (~o.low_support) & (~ref.low_support) & (~o.mixed_pixel)
        x, y = o.ndvi[ok].to_numpy(), ref.ndvi[ok].to_numpy()
        res[label] = {"parcels": int(ok.sum()), "median_ndvi": round(float(np.median(x)), 4),
                      "ref_median_ndvi": round(float(np.median(y)), 4),
                      "median_abs_diff": round(float(np.median(np.abs(x - y))), 4),
                      "pearson_r": round(float(np.corrcoef(x, y)[0, 1]), 4)}
    return res


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--max-days", type=int, default=20)
    ap.add_argument("--apply", action="store_true", help="write verified dn_offset to s2_scene")
    args = ap.parse_args(argv)
    import psycopg
    from rasterio.features import rasterize

    if args.apply:
        with psycopg.connect(er.dsn()) as conn:
            for c in apply_verification(conn):
                print(c)
        return 0
    with psycopg.connect(er.dsn()) as conn:
        recs = er.load_scenes(conn, er.MIN_AOI_VALID)
        vf = dict(conn.execute("SELECT id, aoi_valid_frac FROM s2_scene").fetchall())
    grid = aoi.aoi_grid()
    pix = er.parcel_pixels(aoi.load_parcels_uid(), grid)
    park = rasterize(((g, 1) for g in aoi.load_boundary().to_crs(grid.crs).geometry), out_shape=grid.shape,
                     transform=grid.transform, fill=0, dtype="uint8").astype(bool)
    off = [r for r in recs if r.dn_offset]
    zero = [r for r in recs if not r.dn_offset]
    print(f"dn_offset=1000 scenes in the aoi_valid_frac >= 0.2 set: {len(off)}")
    # most AOI-valid offset scenes first, each paired with the nearest offset-0 scene
    pairs = []
    for r in sorted(off, key=lambda r: -(vf.get(r.id) or 0)):
        near = min(zero, key=lambda z: (abs((z.date - r.date).days), -(vf.get(z.id) or 0)))
        if abs((near.date - r.date).days) <= args.max_days:
            pairs.append((r, near))
    out = [compare(r, z, grid, pix, park) for r, z in pairs[: args.n]]
    print(json.dumps(out, indent=1))
    (ps.CACHE_DIR / "dn_offset_check.json").write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
