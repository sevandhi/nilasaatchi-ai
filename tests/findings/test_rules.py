"""T5.3 findings rules on synthetic fixtures + real-data checks (skipped when the DB is down)."""
from __future__ import annotations

import pathlib
from datetime import date

import pytest

from pipeline.findings import rules
from pipeline.findings.evidence_pack import MASK, mask_owner

ROOT = pathlib.Path(__file__).resolve().parents[2]


def did(d, lo, hi, fl=None):
    return {"did": d, "ci_lo": lo, "ci_hi": hi, "farmland_like_p": fl, "n_pre": 3, "n_post": 1}


def test_pv3_drop_and_lead():
    ps = [{"parcel_uid": "V|1", "possession_level": "parcel", "possession_date": date(2024, 12, 1),
           "vigour": did(-1.2, -2.0, -0.3, 0.9), "plough": did(0.1, -0.5, 0.6)},
          {"parcel_uid": "V|2", "possession_level": "block", "possession_date": date(2024, 12, 1),
           "vigour": did(0.3, -0.1, 0.8, 0.95), "plough": did(2.1, 0.8, 3.7)},
          {"parcel_uid": "V|3", "possession_level": None, "vigour": did(-1, -2, -0.5, 1)},       # no possession
          {"parcel_uid": "V|4", "possession_level": "parcel", "vigour": did(0.2, -0.1, 0.5, 0.4),
           "plough": did(2.0, 0.5, 3.0)}]                                                         # not farmland-like
    out = {f.key: f for f in rules.pv3_post_possession(ps)}
    assert set(out) == {"PV3|drop|V|1", "PV3|farmed|V|2"}
    assert out["PV3|farmed|V|2"].evidence_level == "block" and rules.BLOCK_CAVEAT in out["PV3|farmed|V|2"].caveats
    assert out["PV3|drop|V|1"].confidence > out["PV3|farmed|V|2"].confidence
    assert all(len(f.caveats) >= 4 for f in out.values())


def test_pv4_by_block():
    today = date(2026, 9, 27)
    ps = [{"parcel_uid": "V|1", "village": "V", "unit_id": 1, "block_id": 2, "area_ha": 3.0, "possession_level": "parcel",
           "possession_date": date(2025, 1, 1), "states_since": ["bare_fallow"], "dist_major_road_m": 1500, "dist_substation_m": 800},
          {"parcel_uid": "V|2", "village": "V", "unit_id": 1, "block_id": 2, "area_ha": 2.5, "possession_level": "block",
           "possession_date": date(2025, 3, 1), "states_since": ["cropped"], "dist_major_road_m": 900, "dist_substation_m": 1200},
          {"parcel_uid": "V|3", "village": "V", "unit_id": 1, "block_id": 2, "area_ha": 9, "possession_level": "parcel",
           "possession_date": date(2025, 1, 1), "states_since": ["cleared_or_built"]},             # built -> excluded
          {"parcel_uid": "V|4", "village": "V", "unit_id": 1, "block_id": 3, "area_ha": 1, "possession_level": "parcel",
           "possession_date": date(2026, 8, 1), "states_since": []}]                               # < 6 months
    out = rules.pv4_idle_land_bank(ps, today)
    assert len(out) == 1
    f = out[0]
    assert f.metrics["idle_ha_sum"] == 5.5 and f.metrics["n_parcels"] == 2
    assert f.metrics["min_major_road_km"] == 0.9 and f.metrics["min_substation_km"] == 0.8
    assert f.evidence_level == "parcel" and f.severity == "medium"


def test_pv1_dry_irrigated():
    seasons = [{"ag_year": y, "season": "rabi", "state": "irrigated_multi"} for y in (2019, 2020)] + \
              [{"ag_year": 2021, "season": "rabi", "state": "cropped"}, {"ag_year": 2023, "season": "rabi", "state": "irrigated_multi"}]
    p = {"parcel_uid": "V|1", "classification": "DRY", "t_3_1": date(2022, 10, 28), "seasons": seasons}
    out = rules.pv1_classification([p])
    assert len(out) == 1 and out[0].metrics["irrigated_years"] == [2019, 2020]   # 2023 is after 3(1)
    p2 = {**p, "seasons": seasons[2:]}
    assert rules.pv1_classification([p2]) == []


def test_extent_mismatch_and_unit_error():
    base = {"document_id": 1, "page_no": 1, "part": False, "match_score": 1.0}
    ps = [{"parcel_uid": "V|ok", "area_ha": 1.0, "claims": [{**base, "extraction_id": 1, "extent_ha": 0.97}]},
          {"parcel_uid": "V|coowners", "area_ha": 1.0,     # two co-owner rows summing to the parcel
           "claims": [{**base, "extraction_id": 2, "extent_ha": 0.5}, {**base, "extraction_id": 3, "extent_ha": 0.49}]},
          {"parcel_uid": "V|bad", "area_ha": 1.0, "claims": [{**base, "extraction_id": 4, "extent_ha": 1.3}]},
          {"parcel_uid": "V|unit", "area_ha": 1.0, "claims": [{**base, "extraction_id": 5, "extent_ha": 127.0}]},
          {"parcel_uid": "V|part", "area_ha": 1.0, "claims": [{**base, "extraction_id": 6, "extent_ha": 0.2, "part": True}]}]
    out = {f.parcel_uid: f for f in rules.extent_mismatch(ps)}
    assert set(out) == {"V|bad", "V|unit"}
    assert out["V|bad"].category == "EXTENT_MISMATCH" and out["V|bad"].metrics["diff_ha"] == 0.3
    assert out["V|bad"].severity == "medium"
    assert out["V|unit"].category == "EXTRACTION_ERROR"


