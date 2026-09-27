"""T3.3 — smoothed series and parcel × season / × ag-year phenology features (``make s2-features``).

Input: ``parcel_obs`` rows (DB, or ``data/s2/parcel_obs.parquet`` with ``--from-parquet``).
Steps per parcel:
1. usable obs (``config/seasons.yaml: usable_obs``): valid_frac >= 0.5 and n_px >= 10, or n_px >= 1 for
   the D-026 ``mixed_pixel`` parcels (all of whose obs are low_support by construction);
2. robust Whittaker smoothing of NDVI, BSI and NDWI on a daily grid (lambda tuned by hold-out CV);
3. for every season window (kharif / rabi / summer) and every ag-year (annual) overlapping the archive:
   ``ndvi_max, ndvi_min, amplitude, ndvi_mean, dry_mean, peaks_count, peak_doy, integral, bsi_dry_mean,
   ndwi_max, n_obs, max_gap_days, yoy_delta`` + gap flags (evaluated **per season**, D-026);
4. neighbour context ``nbr_amp_med_300m`` = median amplitude of other parcels within 300 m (same window,
   data-sufficient only) — helps separate a weed flush from a crop;
5. upsert ``parcel_season.features`` (state columns untouched; they stay NULL until T3.4), snapshot
   ``data/s2/parcel_season_features.parquet`` and the 5-day smoothed series ``data/s2/smoothed.parquet``.

Gap rule: a window is ``data_sufficient`` when it has >= 2 usable obs or every gap inside it is < 45 days;
otherwise T3.4 must return ``insufficient_data`` for it. ``gap_flag`` marks windows with a gap > 45 d
(the series is still smoothed across it; values there are interpolations).

All features are *signals needing field verification*; each row carries ``caveats``.
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from planet import aoi, tabio
from planet.extract import parcel_stats as ps
from planet.features import seasons as se
from planet.features import smooth as sm

log = logging.getLogger("planet.features")

FEATURES_VERSION = "feat-v1"
FEATURES_PARQUET = ps.CACHE_DIR / "parcel_season_features.parquet"
SMOOTHED_PARQUET = ps.CACHE_DIR / "smoothed.parquet"
LAMBDA_JSON = ps.CACHE_DIR / "whittaker_lambda.json"
EPOCH = pd.Timestamp("2019-01-01")
BASE_CAVEATS = ("signal needing field verification", "undetected cloud/haze can depress NDVI",
                "10 m pixels mix neighbouring land cover at parcel edges")


# --------------------------------------------------------------------------- usable obs
def usable_mask(obs: pd.DataFrame, cfg: se.SeasonConfig) -> pd.Series:
    u = cfg.get("usable_obs", default={}) or {}
    vf = float(u.get("min_valid_frac", 0.5))
    n_norm = int((u.get("normal") or {}).get("min_n_px", ps.LOW_SUPPORT_PX))
    n_mix = int((u.get("mixed_pixel") or {}).get("min_n_px", 1))
    mixed = obs["mixed_pixel"].astype(bool) if "mixed_pixel" in obs else pd.Series(False, index=obs.index)
    need = np.where(mixed, n_mix, n_norm)
    return (obs["valid_frac"] >= vf) & (obs["n_px"] >= need) & obs["ndvi"].notna()


# --------------------------------------------------------------------------- gaps
def max_gap_in_window(obs_days: np.ndarray, a: int, b: int, lo: int, hi: int) -> int:
    """Longest unobserved stretch inside window [a, b] (inclusive day offsets).

    Gaps are measured between consecutive usable obs of the *whole* series (so a gap straddling the
    window edge counts only its part inside the window); the archive edges ``lo``/``hi`` count as
    observed boundaries only if the window extends beyond them (partial windows are flagged separately).
    """
    a2, b2 = max(a, lo), min(b, hi)
    if b2 < a2:
        return int(b - a + 1)
    d = np.unique(np.asarray(obs_days, dtype=int))
    pts = np.concatenate([[a2 if (len(d) == 0 or d[0] > a2) else d[0]], d,
                          [b2 if (len(d) == 0 or d[-1] < b2) else d[-1]]])
    pts = np.unique(pts)
    starts, ends = pts[:-1], pts[1:]
    s = np.maximum(starts, a2)
    e = np.minimum(ends, b2)
    ov = e - s
    return int(ov.max()) if ov.size and ov.max() > 0 else 0


def gap_rule(n_obs: int, max_gap: int, cfg: se.SeasonConfig) -> tuple[bool, bool]:
    """(data_sufficient, gap_flag) for one window."""
    g = int(cfg.get("gap_rule", "max_gap_days", default=45))
    k = int(cfg.get("gap_rule", "min_obs", default=2))
    return (n_obs >= k) or (max_gap < g), max_gap > g


# --------------------------------------------------------------------------- window features
def _dry_days(dates: pd.DatetimeIndex, cfg: se.SeasonConfig) -> np.ndarray:
    return np.isin(dates.month, cfg.dry_months)


def supported_days(obs_days: np.ndarray, n_days: int, max_gap: int = 45, halo: int = 15) -> np.ndarray:
    """Daily mask of days where the smoothed series is backed by data: inside a gap of <= ``max_gap`` days
    between consecutive usable obs, or within ``halo`` days of an obs. Elsewhere the Whittaker solution is
    an inter/extrapolation across a long gap and must not feed features."""
    m = np.zeros(n_days, dtype=bool)
    d = np.unique(np.asarray(obs_days, dtype=int))
    d = d[(d >= 0) & (d < n_days)]
    for u, v in itertools.pairwise(d):
        if v - u <= max_gap:
            m[u:v + 1] = True
    for u in d:
        m[max(0, u - halo):min(n_days, u + halo + 1)] = True
    return m


def window_features(z_ndvi: np.ndarray, z_bsi: np.ndarray, z_ndwi: np.ndarray, peaks: np.ndarray,
                    a: int, b: int, s0: int, s1: int, cfg: se.SeasonConfig,
                    support: np.ndarray | None = None) -> dict[str, Any]:
    """Features of the smoothed series over the supported days of window [a, b] ∩ [s0, s1]."""
    lo, hi = max(a, s0), min(b, s1)
    out: dict[str, Any] = dict.fromkeys(("ndvi_max", "ndvi_min", "amplitude", "ndvi_mean", "dry_mean",
                                         "bsi_dry_mean", "ndwi_max", "peak_doy", "integral"))
    out["peaks_count"] = 0
    out["covered_days"] = 0
    if hi < lo:
        return out
    day = np.arange(lo, hi + 1)
    keep = support[lo:hi + 1] if support is not None else np.ones(len(day), dtype=bool)
    out["covered_days"] = int(keep.sum())
    if not keep.any():
        return out
    day = day[keep]
    nd = z_ndvi[day]
    dates = pd.DatetimeIndex(EPOCH + pd.to_timedelta(day, unit="D"))
    dry = _dry_days(dates, cfg)
    if not dry.any():  # e.g. rabi: lowest quartile of the window
        dry = nd <= np.quantile(nd, 0.25)
    base = float(cfg.get("integral_baseline", default=0.15))
    imax = int(np.argmax(nd))
    ok_peaks = peaks[(peaks >= lo) & (peaks <= hi)]
    ok_peaks = ok_peaks[support[ok_peaks]] if support is not None else ok_peaks
    out.update(
        ndvi_max=float(nd.max()), ndvi_min=float(nd.min()), amplitude=float(nd.max() - nd.min()),
        ndvi_mean=float(nd.mean()), dry_mean=float(nd[dry].mean()), bsi_dry_mean=float(z_bsi[day][dry].mean()),
        ndwi_max=float(z_ndwi[day].max()), peak_doy=int(dates[imax].dayofyear),
        integral=float(np.clip(nd - base, 0, None).sum()),
        peaks_count=len(ok_peaks),
    )
    return out


def find_peaks(z: np.ndarray, cfg: se.SeasonConfig) -> np.ndarray:
    from scipy.signal import find_peaks as fp

    h = float(cfg.get("peaks", "min_height", default=0.45))
    p = float(cfg.get("peaks", "min_prominence", default=0.15))
    idx, _ = fp(z, height=h, prominence=p)
    return idx


# --------------------------------------------------------------------------- per parcel
def parcel_features(uid: str, obs: pd.DataFrame, cfg: se.SeasonConfig, lam: float, n_days: int,
                    archive: tuple[int, int], wins: Sequence[tuple[int, str, pd.Timestamp, pd.Timestamp]]
                    ) -> tuple[list[dict[str, Any]], pd.DataFrame | None]:
    """Feature rows for one parcel (all windows) + its 5-day smoothed series."""
    it = int(cfg.get("smoothing", "robust_iterations", default=2))
    u = obs[obs["usable"]]
    days = (pd.to_datetime(u["date"]) - EPOCH).dt.days.to_numpy()
    mixed = bool(obs["mixed_pixel"].iloc[0]) if "mixed_pixel" in obs else False
    mode = str(obs["geom_mode"].iloc[0]) if "geom_mode" in obs else "buf5"
    n_total = int(obs["n_total"].iloc[0]) if "n_total" in obs else None
    rows = []
    series = None
    if len(u) >= 2:
        z = {k: sm.smooth_series(days, u[k].to_numpy(dtype=float), n_days, lam, it) for k in ("ndvi", "bsi", "ndwi")}
        s0, s1 = int(days.min()), int(days.max())
        gmax = int(cfg.get("gap_rule", "max_gap_days", default=45))
        support = supported_days(days, n_days, gmax)
        z = {k: np.clip(v, -1.0, 1.0) for k, v in z.items()}
        peaks = find_peaks(z["ndvi"][s0:s1 + 1], cfg) + s0
        step = np.arange(s0, s1 + 1, 5)
        series = pd.DataFrame({"parcel_uid": uid, "date": (EPOCH + pd.to_timedelta(step, unit="D")).date,
                               "ndvi_s": z["ndvi"][step].astype("float32"), "bsi_s": z["bsi"][step].astype("float32"),
                               "ndwi_s": z["ndwi"][step].astype("float32"), "supported": support[step]})
    else:
        z, s0, s1, peaks, support = None, 0, -1, np.empty(0, int), None
    cap = float(cfg.get("mixed_pixel_confidence_cap", default=0.6))
    all_days = (pd.to_datetime(obs["date"]) - EPOCH).dt.days.to_numpy()
    for ag_year, season, wa, wb in wins:
        a, b = int((wa - EPOCH).days), int((wb - EPOCH).days)
        in_w = (days >= a) & (days <= b)
        n_obs = int(in_w.sum())
        gap = max_gap_in_window(days, a, b, *archive)
        sufficient, gflag = gap_rule(n_obs, gap, cfg)
        if z is not None:
            f = window_features(z["ndvi"], z["bsi"], z["ndwi"], peaks, a, b, s0, s1, cfg, support)
        else:
            f = window_features(np.zeros(1), np.zeros(1), np.zeros(1), peaks, a, b, 0, -1, cfg)
        raw_in = (all_days >= a) & (all_days <= b)
        f.update(
            n_obs=n_obs, n_scenes=int(raw_in.sum()), max_gap_days=gap, data_sufficient=bool(sufficient),
            gap_flag=bool(gflag), partial_window=bool(a < archive[0] or b > archive[1]),
            ndvi_obs_max=float(u["ndvi"][in_w].max()) if n_obs else None,
            low_support_frac=float(obs["low_support"][raw_in].mean()) if raw_in.any() else None,
            mixed_pixel=mixed, geom_mode=mode, n_total_px=n_total,
            confidence_cap=cap if mixed else 1.0,
        )
        cav = list(BASE_CAVEATS)
        if gflag:
            cav.append(f"gap > {int(cfg.get('gap_rule', 'max_gap_days', default=45))} d in window (values interpolated)")
        if not sufficient:
            cav.append("insufficient_data: < 2 usable obs and gaps >= 45 d")
        if mixed:
            cav.append("mixed_pixel: parcel too small for the -5 m buffer; measured on the outline, confidence capped")
        if season in ("rabi", cfg.annual) and (f.get("amplitude") or 0) > 0.15:
            cav.append("NE-monsoon weed flush can mimic a crop cycle; compare with neighbour amplitude")
        if f["partial_window"]:
            cav.append("window only partly covered by the archive")
        f["caveats"] = cav
        rows.append({"parcel_uid": uid, "ag_year": ag_year, "season": season, **f})
    return rows, series


def add_yoy(df: pd.DataFrame) -> pd.DataFrame:
    """yoy_delta = ndvi_max minus the same season's ndvi_max in the previous ag-year (both sufficient)."""
    key = df[["parcel_uid", "season", "ag_year", "ndvi_max", "integral", "data_sufficient"]].copy()
    prev = key.assign(ag_year=key.ag_year + 1).rename(
        columns={"ndvi_max": "_pmax", "integral": "_pint", "data_sufficient": "_psuf"})
    m = df.merge(prev, on=["parcel_uid", "season", "ag_year"], how="left")
    ok = m["data_sufficient"] & m["_psuf"].fillna(False).astype(bool)
    m["yoy_delta"] = np.where(ok, m["ndvi_max"] - m["_pmax"], np.nan)
    m["yoy_delta_integral"] = np.where(ok, m["integral"] - m["_pint"], np.nan)
    return m.drop(columns=["_pmax", "_pint", "_psuf"])


