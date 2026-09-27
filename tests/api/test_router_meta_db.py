"""GET /router/models, /router/usage, /router/chains (db-marked: needs no DB, but grouped with the
other db-marked API tests since they read from data/ files that are only present after a full run)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_router_models(client):
    r = client.get("/router/models")
    assert r.status_code == 200
    body = r.json()
    assert body["models"]
    assert "trains_on_free_tier" in body["models"][0]


def test_router_chains(client):
    r = client.get("/router/chains")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    names = {t["task"] for t in tasks}
    assert {"plan", "critic", "judge"} <= names


def test_router_usage_pii_audit_present(client):
    r = client.get("/router/usage")
    assert r.status_code == 200
    body = r.json()
    assert "pii_calls_ok" in body["pii_audit"]
    assert "pii_to_training_tier" in body["pii_audit"]
