"""Rule prior for parcel-season land-use states (T3.4, third voter next to the two VLM teachers).

Deterministic, feature-only rules on the T3.3 parcel-season rows, plus the ESA WorldCover 2021 weak prior
for the seasons that WorldCover actually observed (calendar 2021). Output per row:
``prior_state``, ``prior_src`` (``rules`` | ``worldcover``) and ``prior_reason`` (human-readable).

Region notes (Thoothukudi, SW-monsoon rain shadow; see config/seasons.yaml): rainfed crops are sown with
the NE monsoon (Oct-Nov) and harvested Jan-Feb, so nearly every vegetated parcel greens in *rabi*; a
parcel that does not green in rabi while its neighbours do is suspicious (cleared/built/bare). Off-season
(kharif/summer) green peaks need irrigation. Prosopis scrub stays green through the dry months.

These are weak priors - signals needing field verification - never labels on their own. The rule cannot
separate a rainfed crop from a NE-monsoon weed flush; that ambiguity is carried as a caveat.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RULES_VERSION = "rules-v1"
STATES = ("cropped", "irrigated_multi", "perennial_veg", "bare_fallow", "cleared_or_built", "water",
          "insufficient_data")
MODEL_STATES = STATES[:-1]  # the student never predicts insufficient_data (rule-gated)

# WorldCover 2021 observed calendar 2021: these parcel-seasons overlap it by >= 3 months.
WORLDCOVER_SEASONS = {(2020, "summer"), (2021, "kharif"), (2021, "rabi")}
WC_MIN_FRAC = 0.6
WC_STATIC = {"tree": "perennial_veg", "built": "cleared_or_built", "water": "water", "wetland": "water"}

T = {  # thresholds (NDVI units unless stated); chosen from the feature quantiles, not tuned on labels
    "water_ndwi": 0.0,
    "perennial_dry_mean": 0.38,
    "perennial_min": 0.30,
    "perennial_rabi_amp": 0.25,
    "crop_amp": 0.25,
    "crop_max": 0.45,
    "cleared_rabi_max": 0.30,
    "cleared_nbr_amp": 0.30,
    "cleared_yoy": -0.15,
}


def _f(row: pd.Series | dict, k: str) -> float:
    v = row.get(k) if hasattr(row, "get") else None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return v


def season_rule(r: pd.Series | dict) -> tuple[str, str]:
    """(state, reason) for one season row; ``irrigated_multi`` is decided per ag-year in :func:`apply`."""
    if not bool(r.get("data_sufficient", False)):
        return "insufficient_data", f"obs rule failed (n_obs {r.get('n_obs')}, max gap {r.get('max_gap_days')} d)"
    season = r.get("season")
    mx, mn, amp = _f(r, "ndvi_max"), _f(r, "ndvi_min"), _f(r, "amplitude")
    dry, ndwi, peaks = _f(r, "dry_mean"), _f(r, "ndwi_max"), int(r.get("peaks_count") or 0)
    nbr, yoy = _f(r, "nbr_amp_med_300m"), _f(r, "yoy_delta")
    if np.isnan(mx):
        return "insufficient_data", "no supported smoothed days in the window"
    if ndwi > T["water_ndwi"] and mx < 0.3:
        return "water", f"ndwi_max {ndwi:.2f} > {T['water_ndwi']} with ndvi_max {mx:.2f}"
    if season == "rabi":
        if mx < T["cleared_rabi_max"] and nbr >= T["cleared_nbr_amp"] and yoy <= T["cleared_yoy"]:
            return "cleared_or_built", (f"no NE-monsoon greening (ndvi_max {mx:.2f}) while neighbours swing "
                                        f"{nbr:.2f}; yoy {yoy:+.2f}")
        if dry >= T["perennial_dry_mean"] and amp < T["perennial_rabi_amp"]:
            return "perennial_veg", f"green baseline {dry:.2f} with small swing {amp:.2f}"
        if peaks >= 1 and amp >= T["crop_amp"]:
            return "cropped", f"rabi peak {mx:.2f}, amplitude {amp:.2f} (crop or weed flush)"
        return "bare_fallow", f"weak rabi greening (max {mx:.2f}, amp {amp:.2f})"
    # kharif / summer (dry seasons here)
    if peaks >= 1 and mx >= T["crop_max"] and amp >= 0.2:
        return "cropped", f"off-season peak {mx:.2f} (amp {amp:.2f}) - needs irrigation"
    if mn >= T["perennial_min"]:  # min, not mean: March still carries the senescing rabi crop
        return "perennial_veg", f"stays green in the dry season (min {mn:.2f}, mean {dry:.2f})"
    return "bare_fallow", f"dry-season low NDVI (max {mx:.2f}, amp {amp:.2f})"


def apply(feats: pd.DataFrame, worldcover: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add prior_state / prior_src / prior_reason to the season rows of ``feats`` (annual rows dropped)."""
    df = feats[feats.season != "annual"].copy()
    res = [season_rule(r) for r in df.to_dict("records")]
    df["prior_state"] = [s for s, _ in res]
    df["prior_reason"] = [w for _, w in res]
    df["prior_src"] = "rules"
    # irrigated_multi: >= 2 cropped seasons in the same ag-year (one of them necessarily off-season)
    crop = df.prior_state == "cropped"
    n_crop = crop.groupby([df.parcel_uid, df.ag_year]).transform("sum")
    multi = crop & (n_crop >= 2)
    df.loc[multi, "prior_state"] = "irrigated_multi"
    df.loc[multi, "prior_reason"] = df.loc[multi, "prior_reason"] + "; >= 2 cropped seasons this ag-year"
    if worldcover is not None and len(worldcover):
        wc = worldcover.set_index("parcel_uid")
        key = list(zip(df.ag_year, df.season, strict=True))
        in_wc = np.array([k in WORLDCOVER_SEASONS for k in key])
        cls = df.parcel_uid.map(wc["majority_class"])
        frac = df.parcel_uid.map(wc["weak_label_frac"]).astype(float)
        st = cls.map(WC_STATIC)
        use = in_wc & st.notna().to_numpy() & (frac >= WC_MIN_FRAC).to_numpy() & (df.prior_state != "insufficient_data")
        df.loc[use, "prior_state"] = st[use]
        df.loc[use, "prior_src"] = "worldcover"
        df.loc[use, "prior_reason"] = [f"WorldCover 2021 {c} {f:.0%}" for c, f in zip(cls[use], frac[use], strict=True)]
        df["wc_class"] = cls
        df["wc_frac"] = frac
    return df


def state_counts(df: pd.DataFrame, col: str = "prior_state") -> dict[str, Any]:
    return df.groupby("season")[col].value_counts().unstack(fill_value=0).to_dict("index")