def neighbour_pairs(parcels: Any, radius_m: float) -> dict[str, list[str]]:
    """parcel_uid -> other parcel_uids whose outline lies within ``radius_m`` (UTM 32643)."""
    g = parcels.to_crs(f"EPSG:{aoi.UTM_EPSG}")[["parcel_uid", "geometry"]].reset_index(drop=True)
    tree = g.sindex
    out: dict[str, list[str]] = {}
    for i, geom in enumerate(g.geometry):
        cand = tree.query(geom, predicate="dwithin", distance=radius_m)
        out[g.parcel_uid[i]] = [g.parcel_uid[j] for j in cand if j != i]
    return out


def add_neighbour_context(df: pd.DataFrame, nbrs: dict[str, list[str]]) -> pd.DataFrame:
    amp = df[df["data_sufficient"]].set_index(["ag_year", "season", "parcel_uid"])["amplitude"]
    lut = {k: v for k, v in amp.groupby(level=[0, 1])}
    med, cnt = [], []
    for uid, y, s in zip(df.parcel_uid, df.ag_year, df.season):
        vals = lut.get((y, s))
        if vals is None:
            med.append(np.nan)
            cnt.append(0)
            continue
        v = vals.droplevel([0, 1]).reindex(nbrs.get(uid, [])).dropna()
        med.append(float(v.median()) if len(v) else np.nan)
        cnt.append(len(v))
    return df.assign(nbr_amp_med_300m=med, nbr_n=cnt)


