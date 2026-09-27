"""Print the smoothed NDVI series + season features for demo parcels, with an event date marked.

``python -m planet.features.demo [--parcel Melathattaparai|233 ...] [--event 2025-03-21]``
Reads the snapshots written by ``planet.features`` / ``planet.extract`` (no DB, no network).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

import numpy as np
import pandas as pd

from planet import tabio
from planet.extract import parcel_stats as ps
from planet.features import build as fb

DEFAULT_PARCELS = ("Melathattaparai|233", "Melathattaparai|235")


def bar(v: float, lo: float = 0.0, hi: float = 0.9, width: int = 30) -> str:
    if not np.isfinite(v):
        return ""
    n = round((min(max(v, lo), hi) - lo) / (hi - lo) * width)
    return "#" * n


def show(uid: str, ser: pd.DataFrame, obs: pd.DataFrame, feats: pd.DataFrame, event: pd.Timestamp,
         since: pd.Timestamp) -> None:
    s = ser[ser.parcel_uid == uid].copy()
    s["date"] = pd.to_datetime(s["date"])
    o = obs[(obs.parcel_uid == uid)].copy()
    o["date"] = pd.to_datetime(o["date"])
    o = o[fb.usable_mask(o, fb.se.load_config())]
    print(f"\n=== {uid}  (usable obs {len(o)}, first {o.date.min().date()}, last {o.date.max().date()}; "
          f"mode {obs[obs.parcel_uid == uid].geom_mode.iloc[0]}) — event {event.date()} marked '<<<'")
    s = s[s.date >= since]
    # semi-monthly samples of the smoothed series + raw obs counts in each half-month
    s["half"] = s.date.dt.to_period("M").astype(str) + np.where(s.date.dt.day <= 15, "a", "b")
    o["half"] = o.date.dt.to_period("M").astype(str) + np.where(o.date.dt.day <= 15, "a", "b")
    raw = o.groupby("half").ndvi.agg(["count", "median"])
    printed_event = False
    for half, g in s.groupby("half", sort=True):
        v = float(g.ndvi_s.mean())
        r = raw.loc[half] if half in raw.index else None
        mark = ""
        if not printed_event and g.date.max() >= event:
            mark, printed_event = "  <<< LDR handover", True
        obs_txt = f"obs {int(r['count'])} raw {r['median']:.2f}" if r is not None else "no obs      "
        print(f"{half:>9}  {v:5.2f} {bar(v):<30}  {obs_txt}{mark}")
    f = feats[(feats.parcel_uid == uid) & (feats.season != "annual")].copy()
    f = f[pd.to_datetime(f.ag_year.astype(str) + "-06-01") >= since - pd.DateOffset(months=12)]
    cols = ["ag_year", "season", "n_obs", "max_gap_days", "data_sufficient", "ndvi_max", "ndvi_min", "amplitude",
            "peaks_count", "peak_doy", "dry_mean", "nbr_amp_med_300m"]
    print(f[cols].round(3).to_string(index=False))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parcel", action="append")
    ap.add_argument("--event", default="2025-03-21")
    ap.add_argument("--since", default="2023-06-01")
    a = ap.parse_args(argv)
    ser = tabio.read_table(fb.SMOOTHED_PARQUET)
    obs = tabio.read_table(ps.CACHE_DIR / "parcel_obs.parquet")
    feats = tabio.read_table(fb.FEATURES_PARQUET)
    feats["caveats"] = feats.caveats.map(json.loads)
    for uid in a.parcel or DEFAULT_PARCELS:
        show(uid, ser, obs, feats, pd.Timestamp(a.event), pd.Timestamp(a.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
