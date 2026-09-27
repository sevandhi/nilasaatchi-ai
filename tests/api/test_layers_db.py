"""GET /layers/{name}.geojson against the real loaded GIS layers (db-marked)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_parcel_layer_geojson(client):
    r = client.get("/layers/parcel.geojson", params={"limit": 10})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/geo+json")
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 10
    assert fc["features"][0]["geometry"]["type"] in ("MultiPolygon", "Polygon")
    assert "parcel_uid" in fc["features"][0]["properties"]


def test_fmb_qa_layer_joins_parcel_geometry(client):
    r = client.get("/layers/fmb_qa.geojson", params={"limit": 5})
    assert r.status_code == 200
    fc = r.json()
    if fc["features"]:
        assert fc["features"][0]["geometry"] is not None
        assert "issue" in fc["features"][0]["properties"]


def test_unknown_layer_404(client):
    r = client.get("/layers/not_a_real_layer.geojson")
    assert r.status_code == 404


def test_zoom_simplifies_geometry(client):
    hi = client.get("/layers/parcel.geojson", params={"limit": 5}).json()
    lo = client.get("/layers/parcel.geojson", params={"limit": 5, "zoom": 8}).json()
    assert lo["properties"]["simplify_tolerance_deg"] > 0
    assert hi["properties"]["simplify_tolerance_deg"] == 0


def test_bbox_filters_features(client):
    # A tiny envelope far from Tamil Nadu should return no parcels.
    r = client.get("/layers/parcel.geojson", params={"bbox": "0,0,0.01,0.01"})
    assert r.status_code == 200
    assert r.json()["features"] == []


def test_bad_bbox_422(client):
    r = client.get("/layers/parcel.geojson", params={"bbox": "not,a,bbox"})
    assert r.status_code == 422


def test_parcel_layer_enriched_with_stage_and_findings(client):
    r = client.get("/layers/parcel.geojson", params={"limit": 5})
    assert r.status_code == 200
    props = r.json()["features"][0]["properties"]
    for key in ("current_stage", "evidence_level", "stalled_flag", "n_findings"):
        assert key in props


def test_parcel_layer_season_param_adds_season_state(client):
    r = client.get("/layers/parcel.geojson", params={"limit": 20, "season": "2024-rabi"})
    assert r.status_code == 200
    props_list = [f["properties"] for f in r.json()["features"]]
    assert all("season_state" in p for p in props_list)


def test_controls_layer(client):
    r = client.get("/layers/controls.geojson", params={"limit": 5})
    assert r.status_code == 200
    fc = r.json()
    assert fc["properties"]["layer"] == "controls"
    if fc["features"]:
        assert "cell_id" in fc["features"][0]["properties"]


def test_fmb_overlaps_layer(client):
    r = client.get("/layers/fmb_overlaps.geojson", params={"limit": 5})
    assert r.status_code == 200
    fc = r.json()
    if fc["features"]:
        assert "overlap_ha" in fc["features"][0]["properties"]


def test_outside_survey_layer(client):
    r = client.get("/layers/outside_survey.geojson", params={"limit": 5})
    assert r.status_code == 200
    fc = r.json()
    if fc["features"]:
        assert "outside_ha" in fc["features"][0]["properties"]