# --------------------------------------------------------------------------- lambda
def tune_lambda(obs: pd.DataFrame, cfg: se.SeasonConfig, n_days: int, sample: int = 150, seed: int = 0
                ) -> tuple[float, dict[float, float]]:
    """Hold-out CV over a fixed sample of analysable (non-mixed) parcels; picks the smallest-RMSE lambda."""
    grid = [float(x) for x in cfg.get("smoothing", "lambda_grid", default=[1e2, 1e3, 1e4, 1e5])]
    u = obs[obs.usable & ~obs.mixed_pixel.astype(bool)]
    uids = sorted(u.parcel_uid.unique())
    rng = np.random.default_rng(seed)
    pick = rng.choice(uids, size=min(sample, len(uids)), replace=False)
    series = []
    for uid in pick:
        o = u[u.parcel_uid == uid]
        series.append(((pd.to_datetime(o["date"]) - EPOCH).dt.days.to_numpy(), o["ndvi"].to_numpy(dtype=float)))
    it = int(cfg.get("smoothing", "robust_iterations", default=2))
    rmse = sm.cv_lambda(series, n_days, grid, iterations=it, seed=seed)
    best = min(rmse, key=rmse.get)
    return best, rmse


# --------------------------------------------------------------------------- IO
def load_obs(from_parquet: bool) -> pd.DataFrame:
    if from_parquet:
        return tabio.read_table(ps.CACHE_DIR / "parcel_obs.parquet")
    import psycopg

    from planet.extract.run import dsn

    with psycopg.connect(dsn()) as conn:
        cur = conn.execute(
            "SELECT parcel_uid, scene_id, date, ndvi, ndwi, bsi, ndbi, valid_frac, n_px, n_total, low_support, "
            "mixed_pixel, geom_mode FROM parcel_obs WHERE parcel_uid IS NOT NULL ORDER BY parcel_uid, date")
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def _json_safe(v: Any) -> Any:
    if isinstance(v, float | np.floating):
        return None if not np.isfinite(v) else round(float(v), 5)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


