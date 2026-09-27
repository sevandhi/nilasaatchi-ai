"""Offline tests for T3.3: seasons, Whittaker smoothing, gap rule and feature maths (synthetic data)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("scipy")

from planet.features import build as fb
from planet.features import seasons as se
from planet.features import smooth as sm

CFG = se.load_config()


# ---------------------------------------------------------------- seasons
def test_config_partitions_months():
    CFG.validate()
    assert CFG.seasons["rabi"] == [10, 11, 12, 1, 2]
    assert CFG.start_month == 6


@pytest.mark.parametrize("d,ag,season", [
    ("2024-06-01", 2024, "kharif"), ("2024-09-30", 2024, "kharif"), ("2024-10-01", 2024, "rabi"),
    ("2025-01-15", 2024, "rabi"), ("2025-02-28", 2024, "rabi"), ("2025-03-01", 2024, "summer"),
    ("2025-05-31", 2024, "summer"), ("2025-03-21", 2024, "summer"),
])
def test_assign(d, ag, season):
    r = se.assign([d], CFG).iloc[0]
    assert (r.ag_year, r.season) == (ag, season)
    assert se.ag_year_of(pd.Timestamp(d)) == ag


def test_windows_bounds():
    a, b = se.window(2024, "rabi", CFG)
    assert (a, b) == (pd.Timestamp("2024-10-01"), pd.Timestamp("2025-02-28"))
    a, b = se.window(2023, "rabi", CFG)
    assert b == pd.Timestamp("2024-02-29")  # leap year
    a, b = se.window(2024, "annual", CFG)
    assert (a, b) == (pd.Timestamp("2024-06-01"), pd.Timestamp("2025-05-31"))
    ws = se.windows(pd.Timestamp("2019-01-04"), pd.Timestamp("2019-07-01"), CFG)
    assert [(y, s) for y, s, *_ in ws] == [(2018, "rabi"), (2018, "summer"), (2018, "annual"),
                                           (2019, "kharif"), (2019, "annual")]


# ---------------------------------------------------------------- smoothing
def test_penalty_matches_dense():
    for n in (3, 5, 40):
        d = np.diff(np.eye(n), 2, axis=0)
        p = d.T @ d
        ab = sm._penalty_bands(n, 2.0)
        for k in range(3):
            assert np.allclose(ab[2 - k, k:], 2.0 * np.diag(p, k))


def test_whittaker_recovers_line_and_interpolates_gaps():
    n = 200
    x = np.arange(n, dtype=float)
    y = 0.2 + 0.002 * x
    w = np.zeros(n)
    w[::10] = 1
    z = sm.whittaker(np.where(w > 0, y, np.nan), w, 1e4)
    assert np.allclose(z, y, atol=1e-3)  # 2nd-order penalty leaves straight lines untouched


def test_robust_whittaker_ignores_cloud_dip():
    n = 365
    t = np.arange(n)
    truth = 0.3 + 0.35 * np.exp(-0.5 * ((t - 180) / 30) ** 2)
    days = np.arange(0, n, 5)
    vals = truth[days].copy()
    vals[days == 180] = 0.05  # undetected cloud at the peak
    z_plain = sm.smooth_series(days, vals, n, 1e3, iterations=0)
    z_rob = sm.smooth_series(days, vals, n, 1e3, iterations=2)
    assert abs(z_rob[180] - truth[180]) < abs(z_plain[180] - truth[180])
    assert abs(z_rob[180] - truth[180]) < 0.05


def test_to_daily_averages_duplicates():
    y, w = sm.to_daily(np.array([0, 0, 3]), np.array([0.2, 0.4, 0.5]), 5)
    assert y[0] == pytest.approx(0.3) and w.tolist() == [1, 0, 0, 1, 0] and np.isnan(y[1])


def test_cv_lambda_prefers_smoothing_on_noise():
    rng = np.random.default_rng(1)
    n = 730
    t = np.arange(n)
    truth = 0.35 + 0.25 * np.sin(2 * np.pi * t / 365)
    series = []
    for _ in range(5):
        days = np.sort(rng.choice(n, 90, replace=False))
        series.append((days, truth[days] + rng.normal(0, 0.04, len(days))))
    rmse = sm.cv_lambda(series, n, [1.0, 1e3, 1e4], iterations=0)
    assert rmse[1.0] > min(rmse[1e3], rmse[1e4])


# ---------------------------------------------------------------- gap rule
def test_max_gap_in_window_edges_and_straddle():
    # obs at days 0, 50, 60, 200; window 40..100 -> gap 60->200 clipped to 60..100 = 40
    d = np.array([0, 50, 60, 200])
    assert fb.max_gap_in_window(d, 40, 100, 0, 300) == 40
    # window with no obs, fully inside a gap
    assert fb.max_gap_in_window(d, 70, 150, 0, 300) == 80
    # window beyond the archive end: clipped to the archive
    assert fb.max_gap_in_window(d, 190, 400, 0, 210) == 10


def test_gap_rule():
    assert fb.gap_rule(2, 80, CFG) == (True, True)     # 2 obs suffice, but the gap is flagged
    assert fb.gap_rule(1, 80, CFG) == (False, True)    # -> insufficient_data
    assert fb.gap_rule(1, 40, CFG) == (True, False)
    assert fb.gap_rule(0, 45, CFG) == (False, False)   # gaps must be < 45 to rescue a single obs


# ---------------------------------------------------------------- features end to end
def _synthetic_obs(uid="V|1", mixed=False, n_px=40, drop_monsoon=False):
    dates = pd.date_range("2022-06-03", "2024-05-28", freq="5D")
    doy = dates.dayofyear.to_numpy()
    # a rabi crop peaking mid-December (doy ~350) on a 0.2 background
    dist = np.minimum(np.abs(doy - 350), 365 - np.abs(doy - 350))
    ndvi = 0.2 + 0.5 * np.exp(-0.5 * (dist / 25.0) ** 2)
    df = pd.DataFrame({"parcel_uid": uid, "scene_id": [f"S{i}" for i in range(len(dates))], "date": dates,
                       "ndvi": ndvi, "ndwi": -ndvi, "bsi": 0.1 - 0.3 * ndvi, "ndbi": 0.0,
                       "valid_frac": 1.0, "n_px": n_px, "n_total": n_px, "low_support": n_px < 10,
                       "mixed_pixel": mixed, "geom_mode": "unbuffered" if mixed else "buf5"})
    if drop_monsoon:
        df = df[~((df.date >= "2023-06-01") & (df.date <= "2023-09-30"))]
    return df


def test_build_features_peak_and_seasons():
    df, ser, _info = fb.build(_synthetic_obs(), CFG, lam=100.0)
    r = df.set_index(["ag_year", "season"])
    rabi = r.loc[(2022, "rabi")]
    assert rabi.ndvi_max == pytest.approx(0.7, abs=0.03)
    assert rabi.peaks_count == 1 and 330 <= rabi.peak_doy <= 366
    assert rabi.amplitude > 0.4 and rabi.data_sufficient and not rabi.gap_flag
    kh = r.loc[(2022, "kharif")]
    assert kh.peaks_count == 0 and kh.amplitude < 0.05 and kh.dry_mean == pytest.approx(0.2, abs=0.02)
    assert r.loc[(2023, "rabi")].yoy_delta == pytest.approx(0.0, abs=0.02)
    assert np.isnan(rabi.yoy_delta)  # no previous year
    ann = r.loc[(2022, "annual")]
    assert ann.integral > 0 and ann.n_obs >= 70
    assert not ser.empty and set(ser.columns) >= {"ndvi_s", "bsi_s", "ndwi_s"}
    assert all("signal needing field verification" in c for c in df.caveats)


def test_gap_evaluated_per_season_not_series():
    df, _, _ = fb.build(_synthetic_obs(drop_monsoon=True), CFG, lam=100.0)
    r = df.set_index(["ag_year", "season"])
    k = r.loc[(2023, "kharif")]
    assert k.n_obs == 0 and not k.data_sufficient and k.gap_flag
    assert r.loc[(2023, "rabi")].data_sufficient  # the neighbouring season is unaffected
    assert any("insufficient_data" in c for c in k.caveats)


def test_mixed_pixel_usable_and_capped():
    df, _, _ = fb.build(_synthetic_obs(mixed=True, n_px=4), CFG, lam=100.0)
    assert df.mixed_pixel.all() and (df.confidence_cap < 1).all()
    assert df.set_index(["ag_year", "season"]).loc[(2022, "rabi")].data_sufficient
    # the same small parcel without the D-026 flag has no usable obs
    df2, _, _ = fb.build(_synthetic_obs(mixed=False, n_px=4), CFG, lam=100.0)
    assert not df2.data_sufficient.any()


def test_usable_mask_valid_frac():
    o = _synthetic_obs().head(3).copy()
    o["valid_frac"] = [1.0, 0.4, 0.6]
    assert fb.usable_mask(o, CFG).tolist() == [True, False, True]


def test_neighbour_context():
    df = pd.DataFrame({"parcel_uid": ["a", "b", "c", "a", "b", "c"], "ag_year": [2022] * 6,
                       "season": ["rabi"] * 3 + ["summer"] * 3, "amplitude": [0.1, 0.3, 0.5, 0.2, 0.2, 0.2],
                       "data_sufficient": [True, True, True, True, True, False]})
    out = fb.add_neighbour_context(df, {"a": ["b", "c"], "b": ["a"], "c": []})
    assert out.nbr_amp_med_300m.iloc[0] == pytest.approx(0.4)
    assert out.nbr_amp_med_300m.iloc[1] == pytest.approx(0.1)
    assert np.isnan(out.nbr_amp_med_300m.iloc[2]) and out.nbr_n.iloc[2] == 0
    assert out.nbr_n.iloc[3] == 1  # c is not data_sufficient in summer


def test_supported_days_and_long_gap_window_is_null():
    m = fb.supported_days(np.array([10, 40, 200]), 260, max_gap=45, halo=15)
    assert m[10:41].all() and m[185:216].all() and not m[60:180].any() and not m[0:0].any()
    # two years of data with a whole missing year in between: the empty year gets no smoothed features
    o = _synthetic_obs()
    o = o[(o.date < "2022-10-01") | (o.date > "2023-10-01")]
    df, ser, _ = fb.build(o, CFG, lam=100.0)
    r = df.set_index(["ag_year", "season"]).loc[(2022, "summer")]
    assert r.n_obs == 0 and not r.data_sufficient and r.ndvi_max is None or np.isnan(r.ndvi_max)
    assert "supported" in ser.columns and not ser.supported.all()
