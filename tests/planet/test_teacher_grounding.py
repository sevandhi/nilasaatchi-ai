import numpy as np
import pandas as pd

from planet.classify import teacher as te


def _sm(vals, start="2024-03-01"):
    d = pd.date_range(start, periods=len(vals), freq="5D")
    return pd.DataFrame({"date": d, "ndvi_s": vals, "supported": True})


A, B = pd.Timestamp("2024-03-01"), pd.Timestamp("2024-05-31")


def test_measured_curve_shapes():
    n = 19
    assert te.measured_curve(_sm(np.linspace(0.6, 0.2, n)), A, B) == "falling"
    assert te.measured_curve(_sm(np.linspace(0.2, 0.6, n)), A, B) == "rising"
    assert te.measured_curve(_sm(np.full(n, 0.25) + np.linspace(0, 0.05, n)), A, B) == "flat"
    bump = 0.2 + 0.4 * np.exp(-((np.arange(n) - 9) ** 2) / 8)
    assert te.measured_curve(_sm(bump), A, B) == "peak_inside"
    gaps = _sm(bump).assign(supported=[i % 3 == 0 for i in range(n)])
    assert te.measured_curve(gaps, A, B) == "gappy"


def test_vote_requires_curve_and_consistency():
    exp = {"ndvi_max": 0.62, "chip_dates": ["2024-02-25", "2024-03-06", "2024-05-20"], "curve": "falling"}
    reads = {"read_ndvi_max": 0.62, "read_chip_dates": exp["chip_dates"], "curve_in_band": "peak_inside"}
    g = te.grounding(reads, exp, "cropped")
    assert g["grounded"] and g["curve_ok"] is False and not g["vote_ok"]
    ok = te.grounding({**reads, "curve_in_band": "falling"}, exp, "bare_fallow")
    assert ok["vote_ok"] and ok["consistent"]
    # a crop state contradicting the teacher's own reads is inconsistent even when the reads are right
    bad = te.grounding({**reads, "curve_in_band": "falling"}, exp, "cropped")
    assert not bad["consistent"] and not bad["vote_ok"]
    low = te.grounding({**reads, "read_ndvi_max": 0.25, "curve_in_band": "falling"},
                       {**exp, "ndvi_max": 0.25}, "cropped")
    assert not low["vote_ok"]


def test_pilot_items_train_only_round_robin():
    items = pd.DataFrame({"item_id": [f"i{k}" for k in range(60)],
                          "season": ["kharif", "rabi", "summer"] * 20,
                          "label_set": ["audit"] * 15 + ["train"] * 45})
    p = te.pilot_items(items, 9)
    assert len(p) == 9 and (p.label_set == "train").all()
    assert p.season.value_counts().to_dict() == {"kharif": 3, "rabi": 3, "summer": 3}


def test_slot_prompts_versioned_and_public():
    for slot in ("gemini", "cohere4"):
        txt = te.slot_prompt(slot).read_text()
        assert te.prompt_version(slot=slot).startswith("satellite_teacher-v")
        assert "PUBLIC tier" in txt.splitlines()[0]
    assert te.prompt_version(slot="cohere4") != te.prompt_version(slot="gemini")
    assert set(te.VOTE_SLOTS) == {"gemini", "cohere"}
