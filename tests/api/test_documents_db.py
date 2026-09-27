"""GET /documents, /documents/{id}/pages/{n}.webp, /evidence/{extraction_id} against the real
loaded corpus (db-marked)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_list_documents(client):
    r = client.get("/documents", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2362  # T1.1 catalog count (docs/progress.md)
    assert len(body["items"]) == 5


def test_list_documents_filter_by_village(client):
    r = client.get("/documents", params={"village": "Allikulam", "limit": 5})
    assert r.status_code == 200
    for d in r.json()["items"]:
        assert d["village"] == "Allikulam"


def test_page_preview_serves_webp(client, pg_conn):
    row = pg_conn.execute(
        "SELECT document_id, page_no FROM page WHERE preview_path IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no page previews rendered yet")
    r = client.get(f"/documents/{row['document_id']}/pages/{row['page_no']}.webp")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"


def test_page_preview_404_for_unknown_page(client):
    r = client.get("/documents/1/pages/999999.webp")
    assert r.status_code == 404


def test_evidence_masks_owner_names_by_default(client, pg_conn):
    row = pg_conn.execute(
        "SELECT extraction_id FROM extraction_owner LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no owner-bearing extractions loaded yet")
    r = client.get(f"/evidence/{row['extraction_id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["masked"] is True
    for o in body["owners"]:
        assert o["name"] is None
        assert o["token"].startswith("OWNER-")


def test_evidence_unmask_refused_unless_server_allows_it(client, pg_conn, monkeypatch):
    row = pg_conn.execute("SELECT extraction_id FROM extraction_owner LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no owner-bearing extractions loaded yet")
    from app.api.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("ALLOW_DEMO_UNMASK", "false")
    r = client.get(f"/evidence/{row['extraction_id']}", params={"demo_mask": "false"})
    assert r.json()["masked"] is True  # server config wins even if the caller asks to unmask
    get_settings.cache_clear()


def test_evidence_never_serves_bank_account_fields(client, pg_conn):
    row = pg_conn.execute(
        "SELECT id FROM extraction WHERE row_json::text ILIKE '%account%' LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no extraction rows mention 'account'")
    r = client.get(f"/evidence/{row['id']}")
    assert r.status_code == 200
    import json

    text = json.dumps(r.json()["row"]).lower()
    assert "account_no" not in text and "bank_account" not in text and "ifsc" not in text


def test_evidence_404_for_unknown_id(client):
    assert client.get("/evidence/999999999").status_code == 404


def test_list_documents_filter_by_stage(client, pg_conn):
    row = pg_conn.execute("SELECT stage FROM document WHERE stage IS NOT NULL LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no document.stage values loaded yet")
    r = client.get("/documents", params={"stage": row["stage"], "limit": 5})
    assert r.status_code == 200
    for d in r.json()["items"]:
        assert d["stage"] == row["stage"]


def test_list_documents_full_text_query(client):
    r = client.get("/documents", params={"q": "award", "limit": 5})
    assert r.status_code == 200
    assert r.json()["total"] >= 0


def test_documents_folder_type_mismatch_flag_present(client):
    r = client.get("/documents", params={"limit": 5})
    assert r.status_code == 200
    for d in r.json()["items"]:
        assert isinstance(d["folder_type_mismatch"], bool)


def test_classifier_summary(client):
    r = client.get("/classifier/summary")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["live_distribution"], list)
    assert isinstance(body["folder_vs_classified_mismatch"], int)
