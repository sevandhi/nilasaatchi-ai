"""Loader (pure parts + DB round trip in a rolled-back transaction) and batch runner guards."""
import json
import os

import pytest

from pipeline.extract import batch
from pipeline.load.extractions import content_hash, event_for_record, facts_from_record, strip_pii


def _page(**kw):
    p = {"image_sha": "ab" * 32, "schema_version": "p2.v1", "doc_id": None, "page_no": 1, "page_ref": "x.pdf#p1",
         "doc_type": "LDR", "status": "accepted", "self_consistency": "pass", "confidence": 0.8,
         "privacy_tier": "PII", "header": {"village": {"value": "Allikulam"}, "unit_no": {"value": "4"},
                                           "block_no": {"value": None}, "doc_date": {"value": "2025-03-21",
                                                                                     "precision": "day"},
                                           "handover_date": {"value": "2025-03-20", "precision": "day"}},
         "records": [], "queue_reasons": []}
    p.update(kw)
    return p


def _row(**kw):
    r = {"record_type": "parcel_row", "serial": "1", "survey_no": "177", "sub_div": "4a", "extent_ha": 0.605,
         "extent_ac": 1.494, "owner_raw": "PRIVATE NAME", "owners": [{"raw": "PRIVATE NAME", "name": "PRIVATE NAME",
                                                                    "key": "private name", "relation": None}],
         "raw": {"survey_no": "177/4a", "owner": "PRIVATE NAME"}, "table_role": None,
         "evidence": {"confidence": 0.9, "raw_cells": ["1", "177/4a", "PRIVATE NAME"], "bbox": [0, 0, 1, 1]}}
    r.update(kw)
    return r


def test_strip_pii_is_deep():
    rec = {"record_type": "errata_correction", "published_as": _row(), "should_read_as": _row()}
    assert "PRIVATE NAME" not in json.dumps(strip_pii(rec)) and "PRIVATE NAME" not in json.dumps(strip_pii(_row()))


def test_facts_normalise_refs():
    facts = {f["fact_type"]: f for f in facts_from_record(_row(), "Allikulam")}
    assert facts["extent_ha"]["survey_ref"] == "Allikulam|177|4A" and facts["extent_ha"]["unit"] == "ha"
    assert set(facts) == {"extent_ha", "extent_ac"}              # owner facts are added in SQL, token only


def test_ldr_event_uses_handover_date_and_skips_published_rows():
    p = _page()
    ev = event_for_record(p, _row(), 1, "Allikulam", content_hash(p))
    assert (ev["stage"], ev["sub_stage"], ev["event_date"]) == ("POSSESSION", "handover", "2025-03-20")
    assert event_for_record(p, _row(table_role="published_as"), 2, "Allikulam", "h") is None


@pytest.mark.db
def test_load_page_round_trip_and_idempotent():
    import psycopg
    from dotenv import load_dotenv

    from pipeline.load.extractions import load_page
    load_dotenv(".env")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        try:
            p = _page(image_sha="cd" * 32, records=[_row()], status="needs_human",
                      queue_reasons=["self_consistency_fail"])
            s1 = load_page(conn, p)
            s2 = load_page(conn, p)
            assert s1["extractions"] == 2 and s1["events"] == 1 and s1["queue"] == 1 and s2["skipped"] == 1
            ch = content_hash(p)
            row_json = conn.execute("SELECT row_json::text FROM extraction WHERE content_hash=%s AND record_no=1",
                                    (ch,)).fetchone()[0]
            assert "PRIVATE NAME" not in row_json
            tok = conn.execute("SELECT value_text FROM parcel_fact f JOIN extraction e ON e.id=f.extraction_id "
                               "WHERE e.content_hash=%s AND fact_type='owner'", (ch,)).fetchone()[0]
            assert tok.startswith("OWNER-") and "PRIVATE" not in tok
            with conn.transaction():
                conn.execute("SET LOCAL ROLE agent_ro")
                assert conn.execute("SELECT count(*) FROM v_owner_pseudo").fetchone()[0] >= 1
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE agent_ro")
                    conn.execute("SELECT canonical_name FROM owner")
        finally:
            conn.rollback()


