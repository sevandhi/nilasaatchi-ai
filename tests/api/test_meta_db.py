"""GET /eval/summary, /decisions, /progress — parsed from docs/*.md (db-marked: grouped with the
other API tests that need the `client` fixture; these three don't touch Postgres but do read the
repo's docs/ files, which must exist)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_eval_summary(client):
    r = client.get("/eval/summary")
    assert r.status_code == 200
    assert r.json()["rows"]


def test_decisions_parsed(client):
    r = client.get("/decisions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    assert all(i["id"].startswith("D-") for i in items)
    assert any(i["id"] == "D-001" for i in items)


def test_progress_parsed(client):
    r = client.get("/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_pct"]
    assert body["phases"]
    assert body["raw_markdown"]
