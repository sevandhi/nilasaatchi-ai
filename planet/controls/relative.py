"""Control-relative evidence (D-035): ploughing signature, z-scores vs controls, difference-in-differences.

1. **Control features**: the control obs go through exactly the parcel feature code (``planet.features.build``,
   same usable-obs rule, same Whittaker lambda from ``whittaker_lambda.json``).
2. **Ploughing signature** per parcel/cell-season (only where the season has a green-up, amplitude >= 0.15):
   - green-up onset = first smoothed day in [season start - 30 d, peak] above base + 30 % of (peak - base),
     base = smoothed minimum in [season start - 60 d, peak];
   - pre-onset window = [onset - 42 d, onset - 14 d] (2-6 weeks before green-up);
   - BSI baseline = median usable-obs BSI in [onset - 120 d, onset - 42 d), noise = 1.4826 MAD (floor 0.01);
   - ``plough_signal`` = max over usable obs in the pre-onset window with NDVI <= base + 0.05 of
     (BSI - baseline) / noise, ``plough_date`` its date; present when >= PLOUGH_Z.
   Undefined (NaN) when there is no green-up or no usable obs in the window (reason kept).
3. **Relative metrics** per parcel-season: robust z = (x - median_controls) / (1.4826 MAD_controls) for
   amplitude, integral, peak offset (days from season start) and ndvi_max, against the controls of the
   same (ag_year, season); ``vigour_z`` = mean of the amplitude and integral z; control percentile ranks.
4. **DiD** per parcel and event (T_3(1), T_award, T_possession; fallback dates, planet/events):
   d_t = parcel metric - control mean for season-year t; DiD = mean_post d_t - mean_pre d_t. Seasons are pre
   or post by their midpoint. Metrics: ``vigour_z`` and ``plough`` (signal present 0/1, green-up seasons
   only); scopes ``all`` (kharif/rabi/summer) and ``rabi``. Bootstrap (B = 1000): controls resampled with
   replacement and pre/post season-years resampled within their sets; 95 % percentile CI.
   ``farmland_like_p`` (vigour_z only) = bootstrap share with post-period (parcel - control) >= -1, i.e. the
   parcel's post-event vigour is inside the control farmland range (not below ~1 robust SD).
All outputs are signals needing field verification (clouds, mixed pixels, weed flush, control mismatch).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

import numpy as np
import pandas as pd

from planet.extract import parcel_stats as ps
from planet.features import build as fb
from planet.features import seasons as se

CTRL_FEATURES = ps.CACHE_DIR / "controls" / "control_season_features.parquet"
CTRL_SMOOTHED = ps.CACHE_DIR / "controls" / "control_smoothed.parquet"
PLOUGH_PARQUET = ps.CACHE_DIR / "plough_signals.parquet"
REL_PARQUET = ps.CACHE_DIR / "parcel_season_relative.parquet"
DID_PARQUET = ps.CACHE_DIR / "parcel_did.parquet"
DID_VERSION = "did-v2"
PLOUGH_Z = 2.0
MIN_GREENUP_AMP = 0.15
FARMLIKE_Z = -1.0
FARMLIKE_P = 0.7
PLOUGH_Z_CLIP = 5.0
B = 1000
SEASONS = ("kharif", "rabi", "summer")
Z_METRICS = ("amplitude", "integral", "peak_offset", "ndvi_max")
FARMLIKE_MEANING = ("greenness only: post-event vigour inside the control farmland range; does NOT separate a crop "
                    "from a NE-monsoon weed flush")
CAVEATS = ["signal needing field verification", "controls are WorldCover-2021 cropland 2-6 km away (soil/irrigation may differ)",
           "clouds/haze and mixed pixels", "NE-monsoon weed flush also greens fallow land",
           "event dates are document-level fallbacks until P2", "few post-possession seasons (CI mostly reflects control spread)"]


# ------------------------------------------------------------------------------------------ ploughing
def plough_one(obs: pd.DataFrame, sm: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp) -> dict[str, Any]:
    """Ploughing signature for one season window [a, b]; ``obs`` = usable obs, ``sm`` = smoothed series."""
    res: dict[str, Any] = {"plough_signal": np.nan, "plough_date": None, "plough_delta": np.nan,
                           "greenup_onset": None, "plough_nobs": 0, "plough_reason": None}
    s = sm[(sm.date >= a - pd.Timedelta(days=60)) & (sm.date <= b)]
    win = s[(s.date >= a) & (s.date <= b) & s.supported]
    if win.empty:
        res["plough_reason"] = "no_supported_series"
        return res
    t_peak = win.date.iloc[int(np.argmax(win.ndvi_s.to_numpy()))]
    peak = float(win.ndvi_s.max())
    pre = s[s.date <= t_peak]
    base = float(pre.ndvi_s.min())
    if peak - base < MIN_GREENUP_AMP:
        res["plough_reason"] = "no_greenup"
        return res
    thr = base + 0.3 * (peak - base)
    asc = pre[(pre.date >= a - pd.Timedelta(days=30)) & (pre.ndvi_s >= thr)]
    if asc.empty:
        res["plough_reason"] = "no_onset"
        return res
    onset = asc.date.iloc[0]
    res["greenup_onset"] = onset.date().isoformat()
    bl = obs[(obs.date >= onset - pd.Timedelta(days=120)) & (obs.date < onset - pd.Timedelta(days=42))].bsi.dropna()
    w = obs[(obs.date >= onset - pd.Timedelta(days=42)) & (obs.date <= onset - pd.Timedelta(days=14))
            & (obs.ndvi <= base + 0.05)]
    res["plough_nobs"] = len(w)
    if len(bl) < 2:
        res["plough_reason"] = "no_baseline_obs"
        return res
    if w.empty:
        res["plough_reason"] = "no_obs_in_window"
        return res
    med = float(bl.median())
    noise = max(1.4826 * float((bl - med).abs().median()), 0.01)
    dz = (w.bsi.to_numpy() - med) / noise
    i = int(np.argmax(dz))
    res.update(plough_signal=float(dz[i]), plough_date=w.date.iloc[i].date().isoformat(),
               plough_delta=float(w.bsi.iloc[i] - med), plough_reason="ok")
    return res


def plough_all(obs: pd.DataFrame, sm: pd.DataFrame, feats: pd.DataFrame, key: str = "parcel_uid",
               cfg: se.SeasonConfig | None = None) -> pd.DataFrame:
    cfg = cfg or se.load_config()
    obs = obs.copy()
    obs["date"] = pd.to_datetime(obs["date"])
    obs = obs[fb.usable_mask(obs, cfg)]
    sm = sm.copy()
    sm["date"] = pd.to_datetime(sm["date"])
    og, sg = dict(tuple(obs.groupby(key))), dict(tuple(sm.groupby(key)))
    rows = []
    rowsf = feats[feats.season.isin(SEASONS) & feats.data_sufficient]
    for r in rowsf[[key, "ag_year", "season"]].itertuples(index=False):
        uid, y, s = r
        a, b = se.window(int(y), s, cfg)
        o, m = og.get(uid), sg.get(uid)
        if o is None or m is None:
            continue
        rows.append({key: uid, "ag_year": int(y), "season": s, **plough_one(o, m, a, b)})
    out = pd.DataFrame(rows)
    out["plough_present"] = np.where(out.plough_signal.notna(), (out.plough_signal >= PLOUGH_Z).astype(float), np.nan)
    return out


# -------------------------------------------------------------------------------------- relative z
def _peak_offset(df: pd.DataFrame, cfg: se.SeasonConfig) -> pd.Series:
    starts = {(y, s): se.window(int(y), s, cfg)[0] for y, s in set(zip(df.ag_year, df.season, strict=True))}
    out = []
    for y, s, doy in zip(df.ag_year, df.season, df.peak_doy, strict=True):
        a = starts[(y, s)]
        out.append(np.nan if pd.isna(doy) else float((int(doy) - a.dayofyear) % 366))
    return pd.Series(out, index=df.index)


def control_stats(ctrl: pd.DataFrame) -> pd.DataFrame:
    """Per (ag_year, season): robust centre/scale of each metric over the controls."""
    g = ctrl.groupby(["ag_year", "season"])
    parts = {}
    for m in Z_METRICS:
        med = g[m].median()
        mad = g[m].apply(lambda x: 1.4826 * float((x - x.median()).abs().median()))
        parts[f"{m}_med"] = med
        parts[f"{m}_scale"] = mad.clip(lower={"amplitude": 0.02, "integral": 1.0, "peak_offset": 5.0, "ndvi_max": 0.02}[m])
    parts["n_controls"] = g.size()
    return pd.DataFrame(parts)


def relative(df: pd.DataFrame, ctrl: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """z-scores + control percentile ranks for parcel (or control) season rows."""
    out = df.join(stats, on=["ag_year", "season"])
    cg = {k: v for k, v in ctrl.groupby(["ag_year", "season"])}
    for m in Z_METRICS:
        out[f"z_{m}"] = (out[m] - out[f"{m}_med"]) / out[f"{m}_scale"]
    out["vigour_z"] = out[["z_amplitude", "z_integral"]].mean(axis=1, skipna=False)
    pct = []
    for r in out[["ag_year", "season", "amplitude"]].itertuples(index=False):
        c = cg.get((r.ag_year, r.season))
        pct.append(np.nan if c is None or pd.isna(r.amplitude) else float((c.amplitude <= r.amplitude).mean()))
    out["pct_amplitude"] = pct
    return out


# --------------------------------------------------------------------------------------------- DiD
def _midpoints(keys: list[tuple[int, str]], cfg: se.SeasonConfig) -> dict[tuple[int, str], dt.date]:
    out = {}
    for y, s in keys:
        a, b = se.window(y, s, cfg)
        out[(y, s)] = (a + (b - a) / 2).date()
    return out


def did_group(Z: np.ndarray, C: np.ndarray, pre: np.ndarray, post: np.ndarray, rng: np.random.Generator,
              level_threshold: float | None) -> dict[str, np.ndarray]:
    """Vectorised DiD for parcels sharing one pre/post split.

    Z: (P, S) parcel metric; C: (K, S) control metric; pre/post: season index arrays."""
    cm = np.nanmean(C, axis=0)
    d = Z - cm[None, :]
    pre_d = np.nanmean(d[:, pre], axis=1) if len(pre) else np.full(len(Z), np.nan)
    post_d = np.nanmean(d[:, post], axis=1) if len(post) else np.full(len(Z), np.nan)
    did = post_d - pre_d
    boots = np.full((B, len(Z)), np.nan)
    lvl = np.full((B, len(Z)), np.nan)
    K = C.shape[0]
    for bi in range(B):
        cb = np.nanmean(C[rng.integers(0, K, K)], axis=0)
        pr = pre[rng.integers(0, len(pre), len(pre))] if len(pre) else pre
        po = post[rng.integers(0, len(post), len(post))] if len(post) else post
        db = Z - cb[None, :]
        with np.errstate(all="ignore"):
            a = np.nanmean(db[:, po], axis=1) if len(po) else np.nan
            b0 = np.nanmean(db[:, pr], axis=1) if len(pr) else np.nan
        boots[bi] = a - b0
        lvl[bi] = a
    with np.errstate(all="ignore"):
        lo, hi = np.nanpercentile(boots, [2.5, 97.5], axis=0)
        flp = np.nanmean(lvl >= level_threshold, axis=0) if level_threshold is not None else np.full(len(Z), np.nan)
    n_pre = np.sum(~np.isnan(Z[:, pre]), axis=1) if len(pre) else np.zeros(len(Z), int)
    n_post = np.sum(~np.isnan(Z[:, post]), axis=1) if len(post) else np.zeros(len(Z), int)
    flp = np.where(n_post > 0, flp, np.nan)
    return {"pre_diff": pre_d, "post_diff": post_d, "did": did, "ci_lo": lo, "ci_hi": hi, "farmland_like_p": flp,
            "n_pre": n_pre, "n_post": n_post}


def compute_did(prel: pd.DataFrame, crel: pd.DataFrame, fallbacks: dict[str, Any], seed: int = 7,
                cfg: se.SeasonConfig | None = None) -> pd.DataFrame:
    from planet.events import windows as ew

    cfg = cfg or se.load_config()
    rng = np.random.default_rng(seed)
    keys = sorted({(int(y), s) for y, s in zip(prel.ag_year, prel.season, strict=True)} &
                  {(int(y), s) for y, s in zip(crel.ag_year, crel.season, strict=True)})
    mids = _midpoints(keys, cfg)
    today = dt.datetime.now(dt.UTC).date()
    keys = [k for k in keys if se.window(k[0], k[1], cfg)[1].date() <= today or mids[k] <= today]
    col = {k: i for i, k in enumerate(keys)}
    uids = sorted(prel.parcel_uid.unique())
    cells = sorted(crel.cell_id.unique())
    out = []
    prel = prel.assign(plough_z=prel.plough_signal.clip(-PLOUGH_Z_CLIP, PLOUGH_Z_CLIP))
    crel = crel.assign(plough_z=crel.plough_signal.clip(-PLOUGH_Z_CLIP, PLOUGH_Z_CLIP))
    for metric, pcol in (("vigour_z", "vigour_z"), ("plough", "plough_present"), ("plough_z", "plough_z")):
        Zp = np.full((len(uids), len(keys)), np.nan)
        Zc = np.full((len(cells), len(keys)), np.nan)
        pi = {u: i for i, u in enumerate(uids)}
        ci = {c: i for i, c in enumerate(cells)}
        for r in prel[["parcel_uid", "ag_year", "season", pcol]].itertuples(index=False):
            k = (int(r.ag_year), r.season)
            if k in col:
                Zp[pi[r.parcel_uid], col[k]] = getattr(r, pcol)
        for r in crel[["cell_id", "ag_year", "season", pcol]].itertuples(index=False):
            k = (int(r.ag_year), r.season)
            if k in col:
                Zc[ci[r.cell_id], col[k]] = getattr(r, pcol)
        # parcels grouped by their event-date tuple (village/parcel fallbacks differ)
        evs = {u: ew.event_dates(u, fallbacks) for u in uids}
        for event in ("t_3_1", "t_award", "t_possession"):
            groups: dict[dt.date, list[int]] = {}
            for u in uids:
                groups.setdefault(evs[u][event]["date"], []).append(pi[u])
            for scope in ("all", "rabi"):
                sidx = np.array([col[k] for k in keys if scope == "all" or k[1] == "rabi"])
                for edate, rows in groups.items():
                    pre = np.array([j for j in sidx if mids[keys[j]] < edate], dtype=int)
                    post = np.array([j for j in sidx if mids[keys[j]] >= edate], dtype=int)
                    r = did_group(Zp[rows], Zc, pre, post, rng, FARMLIKE_Z if metric == "vigour_z" else None)
                    zs = Zp[rows][:, sidx] if len(sidx) else np.zeros((len(rows), 0))
                    pos = np.nansum(zs > 0.5, axis=1) if metric == "plough" else np.full(len(rows), -1)
                    for n, row in enumerate(rows):
                        # binary plough: a parcel with no detected ploughing in any season has DiD = minus the
                        # control-rate change (floor effect) -> not parcel evidence
                        informative = bool(r["n_pre"][n] > 0 and r["n_post"][n] > 0 and (metric != "plough" or pos[n] > 0))
                        out.append({"parcel_uid": uids[row], "event": event, "metric": metric, "season_scope": scope,
                                    "event_date": edate, **{k: (float(v[n]) if k not in ("n_pre", "n_post") else int(v[n]))
                                                            for k, v in r.items()},
                                    "pre_seasons": len(pre), "post_seasons": len(post), "informative": informative,
                                    "parcel_plough_seasons": int(pos[n]) if metric == "plough" else None})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------------------------- runs
def _lambda() -> float:
    return float(json.loads(fb.LAMBDA_JSON.read_text())["lambda"])


def run_features(conn: Any, no_db: bool = False) -> None:
    from planet.controls import extract as cx
    from planet.extract.run import OBS_PARQUET

    t0 = time.time()
    cfg = se.load_config()
    cobs = pd.read_parquet(cx.OBS_PARQUET).rename(columns={"cell_id": "parcel_uid"})
    cobs["mixed_pixel"] = False
    cfeat, cser, info = fb.build(cobs, cfg, lam=_lambda())
    cfeat = cfeat.rename(columns={"parcel_uid": "cell_id"})
    cser = cser.rename(columns={"parcel_uid": "cell_id"})
    cfeat.to_parquet(CTRL_FEATURES, index=False)
    cser.to_parquet(CTRL_SMOOTHED, index=False)
    print(f"control features {len(cfeat)} rows ({cfeat.cell_id.nunique()} cells), lambda {info['lambda']}")
    # ploughing
    pobs = pd.read_parquet(OBS_PARQUET)
    pfeat = pd.read_parquet(fb.FEATURES_PARQUET)
    psm = pd.read_parquet(fb.SMOOTHED_PARQUET)
    pp = plough_all(pobs, psm, pfeat)
    cobs2 = cobs.rename(columns={"parcel_uid": "cell_id"})
    cp = plough_all(cobs2, cser, cfeat, key="cell_id")
    pp.to_parquet(PLOUGH_PARQUET, index=False)
    cp.to_parquet(ps.CACHE_DIR / "controls" / "control_plough.parquet", index=False)
    # relative metrics
    cfeat_s = cfeat[cfeat.season.isin(SEASONS) & cfeat.data_sufficient].copy()
    cfeat_s["peak_offset"] = _peak_offset(cfeat_s, cfg)
    cfeat_s = cfeat_s.merge(cp, on=["cell_id", "ag_year", "season"], how="left")
    stats = control_stats(cfeat_s)
    pfeat_s = pfeat[pfeat.season.isin(SEASONS) & pfeat.data_sufficient].copy()
    pfeat_s["peak_offset"] = _peak_offset(pfeat_s, cfg)
    pfeat_s = pfeat_s.merge(pp, on=["parcel_uid", "ag_year", "season"], how="left")
    prel = relative(pfeat_s, cfeat_s, stats)
    crel = relative(cfeat_s, cfeat_s, stats)
    keep = ["ag_year", "season", *[f"z_{m}" for m in Z_METRICS], "vigour_z", "pct_amplitude", "n_controls",
            "plough_signal", "plough_date", "plough_delta", "plough_present", "plough_reason", "greenup_onset",
            "plough_nobs", "amplitude", "integral", "peak_offset"]
    prel[["parcel_uid", *keep]].to_parquet(REL_PARQUET, index=False)
    crel[["cell_id", *keep]].to_parquet(ps.CACHE_DIR / "controls" / "control_relative.parquet", index=False)
    stats.to_csv(ps.CACHE_DIR / "controls" / "control_stats.csv")
    print(summary_plough(prel, crel))
    if not no_db:
        _write_features(conn, cfeat, crel, prel)
    print(f"features/plough/relative runtime {time.time() - t0:.0f} s")


def summary_plough(prel: pd.DataFrame, crel: pd.DataFrame) -> str:
    rows = []
    for name, d in (("parcels", prel), ("controls", crel)):
        for s in SEASONS:
            x = d[d.season == s]
            g = x.plough_present.dropna()
            rows.append({"group": name, "season": s, "rows": len(x), "greenup_rows_with_signal_value": len(g),
                         "plough_rate": round(float(g.mean()), 3) if len(g) else None,
                         "vigour_z_median": round(float(x.vigour_z.median()), 2)})
    return pd.DataFrame(rows).to_string(index=False)


def _clean(v: Any) -> Any:
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def _write_features(conn: Any, cfeat: pd.DataFrame, crel: pd.DataFrame, prel: pd.DataFrame) -> None:
    from psycopg.types.json import Jsonb

    fk = [k for k in fb.FEATURE_KEYS if k in cfeat.columns]
    cr = crel.set_index(["cell_id", "ag_year", "season"])
    rows = []
    for r in cfeat.to_dict("records"):
        k = (r["cell_id"], int(r["ag_year"]), r["season"])
        pr = cr.loc[k] if k in cr.index else None
        feats = {c: _clean(r.get(c)) for c in fk}
        rows.append((r["cell_id"], int(r["ag_year"]), r["season"], Jsonb(feats),
                     None if pr is None else _clean(pr["plough_signal"]),
                     pr["plough_date"] if pr is not None and isinstance(pr["plough_date"], str) else None))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO control_season (cell_id, ag_year, season, features, plough_signal, plough_date) "
                        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (cell_id, ag_year, season) DO UPDATE SET "
                        "features = EXCLUDED.features, plough_signal = EXCLUDED.plough_signal, plough_date = EXCLUDED.plough_date",
                        rows)
        prow = []
        relcols = [f"z_{m}" for m in Z_METRICS] + ["vigour_z", "pct_amplitude", "n_controls", "plough_delta",
                                                    "plough_present", "plough_reason", "greenup_onset", "plough_nobs"]
        for r in prel.to_dict("records"):
            rel = {c: _clean(r.get(c)) for c in relcols}
            rel.update(version=DID_VERSION, caveats=CAVEATS[:4])
            pdate = r.get("plough_date")
            prow.append((_clean(r.get("plough_signal")), pdate if isinstance(pdate, str) else None, Jsonb(rel),
                         r["parcel_uid"], int(r["ag_year"]), r["season"]))
        cur.executemany("UPDATE parcel_season SET plough_signal = %s, plough_date = %s, relative = %s "
                        "WHERE parcel_uid = %s AND ag_year = %s AND season = %s", prow)
    conn.commit()
    print(f"control_season upserted {len(rows)}; parcel_season relative updated {len(prow)}")


def run_did(conn: Any, no_db: bool = False, limit: int | None = None) -> pd.DataFrame:
    from planet.events import windows as ew

    t0 = time.time()
    prel = pd.read_parquet(REL_PARQUET)
    crel = pd.read_parquet(ps.CACHE_DIR / "controls" / "control_relative.parquet")
    if limit:
        prel = prel[prel.parcel_uid.isin(prel.parcel_uid.drop_duplicates().head(limit))]
    did = compute_did(prel, crel, ew.load_fallbacks())
    did.to_parquet(DID_PARQUET, index=False)
    print(summary_did(did))
    if not no_db:
        from psycopg.types.json import Jsonb

        rows = [(r["parcel_uid"], r["event"], r["metric"], r["season_scope"], r["event_date"], r["n_pre"], r["n_post"],
                 _clean(r["pre_diff"]), _clean(r["post_diff"]), _clean(r["did"]), _clean(r["ci_lo"]), _clean(r["ci_hi"]),
                 _clean(r["farmland_like_p"]),
                 Jsonb({"pre_seasons": r["pre_seasons"], "post_seasons": r["post_seasons"], "bootstrap": B,
                        "informative": bool(r["informative"]), "parcel_plough_seasons": _clean(r["parcel_plough_seasons"]),
                        "farmlike_threshold_z": FARMLIKE_Z, "farmlike_meaning": FARMLIKE_MEANING,
                        "caveats": CAVEATS}), DID_VERSION)
                for r in did.to_dict("records")]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO parcel_did (parcel_uid, event, metric, season_scope, event_date, n_pre, n_post, pre_diff, "
                "post_diff, did, ci_lo, ci_hi, farmland_like_p, meta, did_version, computed_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) ON CONFLICT (parcel_uid, event, metric, season_scope) "
                "DO UPDATE SET event_date = EXCLUDED.event_date, n_pre = EXCLUDED.n_pre, n_post = EXCLUDED.n_post, "
                "pre_diff = EXCLUDED.pre_diff, post_diff = EXCLUDED.post_diff, did = EXCLUDED.did, ci_lo = EXCLUDED.ci_lo, "
                "ci_hi = EXCLUDED.ci_hi, farmland_like_p = EXCLUDED.farmland_like_p, meta = EXCLUDED.meta, "
                "did_version = EXCLUDED.did_version, computed_at = now()", rows)
        conn.commit()
        print(f"parcel_did upserted {len(rows)}")
    print(f"did runtime {time.time() - t0:.0f} s")
    return did


def summary_did(did: pd.DataFrame) -> str:
    lines = []
    for (ev, m, sc), g in did.groupby(["event", "metric", "season_scope"]):
        ok = g.did.notna() & g.informative
        sig_neg = (ok & (g.ci_hi < 0)).sum()
        sig_pos = (ok & (g.ci_lo > 0)).sum()
        d = g.did[ok]
        s = (f"{ev:12s} {m:9s} {sc:4s} n {int(ok.sum())}  DiD median {d.median():+.2f} "
             f"(p10 {d.quantile(.1):+.2f}, p90 {d.quantile(.9):+.2f})  CI<0 {sig_neg}  CI>0 {sig_pos}  "
             f"CI spans 0 {int(ok.sum() - sig_neg - sig_pos)}")
        if m == "vigour_z":
            s += f"  farmland_like_p>={FARMLIKE_P}: {int((g.farmland_like_p >= FARMLIKE_P).sum())}"
        if m == "plough":
            s += f"  (uninformative floor rows excluded: {int((~g.informative).sum())})"
        lines.append(s)
    return "\n".join(lines)


# ------------------------------------------------------------------------------------ park-wide report

PERIODS = {"baseline": "rabi seasons with midpoint before T_3(1) (undisturbed)",
           "pending_award": "rabi seasons between T_3(1) and T_possession",
           "post_possession": "rabi seasons with midpoint on/after T_possession"}
PARK_METRICS = ("vigour_z", "pct_amplitude", "plough_z", "plough_present", "onset_doy", "z_peak_offset")


def _period_of(mid: dt.date, ev: dict[str, Any]) -> str:
    if mid < ev["t_3_1"]["date"]:
        return "baseline"
    if mid < ev["t_possession"]["date"]:
        return "pending_award"
    return "post_possession"


def park_wide(prel: pd.DataFrame, crel: pd.DataFrame, fallbacks: dict[str, Any], seed: int = 11,
              cfg: se.SeasonConfig | None = None, n_boot: int = B) -> dict[str, Any]:
    """Park-level rabi comparison of parcels vs controls: per period mean(parcel) - mean(control) and the DiD
    post_possession - baseline, with a two-level bootstrap (parcels and control cells resampled)."""
    from planet.events import windows as ew

    cfg = cfg or se.load_config()
    rng = np.random.default_rng(seed)
    p = prel[prel.season == "rabi"].copy()
    c = crel[crel.season == "rabi"].copy()
    for d in (p, c):
        d["plough_z"] = d.plough_signal.clip(-PLOUGH_Z_CLIP, PLOUGH_Z_CLIP)
        d["onset_doy"] = pd.to_datetime(d.greenup_onset).map(lambda t: np.nan if pd.isna(t) else float(
            (t - pd.Timestamp(year=t.year if t.month >= 6 else t.year - 1, month=6, day=1)).days))
    mids = _midpoints(sorted({(int(y), "rabi") for y in p.ag_year}), cfg)
    evs = {u: ew.event_dates(u, fallbacks) for u in p.parcel_uid.unique()}
    p["period"] = [_period_of(mids[(int(y), "rabi")], evs[u]) for u, y in zip(p.parcel_uid, p.ag_year, strict=True)]
    # exact per-parcel matching: the control mean of the SAME season-year is subtracted
    out: dict[str, Any] = {"periods": PERIODS, "metrics": {}, "n_parcels": int(p.parcel_uid.nunique()),
                           "n_controls": int(c.cell_id.nunique()), "bootstrap": n_boot}
    puids = np.array(sorted(p.parcel_uid.unique()))
    cids = np.array(sorted(c.cell_id.unique()))
    years = sorted(p.ag_year.unique())
    for m in PARK_METRICS:
        P = p.pivot_table(index="parcel_uid", columns="ag_year", values=m, aggfunc="first").reindex(index=puids, columns=years)
        C = c.pivot_table(index="cell_id", columns="ag_year", values=m, aggfunc="first").reindex(index=cids, columns=years)
        per = p.pivot_table(index="parcel_uid", columns="ag_year", values="period", aggfunc="first").reindex(index=puids, columns=years)
        Pv, Cv, Per = P.to_numpy(float), C.to_numpy(float), per.to_numpy(object)

        def stat(pi: np.ndarray, ci: np.ndarray, Pv: np.ndarray = Pv, Cv: np.ndarray = Cv,
                 Per: np.ndarray = Per) -> dict[str, float]:
            cm = np.nanmean(Cv[ci], axis=0)
            d = Pv[pi] - cm[None, :]
            r = {}
            for k in PERIODS:
                mask = Per[pi] == k
                r[k] = float(np.nanmean(np.where(mask, d, np.nan))) if mask.any() else np.nan
            r["did_post_vs_baseline"] = r["post_possession"] - r["baseline"]
            r["did_pending_vs_baseline"] = r["pending_award"] - r["baseline"]
            return r

        with np.errstate(all="ignore"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                est = stat(np.arange(len(puids)), np.arange(len(cids)))
                boots = [stat(rng.integers(0, len(puids), len(puids)), rng.integers(0, len(cids), len(cids)))
                         for _ in range(n_boot)]
        ci = {k: [float(np.nanpercentile([b[k] for b in boots], 2.5)), float(np.nanpercentile([b[k] for b in boots], 97.5))]
              for k in est}
        by_year = {int(y): {"parcels": _clean(np.nanmedian(Pv[:, j])), "controls": _clean(np.nanmedian(Cv[:, j]))}
                   for j, y in enumerate(years)}
        out["metrics"][m] = {"estimate": {k: _clean(v) for k, v in est.items()}, "ci95": ci, "median_by_ag_year": by_year}
    return out


def possession_distribution(did: pd.DataFrame, prel: pd.DataFrame) -> dict[str, Any]:
    """Per-parcel post-possession picture (rabi scope): farmland_like_p, vigour DiD sign, plough detections."""
    v = did[(did.event == "t_possession") & (did.metric == "vigour_z") & (did.season_scope == "rabi")]
    z = did[(did.event == "t_possession") & (did.metric == "plough_z") & (did.season_scope == "rabi")]
    flp = v.farmland_like_p
    post = prel[(prel.season == "rabi")].merge(v[["parcel_uid", "event_date"]], on="parcel_uid")
    post = post[pd.to_datetime(post.greenup_onset).notna() | post.plough_reason.notna()]
    mids = _midpoints(sorted({(int(y), "rabi") for y in post.ag_year}), se.load_config())
    post = post[[mids[(int(y), "rabi")] >= d for y, d in zip(post.ag_year, post.event_date, strict=True)]]
    pl = post.plough_present
    hist = np.histogram(flp.dropna(), bins=[0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001])[0].tolist()
    return {
        "parcels": int(v.parcel_uid.nunique()),
        "farmland_like_p_ge_0_7": int((flp >= FARMLIKE_P).sum()),
        "farmland_like_p_hist": dict(zip(["0-0.1", "0.1-0.3", "0.3-0.5", "0.5-0.7", "0.7-0.9", "0.9-1"], hist, strict=True)),
        "farmland_like_meaning": FARMLIKE_MEANING,
        "vigour_did": {"median": _clean(v.did.median()), "p10": _clean(v.did.quantile(.1)), "p90": _clean(v.did.quantile(.9)),
                       "ci_below_0": int((v.ci_hi < 0).sum()), "ci_above_0": int((v.ci_lo > 0).sum()),
                       "ci_spans_0": int(((v.ci_lo <= 0) & (v.ci_hi >= 0)).sum())},
        "plough_z_did": {"n": int(z.did.notna().sum()), "median": _clean(z.did.median()),
                         "ci_below_0": int((z.ci_hi < 0).sum()), "ci_above_0": int((z.ci_lo > 0).sum())},
        "post_possession_rabi_plough": {"parcel_seasons_defined": int(pl.notna().sum()),
                                        "plough_detected": int((pl == 1).sum()),
                                        "rate": _clean(pl.mean()),
                                        "signal_quantiles": {q: _clean(post.plough_signal.quantile(q)) for q in (0.1, 0.5, 0.9)}},
        "farmland_like_and_plough_detected": int(v[v.farmland_like_p >= FARMLIKE_P].parcel_uid.isin(
            post[post.plough_present == 1].parcel_uid).sum()),
        "significant_vigour_drop_vs_controls": int((v.ci_hi < 0).sum()),
    }


def run_report(no_db: bool = False) -> dict[str, Any]:
    from planet.aoi import REPO
    from planet.events import windows as ew

    prel = pd.read_parquet(REL_PARQUET)
    crel = pd.read_parquet(ps.CACHE_DIR / "controls" / "control_relative.parquet")
    did = pd.read_parquet(DID_PARQUET)
    fbk = ew.load_fallbacks()
    rep = {"version": DID_VERSION, "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
           "park_wide_rabi": park_wide(prel, crel, fbk), "post_possession": possession_distribution(did, prel),
           "caveats": CAVEATS}
    path = REPO / "eval" / "planet" / "did_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: rep["park_wide_rabi"]["metrics"][k]["estimate"] for k in PARK_METRICS}, indent=1))
    print(json.dumps(rep["post_possession"], indent=1))
    print(f"-> {path}")
    return rep
