"""GET /parcels/{uid}, /timeline, /extractions, /satellite, /findings (db-marked). Uses a real
`parcel_uid` with a '/' inside its kide (e.g. 'Allikulam|10/1') to exercise the `:path` converter
ordering (D-020)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


@pytest.fixture()
def parcel_uid(pg_conn) -> str:
    row = pg_conn.execute(
        "SELECT parcel_uid FROM parcel WHERE parcel_uid LIKE '%/%' LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("no parcel_uid with '/' in kide loaded yet")
    return row["parcel_uid"]


def test_get_parcel(client, parcel_uid):
    r = client.get(f"/parcels/{parcel_uid}")
    assert r.status_code == 200
    body = r.json()
    assert body["parcel_uid"] == parcel_uid
    assert body["source"]


def test_get_parcel_404(client):
    assert client.get("/parcels/Nowhere|999999").status_code == 404


def test_parcel_timeline(client, parcel_uid):
    r = client.get(f"/parcels/{parcel_uid}/timeline")
    assert r.status_code == 200
    body = r.json()
    assert body["parcel_uid"] == parcel_uid
    assert isinstance(body["events"], list)
    assert isinstance(body["series"], list)
    assert isinstance(body["season_states"], list)


def test_parcel_extractions_masks_owners(client, parcel_uid):
    r = client.get(f"/parcels/{parcel_uid}/extractions")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    import json

    text = json.dumps(body).lower()
    assert "account_no" not in text and "bank_account" not in text


def test_parcel_satellite(client, parcel_uid):
    r = client.get(f"/parcels/{parcel_uid}/satellite")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["did"], list)
    assert isinstance(body["chips"], list)


def test_parcel_satellite_404(client):
    assert client.get("/parcels/Nowhere|999999/satellite").status_code == 404


def test_parcel_findings(client, parcel_uid):
    r = client.get(f"/parcels/{parcel_uid}/findings")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
