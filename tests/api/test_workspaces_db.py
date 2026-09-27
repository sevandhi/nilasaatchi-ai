"""Workspace CRUD/versioning/export against the real PostGIS instance (db-marked). `spec` follows
the agent's WorkspaceSpec (app/agent/state.py, contract §4) — the shape a saved workspace actually
stores (contract §5: "the backend stores result.data.workspace")."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def _spec(title="Test workspace", **overrides):
    base = {
        "spec_version": "1", "title": title, "domain": "land_acquisition", "run_id": "run-fixture",
        "created_at": "2026-09-27T00:00:00Z", "ledger_head": None, "lang": "en",
        "kpis": [],
        "map": {"bbox": None, "layers": [
            {"id": "l1", "title": "Parcels", "kind": "reference", "ref_layer": "parcel"},
        ], "time_slider": None},
        "tables": [{"id": "t1", "title": "Parcels", "columns": [{"key": "parcel_uid", "label": "Parcel",
                   "type": "string"}], "rows": [{"parcel_uid": "Allikulam|1"}], "claim_ids": []}],
        "charts": [], "timeline": None,
        "narrative": {"en": "Test narrative", "claim_refs": []},
        "evidence": [], "verification": {}, "sql": [], "route_trace": [], "caveats": [],
    }
    base.update(overrides)
    return {"name": title, "spec": base}


def test_create_get_update_versioning(client):
    r = client.post("/workspaces", json=_spec())
    assert r.status_code == 200, r.text
    ws = r.json()
    assert ws["version"] == 1
    wid = ws["id"]

    got = client.get(f"/workspaces/{wid}").json()
    assert got["spec"]["map"]["layers"][0]["ref_layer"] == "parcel"

    updated_spec = dict(got["spec"])
    updated_spec["caveats"] = ["updated"]
    r2 = client.put(f"/workspaces/{wid}", json={"spec": updated_spec, "name": "Renamed"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 2
    assert r2.json()["name"] == "Renamed"

    v1 = client.get(f"/workspaces/{wid}/versions/1").json()
    assert v1["version"] == 1
    assert v1["spec"]["caveats"] == []  # v1 kept its original (empty) caveats

    listed = client.get("/workspaces").json()
    assert any(w["id"] == wid for w in listed)


def test_get_missing_workspace_404(client):
    assert client.get("/workspaces/does-not-exist").status_code == 404


def test_export_geojson_reproducible_from_spec_alone(client):
    r = client.post("/workspaces", json=_spec("Export test")).json()
    wid = r["id"]
    e1 = client.post(f"/workspaces/{wid}/export", params={"fmt": "geojson"})
    e2 = client.post(f"/workspaces/{wid}/export", params={"fmt": "geojson"})
    assert e1.status_code == 200, e1.text
    assert e1.headers["content-type"].startswith("application/geo+json")
    assert e1.content == e2.content  # same spec -> byte-identical export
    fc = e1.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0  # the real `parcel` table (1,242 rows), resolved via ref_layer
    assert fc["features"][0]["properties"]["_layer_id"] == "l1"


def test_export_csv_and_json(client):
    r = client.post("/workspaces", json=_spec("Export csv/json")).json()
    wid = r["id"]
    csv_r = client.post(f"/workspaces/{wid}/export", params={"fmt": "csv"})
    assert csv_r.status_code == 200
    assert csv_r.headers["content-type"].startswith("text/csv")
    assert "parcel_uid" in csv_r.text.splitlines()[0]  # from spec.tables[0], not the map layer

    json_r = client.post(f"/workspaces/{wid}/export", params={"fmt": "json"})
    assert json_r.status_code == 200
    body = json_r.json()
    assert body["id"] == wid
    assert body["spec"]["title"] == "Export csv/json"


def test_export_pdf_or_skip_with_reason(client):
    r = client.post("/workspaces", json=_spec("PDF test")).json()
    wid = r["id"]
    pdf_r = client.post(f"/workspaces/{wid}/export", params={"fmt": "pdf"})
    if pdf_r.status_code == 501:
        pytest.skip(f"Playwright browsers not installed: {pdf_r.json()['detail']}")
    assert pdf_r.status_code == 200
    assert pdf_r.content[:4] == b"%PDF"
