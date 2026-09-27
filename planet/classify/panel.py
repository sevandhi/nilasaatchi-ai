"""Teacher / audit panel for one parcel-season: 3 true-colour chips (pre, peak, dry) + 1 NDVI chip (peak)
over a smoothed NDVI time-series plot, composed into one PUBLIC PNG (no owner data, no parcel id).

Layout (1024 x 640): row 1 = four 256x256 chips with captions; row 2 = 24-month NDVI plot ending 3 months
after the season, target season shaded, chip dates marked, raw usable obs as dots, unsupported (long-gap)
stretches of the smoothed curve drawn light grey. A header line gives the season and summary numbers.

Chip dates are picked from the parcel's *usable* obs (valid_frac >= 0.5, n_px >= 10 or mixed-pixel):
- pre  = usable obs nearest the season start, searched in [start - 30 d, start + 20 d] (else first obs in window)
- peak = usable obs nearest the smoothed-NDVI maximum over supported days in the window
- dry  = usable obs nearest the smoothed minimum after the peak (to window end + 15 d)
The panel's provenance (scene ids, hrefs, dates) is returned and stored with every label.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from planet import aoi
from planet.chips import render as cr
from planet.extract import parcel_stats as ps
from planet.features import seasons as se

PANEL_DIR = ps.CACHE_DIR / "panels"
PANEL_VERSION = "panel-v1"
PANEL_VERSIONS = {"v1": "panel-v1", "v2": "panel-v2"}
# panel-v2 (Cohere v4 teacher): strong season band with labelled edges, neutral chip markers A/B/C (v1 labelled
# the in-season NDVI-high chip "peak", which a model read as "the curve peaks inside the band")
GAMMA = 0.6  # common linear stretch across dates, then gamma so dense (dark) vegetation keeps texture
W, CHIP = 1024, 256
HEAD_H, CAP_H, PLOT_H = 28, 22, 330
H = HEAD_H + CAP_H + CHIP + PLOT_H
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _font(size: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


@dataclass
class Context:
    """Preloaded inputs shared by all panels of a run."""
    obs: pd.DataFrame
    smoothed: pd.DataFrame
    feats: pd.DataFrame
    grid: Any = None
    geoms: dict[str, Any] = field(default_factory=dict)
    scenes: dict[str, Any] = field(default_factory=dict)
    clarity: dict[str, float] = field(default_factory=dict)
    cfg: se.SeasonConfig = field(default_factory=se.load_config)

    @classmethod
    def load(cls, conn: Any = None) -> Context:
        from planet.extract.run import OBS_PARQUET, dsn, load_scenes
        from planet.features.build import FEATURES_PARQUET, SMOOTHED_PARQUET

        obs = pd.read_parquet(OBS_PARQUET)
        obs["date"] = pd.to_datetime(obs["date"])
        obs = obs[(obs.valid_frac >= 0.5) & ((obs.n_px >= 10) | (obs.mixed_pixel & (obs.n_px >= 1)))]
        sm = pd.read_parquet(SMOOTHED_PARQUET)
        sm["date"] = pd.to_datetime(sm["date"])
        feats = pd.read_parquet(FEATURES_PARQUET)
        grid = aoi.aoi_grid()
        parcels = aoi.load_parcels_uid().to_crs(grid.crs)
        geoms = dict(zip(parcels.parcel_uid, parcels.geometry, strict=True))
        import psycopg

        own = conn is None
        conn = conn or psycopg.connect(dsn())
        try:
            scenes = {r.id: r for r in load_scenes(conn, min_valid=0.0)}
            clarity = dict(conn.execute("SELECT id, coalesce(aoi_valid_frac, 0) FROM s2_scene").fetchall())
        finally:
            if own:
                conn.close()
        return cls(obs=obs, smoothed=sm, feats=feats, grid=grid, geoms=geoms, scenes=scenes,
                   clarity={k: float(v) for k, v in clarity.items()})


def pick_dates(obs: pd.DataFrame, sm: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp,
               clarity: dict[str, float] | None = None, tol_days: int = 12) -> dict[str, Any]:
    """Chip dates (``pre``, ``peak``, ``dry``) -> obs rows; pure function of the parcel's obs + smoothed series.
    Among obs within ``tol_days`` of each target date, the clearest scene (``clarity[scene_id]`` = AOI valid
    fraction) wins, so the chip context is less cloudy; ties go to the nearest date."""
    o = obs.sort_values("date")
    inwin = o[(o.date >= a - pd.Timedelta(days=30)) & (o.date <= b + pd.Timedelta(days=15))]
    if inwin.empty:
        raise LookupError("no usable obs near the season")

    def nearest(t: pd.Timestamp, pool: pd.DataFrame) -> pd.Series:
        dd = np.abs((pool.date - t).dt.days.to_numpy())
        if clarity:
            near = dd <= max(tol_days, int(dd.min()))
            cl = pool.scene_id.map(clarity).fillna(0).to_numpy()
            key = np.where(near, -cl * 1000 + dd, np.inf)
            return pool.iloc[int(np.argmin(key))]
        return pool.iloc[int(np.argmin(dd))]

    pre_pool = o[(o.date >= a - pd.Timedelta(days=30)) & (o.date <= a + pd.Timedelta(days=20))]
    pre = nearest(a, pre_pool) if len(pre_pool) else inwin.iloc[0]
    s = sm[(sm.date >= a) & (sm.date <= b) & sm.supported]
    win_obs = o[(o.date >= a) & (o.date <= b)]
    if s.empty or win_obs.empty:
        peak = inwin.loc[inwin.ndvi.idxmax()]
        t_peak = peak.date
    else:
        t_peak = s.date.iloc[int(np.argmax(s.ndvi_s.to_numpy()))]
        peak = nearest(t_peak, win_obs)
    after = sm[(sm.date > t_peak) & (sm.date <= b + pd.Timedelta(days=15)) & sm.supported]
    dry_pool = o[(o.date > peak.date) & (o.date <= b + pd.Timedelta(days=15))]
    if len(after) and len(dry_pool):
        dry = nearest(after.date.iloc[int(np.argmin(after.ndvi_s.to_numpy()))], dry_pool)
    elif len(dry_pool):
        dry = dry_pool.iloc[-1]
    else:
        dry = inwin.iloc[-1]
    return {"pre": pre, "peak": peak, "dry": dry}


def joint_stretch(dns: dict[str, dict[str, np.ndarray]], offsets: dict[str, int], geom: Any, grid: Any,
                  lo: float = 2, hi: float = 98) -> dict[str, tuple[float, float]]:
    """Per-band 2-98% reflectance range over the SCL-valid chip pixels of all panel dates together."""
    r0, c0, nr, nc = cr.chip_window(geom, grid)
    sl = (slice(r0, r0 + nr), slice(c0, c0 + nc))
    out = {}
    for b in ("red", "green", "blue"):
        vals = []
        for k, dn in dns.items():
            v = ps.valid_mask(dn["scl"][sl])
            r = ps.reflectance(dn[b][sl], offsets[k])
            vals.append(r[v & np.isfinite(r)])
        allv = np.concatenate(vals) if vals else np.empty(0)
        out[b] = (float(np.percentile(allv, lo)), float(np.percentile(allv, hi))) if allv.size >= 10 else (0.0, 0.3)
    return out


def _plot(img: Any, top: int, sm: pd.DataFrame, obs: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp,
          marks: dict[str, pd.Timestamp], style: str = "v1") -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    f = _font(12)
    x0, x1, y0, y1 = 48, W - 12, top + 26, top + PLOT_H - 30
    t1 = (b + pd.offsets.MonthBegin(1) + pd.DateOffset(months=3)).normalize()
    t0 = t1 - pd.DateOffset(months=24)
    span = (t1 - t0).days

    def X(t: pd.Timestamp) -> float:
        return x0 + (x1 - x0) * (t - t0).days / span

    def Y(v: float) -> float:
        return y1 - (y1 - y0) * (np.clip(v, -0.1, 0.9) + 0.1) / 1.0

    if style == "v2":
        d.rectangle([X(max(a, t0)), y0, X(min(b, t1)), y1], fill=(255, 214, 120))
        for t, lab in ((a, "season start"), (b, "season end")):
            if t0 <= t <= t1:
                d.line([X(t), y0, X(t), y1], fill=(170, 90, 0), width=3)
                d.text((X(t) - 30, y1 - 16), lab, fill=(120, 60, 0), font=f)
    else:
        d.rectangle([X(max(a, t0)), y0, X(min(b, t1)), y1], fill=(255, 243, 205))
    for v in (0.0, 0.2, 0.4, 0.6, 0.8):
        d.line([x0, Y(v), x1, Y(v)], fill=(215, 215, 215))
        d.text((6, Y(v) - 7), f"{v:.1f}", fill=(60, 60, 60), font=f)
    m = t0
    while m <= t1:
        d.line([X(m), y1, X(m), y1 + 4], fill=(90, 90, 90))
        if m.month in (1, 4, 7, 10):
            d.text((X(m) - 14, y1 + 6), m.strftime("%b %y"), fill=(40, 40, 40), font=f)
        m += pd.DateOffset(months=1)
    s = sm[(sm.date >= t0) & (sm.date <= t1)].sort_values("date")
    pts = list(zip([X(t) for t in s.date], [Y(v) for v in s.ndvi_s], s.supported, strict=True))
    for (xa, ya, sa), (xb, yb, sb) in itertools.pairwise(pts):
        d.line([xa, ya, xb, yb], fill=(0, 110, 40) if (sa and sb) else (190, 190, 190), width=3 if (sa and sb) else 1)
    oo = obs[(obs.date >= t0) & (obs.date <= t1)]
    for t, v in zip(oo.date, oo.ndvi, strict=True):
        cx, cy = X(t), Y(v)
        d.ellipse([cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5], outline=(20, 60, 160), width=1)
    for k, t in marks.items():
        d.line([X(t), y0, X(t), y1], fill=(200, 30, 30), width=1)
        d.text((X(t) + 2, y0 - 2), k, fill=(200, 30, 30), font=f)
    d.rectangle([x0, y0, x1, y1], outline=(80, 80, 80))
    legend = ("NDVI (green = smoothed, grey = long gap / unsupported, blue o = cloud-free obs, "
              + ("orange band = target season, red A/B/C = chip dates)" if style == "v2" else "shaded = target season)"))
    d.text((x0, top + 6), legend, fill=(30, 30, 30), font=f)


def render_panel(ctx: Context, parcel_uid: str, ag_year: int, season: str, out_path: Path,
                 refresh: bool = False, style: str = "v1") -> dict[str, Any]:
    """Render (or reuse) the panel PNG; returns provenance {path, sha256, dates, scene_ids, hrefs, ...}."""
    from PIL import Image, ImageDraw

    meta_path = out_path.with_suffix(".json")
    if out_path.exists() and meta_path.exists() and not refresh:
        return json.loads(meta_path.read_text())
    a, b = se.window(ag_year, season, ctx.cfg)
    obs = ctx.obs[ctx.obs.parcel_uid == parcel_uid]
    sm = ctx.smoothed[ctx.smoothed.parcel_uid == parcel_uid]
    picks = pick_dates(obs, sm, a, b, ctx.clarity)
    geom = ctx.geoms[parcel_uid]
    tiles, prov = {}, {}
    tmpdir = PANEL_DIR / "_chips" / cr.safe_name(parcel_uid)
    dns = {k: ps.read_scene(ctx.scenes[picks[k].scene_id], ctx.grid, bands=ps.CACHE_BANDS) for k in picks}
    st = joint_stretch(dns, {k: ctx.scenes[picks[k].scene_id].dn_offset for k in picks}, geom, ctx.grid)
    for key in ("pre", "peak", "dry"):
        row = picks[key]
        rec = ctx.scenes[row.scene_id]
        kinds = ("truecolor", "ndvi") if key == "peak" else ("truecolor",)
        res = cr.render_chips(dns[key], ctx.grid, geom, {"scene_id": rec.id, "date": rec.date.isoformat()},
                              tmpdir, f"{rec.date.isoformat()}_{key}", rec.dn_offset, kinds=kinds,
                              stretch_override=st, gamma=GAMMA)
        tiles[key] = Image.open(res["truecolor"]).convert("RGB")
        if key == "peak":
            tiles["ndvi"] = Image.open(res["ndvi"]).convert("RGB")
        prov[key] = {"date": rec.date.isoformat(), "scene_id": rec.id, "obs_ndvi": round(float(row.ndvi), 3),
                     "valid_frac": round(float(row.valid_frac), 3),
                     "hrefs": {bb: rec.hrefs.get(bb) for bb in ("red", "green", "blue", "nir", "scl")}}
    fr = ctx.feats[(ctx.feats.parcel_uid == parcel_uid) & (ctx.feats.ag_year == ag_year) & (ctx.feats.season == season)]
    fr = fr.iloc[0] if len(fr) else {}
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    hdr = (f"Target season: {season} {ag_year}-{(ag_year + 1) % 100:02d} ({a:%d %b %Y} - {b:%d %b %Y})   "
           f"NDVI max {fr.get('ndvi_max', float('nan')):.2f}  min {fr.get('ndvi_min', float('nan')):.2f}  "
           f"cloud-free obs {int(fr.get('n_obs', 0))}  max gap {int(fr.get('max_gap_days', 0))} d  "
           f"neighbour (300 m) median swing {fr.get('nbr_amp_med_300m', float('nan')):.2f}")
    d.text((6, 7), hdr, fill=(0, 0, 0), font=_font(12))
    if style == "v2":
        caps = [("pre", f"A pre-season  {prov['pre']['date']}"), ("peak", f"B in-season high  {prov['peak']['date']}"),
                ("dry", f"C after  {prov['dry']['date']}"), ("ndvi", f"NDVI at B  {prov['peak']['date']}")]
    else:
        caps = [("pre", f"pre-season  {prov['pre']['date']}"), ("peak", f"peak  {prov['peak']['date']}"),
                ("dry", f"dry / after  {prov['dry']['date']}"), ("ndvi", f"NDVI at peak  {prov['peak']['date']}")]
    for i, (k, cap) in enumerate(caps):
        img.paste(tiles[k].resize((CHIP, CHIP)), (i * CHIP, HEAD_H + CAP_H))
        d.text((i * CHIP + 6, HEAD_H + 3), cap, fill=(0, 0, 0), font=_font(13))
    for i in range(1, 4):
        d.line([i * CHIP, HEAD_H, i * CHIP, HEAD_H + CAP_H + CHIP], fill="white", width=2)
    names = ("A", "B", "C") if style == "v2" else ("pre", "peak", "dry")
    _plot(img, HEAD_H + CAP_H + CHIP, sm, obs, a, b,
          {names[0]: pd.Timestamp(prov["pre"]["date"]), names[1]: pd.Timestamp(prov["peak"]["date"]),
           names[2]: pd.Timestamp(prov["dry"]["date"])}, style=style)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    img.save(tmp, format="PNG")  # no text chunks: nothing identifying leaves in the image
    tmp.replace(out_path)
    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    meta = {"path": str(out_path), "sha256": sha, "panel_version": PANEL_VERSIONS[style], "parcel_uid": parcel_uid,
            "ag_year": int(ag_year), "season": season, "window": [a.date().isoformat(), b.date().isoformat()],
            "chips": prov, "scene_ids": sorted({p["scene_id"] for p in prov.values()}),
            "distinct_dates": len({p["date"] for p in prov.values()}), "stretch_refl": st,
            "caveats": list(cr.CAVEATS)}
    meta_path.write_text(json.dumps(meta, indent=1))
    return meta