FEATURE_KEYS = ("ndvi_max", "ndvi_min", "amplitude", "ndvi_mean", "dry_mean", "peaks_count", "peak_doy", "integral",
                "bsi_dry_mean", "ndwi_max", "n_obs", "n_scenes", "max_gap_days", "yoy_delta", "yoy_delta_integral",
                "nbr_amp_med_300m", "nbr_n", "data_sufficient", "gap_flag", "partial_window", "covered_days",
                "ndvi_obs_max", "low_support_frac", "mixed_pixel", "geom_mode", "n_total_px", "confidence_cap",
                "caveats")


def upsert_features(conn: Any, df: pd.DataFrame, meta: dict[str, Any]) -> int:
    now = datetime.now(UTC)
    n = 0
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE _ps (parcel_uid text, ag_year smallint, season text, features jsonb) "
                     "ON COMMIT DROP")
        with conn.cursor().copy("COPY _ps (parcel_uid, ag_year, season, features) FROM STDIN") as cp:
            for r in df.itertuples(index=False):
                d = r._asdict()
                feats = {k: _json_safe(d[k]) for k in FEATURE_KEYS if k in d}
                feats["_meta"] = meta
                cp.write_row([d["parcel_uid"], int(d["ag_year"]), d["season"], json.dumps(feats)])
                n += 1
        conn.execute(
            "INSERT INTO parcel_season (parcel_uid, ag_year, season, features, features_version, features_at) "
            "SELECT parcel_uid, ag_year, season, features, %s, %s FROM _ps "
            "ON CONFLICT (parcel_uid, ag_year, season) WHERE parcel_uid IS NOT NULL DO UPDATE SET "
            "features = EXCLUDED.features, features_version = EXCLUDED.features_version, "
            "features_at = EXCLUDED.features_at", (FEATURES_VERSION, now))
    return n