def test_compensation():
    rows = [{"extraction_id": i, "page_no": 1, "extent_ac": ac, "land_amount_rs": ac * 500_000}
            for i, ac in enumerate([1.0, 2.5, 0.8, 1.2], start=1)]
    rows.append({"extraction_id": 9, "page_no": 1, "extent_ac": 19.14, "land_amount_rs": 9_564_800})  # 19.14 × 5L = 95,70,000
    rows.append({"extraction_id": 10, "page_no": 1, "extent_ac": 1.0, "land_amount_rs": 50_000_000})    # misread
    rows.append({"extraction_id": 11, "page_no": 1, "extent_ac": 1.004, "land_amount_rs": 500_000})     # acre rounding
    assert rules.document_rate(rows) == 500_000
    out = {f.key: f for f in rules.compensation_mismatch({7: rows})}
    assert set(out) == {"COMP|9", "EXTR|amount|10"}
    assert out["COMP|9"].metrics["diff_rs"] == -5200 and out["COMP|9"].severity == "low"
    assert rules.compensation_mismatch({8: rows[:2]}) == []                                              # too few rows


def test_doc_version_conflict():
    go = {"A": 186.455, "K": 193.15, "M": 175.835, "U": 106.01, "P": 141.06, "R": 95.23, "S": 6.66}
    prop = {**go, "K": 195.48, "R": 99.895}
    part = {"A": 4.155, "K": 1.265, "M": 0.85, "U": 2.1, "P": 0.52, "R": 0.26, "S": 0.1}   # unit sub-table
    src = []
    for n, (tab, dt, doc) in enumerate(((go, "GO", 2), (prop, "AS", 1), (part, "AS", 5))):
        src += [{"village": v, "total_ha": t, "doc_type": dt, "document_id": doc, "page_no": 1 + n,
                 "path": f"x/{dt}.pdf", "extraction_id": 100 * n + i} for i, (v, t) in enumerate(tab.items())]
    out = {f.village: f for f in rules.doc_version_conflict(src, {"K": 196.028})}
    assert set(out) == {"K", "R"}
    assert out["K"].metrics["sanction_ha"] == 193.15 and out["K"].metrics["spread_ha"] == 2.33
    assert out["K"].metrics["fmb_union_ha"] == 196.028 and out["K"].severity == "high"


def test_fmb_quality():
    out = rules.fmb_quality([{"uid_a": "A|1", "uid_b": "B|2", "overlap_ha": 0.46, "cross_village": True, "village_a": "A"},
                             {"uid_a": "A|3", "uid_b": "A|4", "overlap_ha": 0.0001, "cross_village": False, "village_a": "A"}],
                            [{"uid": "A|5", "village": "A", "survey_no": "5", "outside_ha": 0.02, "outside_pct": 3.1}])
    assert [f.key for f in out] == ["FMB|overlap|A|1|B|2", "FMB|outside|A|5"]
    assert out[0].severity == "high" and out[1].severity == "low"


def test_mask_owner():
    assert mask_owner({"owner": "x", "raw": {"owner_vlm_raw": "y", "survey_no": "1"}, "rows": [{"account_no": 1}]}) == \
        {"owner": MASK, "raw": {"owner_vlm_raw": MASK, "survey_no": "1"}, "rows": [{"account_no": MASK}]}


# ------------------------------------------------------------------ real data
@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    from dotenv import dotenv_values
    dsn = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    try:
        c = psycopg.connect(dsn, connect_timeout=3)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"db down: {e}")
    if not c.execute("SELECT to_regclass('finding')").fetchone()[0] or \
            not c.execute("SELECT count(*) FROM finding").fetchone()[0]:
        pytest.skip("findings not run")
    yield c
    c.close()


def test_real_doc_version_reproduces_ground_truth(conn):
    rows = dict(conn.execute("""SELECT village, metrics->'values_ha' FROM finding
                                WHERE category = 'DOC_VERSION_CONFLICT' AND status = 'open'""").fetchall())
    assert [193.15, 195.48] == [round(x, 3) for x in rows["Keelathattaparai"]]
    assert [95.23, 99.895] == [round(x, 3) for x in rows["Ramasamypuram"]]


def test_real_evidence_pack(conn):
    from pipeline.findings.evidence_pack import evidence_pack
    fid = conn.execute("SELECT id FROM finding WHERE category LIKE 'PV3%%' ORDER BY id LIMIT 1").fetchone()[0]
    p = evidence_pack(fid, conn=conn)
    assert p["paper"] and p["planet"]["parcel_did"] and p["caveats"]
    assert "த/பெ" not in str(p["paper"])
