import datetime as dt

import numpy as np
import pandas as pd
import pytest

from planet.classify import rules
from planet.classify import student as st
from planet.classify import teacher as te
from planet.events import windows as ew


def _row(**kw):
    base = {"season": "rabi", "data_sufficient": True, "ndvi_max": 0.7, "ndvi_min": 0.2, "amplitude": 0.5,
            "dry_mean": 0.25, "ndwi_max": -0.3, "peaks_count": 1, "nbr_amp_med_300m": 0.45, "yoy_delta": 0.0,
            "n_obs": 10, "max_gap_days": 20}
    return {**base, **kw}


def test_season_rules():
    assert rules.season_rule(_row())[0] == "cropped"
    assert rules.season_rule(_row(data_sufficient=False))[0] == "insufficient_data"
    assert rules.season_rule(_row(ndvi_max=0.25, amplitude=0.05, yoy_delta=-0.3))[0] == "cleared_or_built"
    assert rules.season_rule(_row(dry_mean=0.45, amplitude=0.15, peaks_count=0))[0] == "perennial_veg"
    assert rules.season_rule(_row(amplitude=0.15, peaks_count=0))[0] == "bare_fallow"
    assert rules.season_rule(_row(season="summer", ndvi_max=0.3, ndvi_min=0.33, amplitude=0.05, peaks_count=0))[0] == "perennial_veg"
    assert rules.season_rule(_row(season="kharif", ndvi_max=0.6, amplitude=0.35))[0] == "cropped"
    assert rules.season_rule(_row(ndwi_max=0.2, ndvi_max=0.1))[0] == "water"


def test_irrigated_multi_per_ag_year():
    f = pd.DataFrame([{**_row(season="rabi"), "parcel_uid": "V|1", "ag_year": 2022},
                      {**_row(season="summer", ndvi_max=0.6, amplitude=0.3), "parcel_uid": "V|1", "ag_year": 2022},
                      {**_row(season="rabi"), "parcel_uid": "V|2", "ag_year": 2022}])
    d = rules.apply(f)
    assert d.set_index(["parcel_uid", "season"]).loc[("V|1", "rabi"), "prior_state"] == "irrigated_multi"
    assert d.set_index(["parcel_uid", "season"]).loc[("V|2", "rabi"), "prior_state"] == "cropped"


def test_vote():
    assert te.vote({"gemini": "cropped", "cohere": "cropped", "prior": "cropped"}) == ("cropped", "3of3")
    assert te.vote({"gemini": "cropped", "cohere": "bare_fallow", "prior": "cropped"}) == ("cropped", "2of3")
    assert te.vote({"gemini": "water", "cohere": "bare_fallow", "prior": "cropped"}) == (None, "none")
    assert te.vote({"gemini": None, "cohere": "cropped", "prior": "cropped"}) == ("cropped", "2of2")
    assert te.vote({"gemini": None, "cohere": "water", "prior": "cropped"}) == (None, "split")
    assert te.vote({"gemini": "water", "cohere": float("nan"), "prior": "cropped"}, {"gemini": 0.8}) == ("water", "split_vlm")
    assert te.vote({"gemini": "water", "cohere": None, "prior": "cropped"}, {"gemini": 0.4}) == (None, "split")
    assert te.vote({"gemini": None, "cohere": None, "prior": "cropped"}) == (None, "single")


def test_parse_answer_and_prompt_version():
    a = te.parse_answer({"state": "Cropped", "confidence": 85, "visual_evidence": "x", "caveats": "clouds"})
    assert a["state"] == "cropped" and a["confidence"] == pytest.approx(0.85) and a["caveats"] == ["clouds"]
    with pytest.raises(ValueError):
        te.parse_answer({"state": "forest", "confidence": 0.5})
    assert te.prompt_version("<!-- prompt_version: satellite_teacher-v9 (x) -->\nbody") == "satellite_teacher-v9"
    assert te.prompt_version().startswith("satellite_teacher-v")


def test_prompt_has_no_owner_fields():
    txt = te.prompt_text().lower()
    assert not txt.startswith("<!--") and "prompt_version" not in txt
    for bad in ("owner", "patta", "survey", "{parcel", "village"):
        assert bad not in txt


def test_stratified_sample_includes_demo_and_audit():
    rng = np.random.default_rng(0)
    n = 900
    cand = pd.DataFrame({"parcel_uid": [f"V{i % 3}|{i}" for i in range(n)], "ag_year": 2022,
                         "season": rng.choice(["kharif", "rabi", "summer"], n),
                         "prior_state": rng.choice(["cropped", "bare_fallow", "perennial_veg"], n),
                         "amp_q": rng.integers(0, 3, n)})
    cand["village"] = cand.parcel_uid.str.split("|").str[0]
    cand["item_id"] = [te.item_id(u, y, s) for u, y, s in zip(cand.parcel_uid, cand.ag_year, cand.season, strict=True)]
    demo = [(cand.parcel_uid[0], 2022, cand.season[0])]
    s = te.stratified_sample(cand, n=120, demo=demo)
    assert 110 <= len(s) <= 125 and s.item_id.is_unique
    assert s.is_demo.sum() == 1 and (s.label_set == "audit").sum() == te.N_AUDIT
    assert not s[s.is_demo].label_set.eq("audit").any()