# --------------------------------------------------------------------------- main
def build(obs: pd.DataFrame, cfg: se.SeasonConfig, lam: float | None = None, parcels: Any = None,
          limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    obs = obs.copy()
    obs["date"] = pd.to_datetime(obs["date"])
    if "mixed_pixel" not in obs:
        obs["mixed_pixel"] = False
    obs["usable"] = usable_mask(obs, cfg)
    first, last = obs["date"].min(), obs["date"].max()
    n_days = int((last - EPOCH).days) + 1
    archive = (int((first - EPOCH).days), int((last - EPOCH).days))
    info: dict[str, Any] = {}
    cfg_lam = cfg.get("smoothing", "lambda", default="auto")
    if lam is None and str(cfg_lam) != "auto":
        lam = float(cfg_lam)
    if lam is None:
        lam, rmse = tune_lambda(obs, cfg, n_days)
        info["lambda_cv_rmse"] = rmse
    info["lambda"] = lam
    wins = se.windows(first, last, cfg)
    uids = sorted(obs.parcel_uid.unique())
    if limit:
        uids = uids[:limit]
    rows, series = [], []
    for uid, o in obs[obs.parcel_uid.isin(uids)].groupby("parcel_uid", sort=True):
        r, s = parcel_features(uid, o.sort_values("date"), cfg, lam, n_days, archive, wins)
        rows.extend(r)
        if s is not None:
            series.append(s)
    df = add_yoy(pd.DataFrame(rows))
    if parcels is not None:
        df = add_neighbour_context(df, neighbour_pairs(parcels, float(cfg.get("neighbour_radius_m", default=300))))
    ser = pd.concat(series, ignore_index=True) if series else pd.DataFrame()
    info.update(archive=[str(first.date()), str(last.date())], windows=len(wins), parcels=len(uids))
    return df, ser, info


def summary_lines(df: pd.DataFrame) -> list[str]:
    out = [(f"parcel_season rows {len(df)} ({df.parcel_uid.nunique()} parcels); by season "
           f"{df.season.value_counts().to_dict()}")]
    t = df.groupby("season").agg(sufficient=("data_sufficient", "mean"), gap_flag=("gap_flag", "mean"),
                                 n_obs_p10=("n_obs", lambda s: s.quantile(0.1)), n_obs_p50=("n_obs", "median"),
                                 n_obs_p90=("n_obs", lambda s: s.quantile(0.9)),
                                 gap_p50=("max_gap_days", "median"), amp_p50=("amplitude", "median"))
    out.append(t.round(3).to_string())
    by_year = df[df.season != "annual"].groupby("ag_year")["data_sufficient"].mean().round(3).to_dict()
    out.append(f"share data_sufficient by ag_year (seasons): {by_year}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T3.3 phenology features")
    ap.add_argument("--limit", type=int, default=None, help="only the first N parcels (smoke)")
    ap.add_argument("--from-parquet", action="store_true", help="read data/s2/parcel_obs.parquet, not the DB")
    ap.add_argument("--lambda", dest="lam", type=float, default=None, help="override Whittaker lambda")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t0 = time.monotonic()
    cfg = se.load_config()
    obs = load_obs(args.from_parquet)
    lam = args.lam
    if lam is None and LAMBDA_JSON.exists() and str(cfg.get("smoothing", "lambda", default="auto")) == "auto":
        cached = json.loads(LAMBDA_JSON.read_text())
        if cached.get("n_obs_rows") == len(obs) and cached.get("config") == cfg.version:
            lam = float(cached["lambda"])
            print(f"lambda {lam:g} (cached CV, {LAMBDA_JSON.name})")
    parcels = aoi.load_parcels_uid()
    df, ser, info = build(obs, cfg, lam, parcels, args.limit)
    if "lambda_cv_rmse" in info:
        print("lambda CV hold-out RMSE: " + ", ".join(f"{k:g}: {v:.4f}" for k, v in info["lambda_cv_rmse"].items()))
        if not args.limit:
            LAMBDA_JSON.write_text(json.dumps({"lambda": info["lambda"], "cv_rmse": info["lambda_cv_rmse"],
                                               "n_obs_rows": len(obs), "config": cfg.version}, indent=1))
    print(f"lambda {info['lambda']:g}; archive {info['archive']}; windows {info['windows']}")
    if not args.limit:
        tabio.write_table(df.assign(caveats=df.caveats.map(json.dumps)), FEATURES_PARQUET)
        tabio.write_table(ser, SMOOTHED_PARQUET)
    if not args.no_db:
        import psycopg

        from planet.extract.run import dsn

        meta = {"features_version": FEATURES_VERSION, "seasons": cfg.version, "lambda": info["lambda"]}
        with psycopg.connect(dsn()) as conn:
            n = upsert_features(conn, df, meta)
            tot = conn.execute("SELECT count(*), count(*) FILTER (WHERE state IS NOT NULL) FROM parcel_season "
                               "WHERE parcel_uid IS NOT NULL").fetchone()
        print(f"parcel_season upserted {n}; table {tot[0]} rows ({tot[1]} with state)")
    for line in summary_lines(df):
        print(line)
    print(f"runtime {time.monotonic() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
