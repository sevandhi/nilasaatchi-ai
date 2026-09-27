"""T5.1 matcher: synthetic fixtures + one real-data check."""
from __future__ import annotations

import pathlib

import pytest

from pipeline.match.core import (Context, VillageIndex, make_parcel, match_record, norm_token,
                                 ocr_variants, parse_refs)

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def index():
    a = [make_parcel("A|177/4A", "177/4A", 3, 7, 0.50), make_parcel("A|177/4B", "177/4B", 3, 7, 0.40),
         make_parcel("A|173/1", "173/1", 2, 7, 1.20), make_parcel("A|173/2", "173/2", 2, 7, 0.80),
         make_parcel("A|168", "168", 1, 7, 2.00), make_parcel("A|16/3-4-5", "16/3-4-5", 1, 7, 0.30),
         make_parcel("A|25/1A", "25/1A", 1, 7, 0.9), make_parcel("A|25/1B", "25/1B", 1, 7, 0.9)]
    b = [make_parcel("B|168", "168", 5, 4, 1.00), make_parcel("B|380/2", "380/2", 5, 4, 1.00)]
    return {"A": VillageIndex(a), "B": VillageIndex(b)}


def test_norm_token():
    assert norm_token("4-A") == "4A"
    assert norm_token(" 4 a ") == "4A"
    assert norm_token("௧௭௭") == "177"
    assert norm_token("1C(PLOT)") == "1C"
    assert norm_token("00394") == "394"
    assert norm_token("அ") == "A"


def test_parse_refs_lists():
    assert parse_refs("177", "4A") == [("177", "4A")]
    assert parse_refs("26", "1E,42/4C") == [("26", "1E"), ("42", "4C")]
    assert parse_refs("1", "6,7,8,10A") == [("1", "6"), ("1", "7"), ("1", "8"), ("1", "10A")]
    assert parse_refs("172", "1 மற்றும் 172/2") == [("172", "1"), ("172", "2")]
    assert parse_refs("173/1", None) == [("173", "1")]
    assert parse_refs(None, None) == []


def test_ocr_variants():
    v = ocr_variants("38")
    assert "88" in v and "33" in v and "83" in v


def test_exact_kide(index):
    o = match_record(index, "A", "177", "4A", Context())
    assert o.status == "accepted" and o.parcel_uid == "A|177/4A"
    assert o.candidates[0]["tier"] == "exact"


def test_normalised(index):
    o = match_record(index, "A", "177", "4-a", Context())
    assert o.parcel_uid == "A|177/4A"
    o = match_record(index, "A", "16", "3-4-5", Context())   # Excel-mangled sub_div: KIDE wins
    assert o.parcel_uid == "A|16/3-4-5"


def test_never_cross_village(index):
    o = match_record(index, "B", "177", "4A", Context())
    assert o.status == "no_candidate" and o.parcel_uid is None and o.links == []
    assert o.candidates[0]["other_village_hint"][0]["parcel_uid"] == "A|177/4A"
    assert match_record(index, "B", "168", None, Context()).parcel_uid == "B|168"
    assert match_record(index, None, "168", None, Context()).status == "no_village"


def test_parent_survey_expands(index):
    o = match_record(index, "A", "173", None, Context())
    assert o.status == "survey_level" and o.parcel_uid is None
    assert {l[0] for l in o.links} == {"A|173/1", "A|173/2"}
    assert all(l[1] == "survey_level" for l in o.links)


def test_subdiv_prefix_ambiguous(index):
    o = match_record(index, "A", "25", "1", Context())
    assert o.status == "ambiguous" and o.parcel_uid is None and len(o.candidates) == 2


def test_ocr_penalty_needs_support(index):
    # 178/1 -> 173/1 needs one swap (8->3): 0.65 < tau -> ambiguous, candidate kept
    o = match_record(index, "A", "178", "1", Context())
    assert o.status == "ambiguous" and o.candidates[0]["parcel_uid"] == "A|173/1"
    assert "ocr_variant" in o.candidates[0]["reasons"][0]


def test_block_disagreement_lowers_score(index):
    ok = match_record(index, "A", "173", "1", Context(block_no=2, unit_no=7))
    bad = match_record(index, "A", "173", "1", Context(block_no=5, unit_no=7))
    assert ok.score > bad.score


def test_extent_far_penalty(index):
    o = match_record(index, "A", "177", "4B", Context(extent_ha=5.0, village_from_header=True))
    assert o.score < 0.9 and any("extent_far" in r for r in o.candidates[0]["reasons"])


def test_list_links_each_member(index):
    o = match_record(index, "A", "177", "4A,173/2", Context())
    assert o.status == "list"
    assert {(l[0], l[1]) for l in o.links} == {("A|177/4A", "list_member"), ("A|173/2", "list_member")}


def test_reresolve_scheme_village_by_unit_block(index):
    # header "A" (scheme name) but ref 380/2 exists only in B, and B's parcel is in unit 4 block 5
    ctx = Context(block_no=5, unit_no=4, header_is_scheme=True)
    o = match_record(index, "A", "380", "2", ctx)
    assert o.status == "accepted" and o.parcel_uid == "B|380/2" and o.village == "B"
    assert "village_reresolved_from_unit_block" in o.candidates[0]["reasons"]
    # block disagrees -> no re-resolution, only the review hint
    o = match_record(index, "A", "380", "2", Context(block_no=9, unit_no=4, header_is_scheme=True))
    assert o.status == "no_candidate" and o.parcel_uid is None
    # header is a real village (not the scheme) -> never re-resolved
    o = match_record(index, "A", "380", "2", Context(block_no=5, unit_no=4))
    assert o.status == "no_candidate"


def test_block_level_event_links():
    from pipeline.match.__main__ import block_level
    idx = {"A": VillageIndex([make_parcel("A|1/1", "1/1", 2, 7), make_parcel("A|1/2", "1/2", 2, 7),
                              make_parcel("A|9", "9", 3, 7)]),
           "B": VillageIndex([make_parcel("B|5", "5", 4, 4)])}
    o = block_level(idx, "A", Context(block_no=2, unit_no=7), 0.5)
    assert o.status == "block_level" and {l[0] for l in o.links} == {"A|1/1", "A|1/2"}
    assert all(l[1] == "block_level" and l[2] == 0.5 for l in o.links)
    o = block_level(idx, "A", Context(block_no=4, unit_no=4, header_is_scheme=True), 0.5)
    assert o.village == "B" and [l[0] for l in o.links] == ["B|5"]
    assert block_level(idx, "A", Context(block_no=None), 0.5) is None


def _dsn():
    from dotenv import dotenv_values
    return dotenv_values(ROOT / ".env").get("DATABASE_URL")


def test_real_data_matches_never_cross_village():
    """Real data: every linked parcel lies in the row's resolved village."""
    psycopg = pytest.importorskip("psycopg")
    dsn = _dsn()
    if not dsn:
        pytest.skip("no DATABASE_URL")
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"db down: {e}")
    with conn:
        n = conn.execute("SELECT count(*) FROM parcel_fact WHERE match_status IS NOT NULL").fetchone()[0]
        if n == 0:
            pytest.skip("matcher not run")
        bad = conn.execute("""
            SELECT count(*) FROM parcel_fact f JOIN parcel_fact_link l ON l.fact_id = f.id
            WHERE f.match_village IS NOT NULL AND split_part(l.parcel_uid, '|', 1) <> f.match_village""").fetchone()[0]
        assert bad == 0
        acc = conn.execute("SELECT count(*) FROM parcel_fact WHERE match_status='accepted' AND parcel_uid IS NULL").fetchone()[0]
        assert acc == 0