def test_pav_and_calibrator_monotone():
    x = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    y = np.array([0, 1, 0, 1, 1, 1], float)
    xk, yk = st.pav(x, y)
    fit = np.interp(x, xk, yk)
    assert np.all(np.diff(fit) >= -1e-12) and fit[0] == pytest.approx(0.0)
    p = np.array([[0.7, 0.3], [0.4, 0.6], [0.9, 0.1], [0.2, 0.8]])
    yy = np.array(["a", "b", "a", "b"])
    cal = st.Calibrator(["a", "b"]).fit(p, yy)
    q = cal.transform(p)
    assert np.allclose(q.sum(axis=1), 1.0)
    assert st.Calibrator.from_json(cal.to_json()).transform(p) == pytest.approx(q)


def test_metrics():
    y = ["a", "a", "b", "b"]
    p = ["a", "b", "b", "b"]
    cm = st.confusion(y, p, ["a", "b", "c"])
    assert cm.loc["a", "b"] == 1 and cm.to_numpy().sum() == 4
    assert st.macro_f1(y, p, ["a", "b", "c"]) == pytest.approx((2 / 3 + 0.8) / 2)


def _pred(**kw):
    base = {"parcel_uid": "V|1", "ag_year": 2024, "season": "rabi", "data_sufficient": True, "gap_flag": False,
            "confidence_cap": None, "mixed_pixel": False, "caveats": "", "state": "cropped", "p_raw": 0.9, "probs": {}}
    return {**base, **kw}


def test_route_policy_and_caps():
    df = pd.DataFrame([_pred(), _pred(season="summer", p_raw=0.5), _pred(season="kharif", p_raw=0.5),
                       _pred(ag_year=2023, gap_flag=True), _pred(ag_year=2022, confidence_cap=0.6),
                       _pred(ag_year=2021, data_sufficient=False), _pred(ag_year=2020, p_raw=0.5)])
    second = {("V|1", 2024, "summer"): {"state": "cropped"}, ("V|1", 2024, "kharif"): {"state": "bare_fallow"}}
    r = st.route(df, second).set_index(["ag_year", "season"])
    assert r.loc[(2024, "rabi"), "route"] == "student"
    assert r.loc[(2024, "summer"), "route"] == "vlm_second_opinion"
    assert r.loc[(2024, "kharif"), "route"] == "disagreement" and r.loc[(2024, "kharif"), "needs_field"]
    assert r.loc[(2023, "rabi"), "p_state"] == pytest.approx(0.6) and r.loc[(2023, "rabi"), "route"] == "pending_second_opinion"
    assert r.loc[(2022, "rabi"), "p_state"] == pytest.approx(0.6)
    assert r.loc[(2021, "rabi"), "state"] == "insufficient_data" and r.loc[(2021, "rabi"), "route"] == "rule"


def test_annual_state():
    s = pd.DataFrame([
        {"parcel_uid": "V|1", "ag_year": 2024, "season": "rabi", "state": "cropped", "p_state": 0.9, "route": "student", "needs_field": False},
        {"parcel_uid": "V|1", "ag_year": 2024, "season": "summer", "state": "cropped", "p_state": 0.8, "route": "student", "needs_field": False},
        {"parcel_uid": "V|1", "ag_year": 2024, "season": "kharif", "state": "bare_fallow", "p_state": 0.8, "route": "student", "needs_field": False},
        {"parcel_uid": "V|2", "ag_year": 2024, "season": "rabi", "state": "insufficient_data", "p_state": np.nan, "route": "rule", "needs_field": False}])
    a = st.annual_state(s).set_index("parcel_uid")
    assert a.loc["V|1", "state"] == "irrigated_multi" and a.loc["V|1", "p_state"] == pytest.approx(0.8)
    assert a.loc["V|2", "state"] == "insufficient_data"


def test_event_windows_assignment():
    fb = ew.load_fallbacks()
    ev = ew.event_dates("Melathattaparai|233", fb)
    assert ev["t_possession"]["date"] == dt.date(2025, 3, 21) and ev["t_possession"]["level"] == "parcel"
    assert ew.event_dates("Allikulam|1/1", fb)["t_possession"]["date"] == dt.date(2025, 2, 27)
    assert ew.event_dates("Umarikottai|1", fb)["t_possession"]["level"] == "default"
    today = dt.date(2026, 9, 26)
    b = ew.window_bounds(ev, today)
    assert b["pre_notification"] == (dt.date(2021, 10, 28), dt.date(2022, 10, 28))
    assert ew.assign_window(dt.date(2022, 10, 1), dt.date(2023, 2, 28), b, today) == "pending"
    assert ew.assign_window(dt.date(2021, 10, 1), dt.date(2022, 2, 28), b, today) == "pre_notification"
    assert ew.assign_window(dt.date(2025, 3, 1), dt.date(2025, 5, 31), b, today) == "post_possession"
    assert ew.assign_window(dt.date(2019, 6, 1), dt.date(2019, 9, 30), b, today) is None
    states = pd.DataFrame([{"parcel_uid": "Melathattaparai|233", "ag_year": 2025, "season": s, "state": st_,
                            "p_state": 0.8, "route": "student", "needs_field": False}
                           for s, st_ in (("kharif", "bare_fallow"), ("rabi", "cropped"), ("summer", "insufficient_data"))])
    w = {r["window_name"]: r for r in ew.parcel_windows("Melathattaparai|233", states, fb, today)}
    pp = w["post_possession"]
    # ag_year 2025 summer = Mar-May 2026: all three seasons fall after the 2025-03-21 handover
    assert pp["n_seasons"] == 3 and pp["n_sufficient"] == 2 and pp["crop_seasons"] == 1 and pp["state_share"] == {"bare_fallow": 0.5, "cropped": 0.5}
