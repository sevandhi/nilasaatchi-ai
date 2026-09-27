"""GET /findings, /findings/summary, /findings/{id}/evidence-pack, /idle-land (db-marked)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_list_findings(client):
    r = client.get("/findings", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1388  # docs/metrics.md "Findings engine" count
    assert len(body["items"]) == 5


def test_list_findings_filter_by_severity(client):
    r = client.get("/findings", params={"severity": "high", "limit": 20})
    assert r.status_code == 200
    for f in r.json()["items"]:
        assert f["severity"] == "high"


def test_findings_summary(client):
    r = client.get("/findings/summary")
    assert r.status_code == 200
    assert r.json()["items"]


def test_idle_land(client):
    r = client.get("/idle-land")
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


def test_evidence_pack(client, pg_conn):
    row = pg_conn.execute("SELECT id FROM finding WHERE status = 'open' LIMIT 1").fetchone()
    if row is None:
        pytest.skip("no findings loaded yet")
    r = client.get(f"/findings/{row['id']}/evidence-pack")
    assert r.status_code == 200
    pack = r.json()["pack"]
    assert pack["finding_id"] == row["id"]
    assert "paper" in pack and "planet" in pack


def test_evidence_pack_404(client):
    assert client.get("/findings/999999999/evidence-pack").status_code == 404
