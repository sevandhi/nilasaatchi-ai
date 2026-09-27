"""GET /review-queue, POST /review/{id} against the real review_queue (db-marked)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_list_open_review_queue(client):
    r = client.get("/review-queue")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1  # docs/progress.md: 9 open items loaded in P2
    assert all(i["status"] == "open" for i in body["items"])


def test_decide_review_item_writes_back_to_extraction(client, pg_conn):
    row = pg_conn.execute(
        "SELECT id, extraction_id FROM review_queue WHERE status = 'open' AND extraction_id IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no open review_queue item with a linked extraction_id")
    r = client.post(f"/review/{row['id']}", json={"decision": "approved", "decided_by": "qa-test"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "decided"
    assert body["review_status"] == "approved"

    ext = pg_conn.execute("SELECT review_status FROM extraction WHERE id = %s", (row["extraction_id"],)).fetchone()
    assert ext["review_status"] == "approved"

    # cleanup: put the fixture data back the way we found it, so repeated test runs stay stable
    pg_conn.execute("UPDATE review_queue SET status = 'open', decided_value = NULL, decided_by = NULL, "
                    "decided_at = NULL WHERE id = %s", (row["id"],))
    pg_conn.execute("UPDATE extraction SET review_status = 'auto_unverified' WHERE id = %s",
                    (row["extraction_id"],))
    pg_conn.commit()


def test_decide_unknown_review_404(client):
    assert client.post("/review/999999999", json={"decision": "approved"}).status_code == 404