def test_budget_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(batch, "STATE_PATH", tmp_path / "state.json")
    r = batch.Runner(budget_usd=0.002, max_fail=5)
    assert r.may_start()
    r.record({"doc_id": 1, "page_no": 1, "type": "LDR"},
             {"status": "accepted", "reads": [{"ok": True, "cost_usd": 0.0015, "from_cache": False}]}, None)
    assert not r.may_start() and r.stop_reason.startswith("budget")


def test_aws_guard_and_failure_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(batch, "STATE_PATH", tmp_path / "state.json")
    r = batch.Runner(budget_usd=7, max_fail=2)
    bad = {"status": "needs_human", "review_reasons": ["vlm_failed"],
           "reads": [{"ok": False, "error": "no eligible model: bedrock: quota:aws_spend $12.10 >= 80% of $15 cap"}]}
    r.record({"doc_id": 1, "page_no": 1, "type": "LDR"}, bad, None)
    assert "AWS spend guard" in r.stop_reason and not r.may_start()
    r2 = batch.Runner(budget_usd=7, max_fail=2)
    fail = {"status": "needs_human", "review_reasons": ["vlm_failed"], "reads": [{"ok": False, "error": "ExpiredToken"}]}
    r2.record({"doc_id": 1, "page_no": 1, "type": "LDR"}, fail, None)
    r2.record({"doc_id": 1, "page_no": 2, "type": "LDR"}, fail, None)
    assert "consecutive" in r2.stop_reason


def test_selection_prefers_segment_type_and_skips_unknown():
    class Conn:
        def execute(self, sql):
            class R:
                def fetchall(self_inner):
                    return [(1, "s1", "a.pdf", "FORM_F", None, 1, 11, False, "AWARD_7_2"),
                            (1, "s1", "a.pdf", "FORM_F", None, 2, 12, False, None),
                            (2, "s2", "b.pdf", "OTHER", None, 1, 21, False, None)]
            return R()
    pages = batch.select_pages(Conn(), {"AWARD_7_2", "FORM_F"})
    assert [(p["page_no"], p["type"], p["type_source"]) for p in pages] == [(1, "AWARD_7_2", "segment"),
                                                                            (2, "FORM_F", "document")]


def test_batch_stops_when_served_by_fallback_model(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(batch, "STATE_PATH", tmp_path / "state.json")
    r = batch.Runner(budget_usd=7, max_fail=5)
    ok_bedrock = {"status": "accepted", "reads": [{"ok": True, "model_id": "bedrock-ministral-8b", "from_cache": False}]}
    r.record({"doc_id": 1, "page_no": 1, "type": "LDR"}, ok_bedrock, None)
    assert r.may_start()
    monkeypatch.setattr(batch, "OUT_DIR", tmp_path / "pages")
    # D-048: a single fallback page does not stop the batch; its primary-from-fallback result is marked for redo
    for n in range(2, 6):
        (tmp_path / "pages" / "1").mkdir(parents=True, exist_ok=True)
        groq = {"status": "accepted", "reads": [{"ok": True, "route": "primary", "model_id": "groq-qwen-vl",
                                                 "from_cache": False}]}
        r.record({"doc_id": 1, "page_no": n, "type": "LDR"}, groq, None)
        assert groq["status"] == "failed"
        assert r.may_start(), f"stopped too early after {n - 1} fallback page(s)"
    groq = {"status": "accepted", "reads": [{"ok": True, "route": "primary", "model_id": "groq-qwen-vl",
                                             "from_cache": False}]}
    r.record({"doc_id": 1, "page_no": 6, "type": "LDR"}, groq, None)
    assert "5 consecutive pages served by a fallback model" in r.stop_reason and not r.may_start()
