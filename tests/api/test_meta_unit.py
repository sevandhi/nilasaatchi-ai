"""Pure unit tests: no DB required (these routes never call app.api.db.get_conn)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_openapi_has_expected_paths():
    spec = client.get("/openapi.json").json()
    expected = {
        "/runs", "/runs/{run_id}/events", "/runs/{run_id}", "/runs/{run_id}/clarify",
        "/workspaces", "/workspaces/{workspace_id}", "/workspaces/{workspace_id}/versions/{version}",
        "/workspaces/{workspace_id}/export", "/documents", "/documents/{document_id}/pages/{page_no}.webp",
        "/evidence/{extraction_id}", "/layers/{name}.geojson", "/chips/{parcel_uid}/{date}.png",
        "/review-queue", "/review/{review_id}", "/examples",
    }
    assert expected <= set(spec["paths"])


def test_examples_reads_eval_queries_yaml():
    r = client.get("/examples")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    ids = {i["id"] for i in items}
    assert "paper-vs-planet-233" in ids  # eval/queries.yaml (plan.md §4.2 demo request #1)
    # No hard-coded brief query text in app/ (CLAUDE.md hard rule): every item's text came from the
    # YAML file, which we can independently re-parse and compare.
    import yaml

    from app.api.config import REPO_ROOT

    doc = yaml.safe_load((REPO_ROOT / "eval" / "queries.yaml").read_text())
    by_id = {q["id"]: q["text"] for q in doc["queries"]}
    for item in items:
        assert item["text"] == by_id[item["id"]]


def test_cors_configured_for_vite_dev_server():
    r = client.options("/examples", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
