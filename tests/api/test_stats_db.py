"""GET /stats/overview against the real loaded KG (db-marked): every KPI carries a `source`."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_overview_kpis_have_sources(client):
    r = client.get("/stats/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"], "expected at least one KPI"
    ids = {k["id"] for k in body["kpis"]}
    for expected in ("documents", "pages", "extraction_rows", "parcels", "findings", "review_queue"):
        assert expected in ids
    for k in body["kpis"]:
        assert k["source"], f"{k['id']} has no source"


def test_overview_document_count_matches_table(client, pg_conn):
    n = pg_conn.execute("SELECT count(*) AS n FROM document").fetchone()["n"]
    body = client.get("/stats/overview").json()
    kpi = next(k for k in body["kpis"] if k["id"] == "documents")
    assert kpi["value"] == n


def test_overview_has_phase_status(client):
    body = client.get("/stats/overview").json()
    assert body["phase_status"] is not None
    assert body["phase_status"]["source"] == "docs/progress.md"


def test_overview_has_both_aws_spend_kpis(client):
    body = client.get("/stats/overview").json()
    ids = {k["id"]: k for k in body["kpis"]}
    assert "aws_spend_usd" in ids
    assert "aws_spend_router_estimate_usd" in ids
    assert ids["aws_spend_router_estimate_usd"]["value"] >= 0
    assert "router_log.sqlite" in ids["aws_spend_router_estimate_usd"]["source"]
