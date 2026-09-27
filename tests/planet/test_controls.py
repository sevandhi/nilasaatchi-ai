import numpy as np
import pandas as pd
import pytest

from planet.classify import teacher as te
from planet.controls import cells as cc
from planet.controls import relative as rel


def _series(onset="2023-10-20", plough_bsi=0.30, base_bsi=0.15):
    days = pd.date_range("2023-05-01", "2024-03-31", freq="5D")
    t = (days - pd.Timestamp(onset)).days.to_numpy()
    ndvi = 0.2 + 0.5 / (1 + np.exp(-t / 8.0)) * np.exp(-np.clip(t - 60, 0, None) / 40.0)
    sm = pd.DataFrame({"date": days, "ndvi_s": ndvi, "supported": True})
    obs = sm.rename(columns={"ndvi_s": "ndvi"}).drop(columns="supported").copy()
    rng = np.random.default_rng(0)
    obs["bsi"] = base_bsi + rng.normal(0, 0.005, len(obs))
    win = (obs.date >= pd.Timestamp(onset) - pd.Timedelta(days=40)) & (obs.date <= pd.Timestamp(onset) - pd.Timedelta(days=16))
    obs.loc[win, "bsi"] = plough_bsi
    return obs, sm


def test_plough_detects_bsi_spell_before_greenup():
    obs, sm = _series()
    r = rel.plough_one(obs, sm, pd.Timestamp("2023-10-01"), pd.Timestamp("2024-02-29"))
    assert r["plough_reason"] == "ok" and r["plough_signal"] > rel.PLOUGH_Z
    assert pd.Timestamp(r["plough_date"]) < pd.Timestamp(r["greenup_onset"])


def test_plough_absent_and_no_greenup():
    obs, sm = _series(plough_bsi=0.15)
    r = rel.plough_one(obs, sm, pd.Timestamp("2023-10-01"), pd.Timestamp("2024-02-29"))
    assert r["plough_reason"] == "ok" and r["plough_signal"] < rel.PLOUGH_Z
    flat = sm.assign(ndvi_s=0.2)
    assert rel.plough_one(obs.assign(ndvi=0.2), flat, pd.Timestamp("2023-10-01"),
                          pd.Timestamp("2024-02-29"))["plough_reason"] == "no_greenup"


def test_did_group_detects_drop_and_null():
    rng = np.random.default_rng(1)
    S, K = 10, 200
    C = rng.normal(0, 1, (K, S))
    same = C.mean(axis=0) + rng.normal(0, 0.05, S)
    drop = same.copy()
    drop[6:] -= 2.0
    Z = np.vstack([same, drop])
    pre, post = np.arange(6), np.arange(6, S)
    r = rel.did_group(Z, C, pre, post, np.random.default_rng(2), -1.0)
    assert r["ci_lo"][0] < 0 < r["ci_hi"][0] and abs(r["did"][0]) < 0.3
    assert r["ci_hi"][1] < 0 and r["did"][1] == pytest.approx(-2.0, abs=0.3)
    assert r["farmland_like_p"][0] > 0.9 and r["farmland_like_p"][1] < 0.1
    assert r["n_post"][0] == 4


def test_control_select_filters_and_thins():
    n = 400
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x0": rng.integers(0, 50, n) * 80.0, "y0": rng.integers(0, 50, n) * 80.0,
                       "frac_cropland": rng.choice([0.9, 0.5], n), "frac_tree": 0.0, "frac_built": rng.choice([0.0, 0.1], n),
                       "frac_water": 0.0, "frac_wetland": 0.0, "wc_valid_px": 64, "touches_water": False,
                       "near_other_sipcot": False, "sector": rng.choice(["N", "S"], n), "dist_km": 3.0})
    df = df.drop_duplicates(["x0", "y0"])
    s = cc.select(df, n=1000)
    assert (s.frac_cropland >= 0.8).all() and (s.frac_built == 0).all()
    blocks = (s.x0 // cc.BLOCK_M).astype(int).astype(str) + "_" + (s.y0 // cc.BLOCK_M).astype(int).astype(str)
    assert blocks.is_unique and s.cell_id.is_unique


def test_grounding_and_unclear():
    a = te.parse_answer({"state": "unclear", "confidence": 0.3, "visual_evidence": "", "caveats": [],
                         "read_ndvi_max": 0.18, "read_chip_dates": ["2019-06-08", "2019-06-08", "2019-09-11"],
                         "curve_in_band": "flat"})
    assert a["state"] is None and a["unclear"]
    exp = {"ndvi_max": 0.18, "chip_dates": ["2019-06-08", "2019-06-08", "2019-09-11"]}
    assert te.grounding(a["reads"], exp)["grounded"]
    bad = {**a["reads"], "read_ndvi_max": 0.76}
    g = te.grounding(bad, exp)
    assert not g["grounded"] and not g["ndvi_max_ok"] and g["dates_ok"]


def _rel_frames():
    rows_p, rows_c = [], []
    for y in (2019, 2020, 2021, 2022, 2023, 2024, 2025):
        for s in ("rabi",):
            rows_p.append({"parcel_uid": "V|1", "ag_year": y, "season": s, "vigour_z": 0.0, "plough_present": 0.0,
                           "plough_signal": -0.5})
            rows_p.append({"parcel_uid": "V|2", "ag_year": y, "season": s, "vigour_z": 0.0,
                           "plough_present": 1.0 if y < 2022 else 0.0, "plough_signal": 3.0 if y < 2022 else -1.0})
            for k in range(30):
                rows_c.append({"cell_id": f"c{k}", "ag_year": y, "season": s, "vigour_z": 0.0,
                               "plough_present": float(k < (6 if y < 2022 else 2)), "plough_signal": 0.0})
    return pd.DataFrame(rows_p), pd.DataFrame(rows_c)


def test_did_plough_floor_flag_and_plough_z():
    prel, crel = _rel_frames()
    fb = {"defaults": {"t_3_1": {"date": "2022-10-28"}, "t_award": {"date": "2024-01-01"},
                       "t_possession": {"date": "2025-03-21"}}}
    did = rel.compute_did(prel, crel, fb)
    assert set(did.metric) == {"vigour_z", "plough", "plough_z"}
    pl = did[(did.metric == "plough") & (did.event == "t_3_1") & (did.season_scope == "rabi")].set_index("parcel_uid")
    # parcel 1 never ploughs: its positive DiD is only the control-rate drop -> flagged uninformative
    assert pl.loc["V|1", "did"] > 0 and not pl.loc["V|1", "informative"]
    assert pl.loc["V|2", "informative"] and pl.loc["V|2", "did"] < 0
    pz = did[(did.metric == "plough_z") & (did.event == "t_3_1") & (did.season_scope == "rabi")].set_index("parcel_uid")
    assert pz.loc["V|2", "did"] == pytest.approx(-4.0, abs=0.01) and pz.loc["V|2", "informative"]
