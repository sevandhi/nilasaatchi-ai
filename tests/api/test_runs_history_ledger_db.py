"""GET /runs (history), GET /runs/{id}/trace, POST /ledger/verify — against the stub runner
(db-marked, same lifecycle as tests/api/test_runs_db.py)."""
from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.db


def _wait_for_status(client, run_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = client.get(f"/runs/{run_id}").json()
        if state["status"] in statuses:
            return state
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach {statuses} in {timeout}s (last: {state})")


def test_runs_history_lists_created_run(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})

    hist = client.get("/runs", params={"limit": 200})
    assert hist.status_code == 200
    body = hist.json()
    assert body["total"] >= 1
    assert any(item["id"] == run_id for item in body["items"])


def test_runs_history_filters_by_status(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})
    r2 = client.get("/runs", params={"status": "done", "limit": 200})
    assert r2.status_code == 200
    assert all(item["status"] == "done" for item in r2.json()["items"])


def test_run_trace_has_critic_judge_and_ledger_head(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})

    tr = client.get(f"/runs/{run_id}/trace")
    assert tr.status_code == 200
    body = tr.json()
    assert body["run_id"] == run_id
    assert body["critic"] and body["critic"][0]["claim_id"] == "c1"
    assert body["judge"] and body["judge"][0]["verdict"] == "ACCEPT"
    assert body["ledger_head"]


def test_run_trace_captures_fallback(client):
    r = client.post("/runs", json={"request": "chaos test request"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})
    tr = client.get(f"/runs/{run_id}/trace").json()
    assert tr["fallbacks"]
    assert tr["fallbacks"][0]["reason"] == "429 (chaos)"


def test_run_trace_404_for_unknown_run(client):
    assert client.get("/runs/does-not-exist/trace").status_code == 404


def test_ledger_verify_ok(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})

    lv = client.post("/ledger/verify", params={"run_id": run_id})
    assert lv.status_code == 200
    body = lv.json()
    assert body["ok"] is True
    assert body["first_bad_seq"] is None
    assert body["n_entries"] > 0
    assert body["head_hash"]


def test_ledger_verify_404_for_unknown_run(client):
    assert client.post("/ledger/verify", params={"run_id": "does-not-exist"}).status_code == 404


def test_chaos_toggle_recorded_as_first_event_and_reset_after(client):
    from app.router import chaos as router_chaos

    r = client.post("/runs", json={"request": "chaos test request",
                                   "chaos": "gemini:down,groq:slow"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})

    ev = client.get(f"/runs/{run_id}/events").text
    lines = ev.splitlines()
    assert lines[0] == "id: 0"
    assert lines[1] == "event: status"
    first_data = json.loads(lines[2][len("data: "):])
    assert first_data["data"]["chaos"] == "gemini:down,groq:slow"
    assert first_data["data"]["message"] == "chaos: gemini down, groq slow"
    # scoped to this run only: the process-wide override is cleared once the run finishes
    assert router_chaos._override is None


def test_ledger_verify_detects_tampering(client, pg_conn):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})

    pg_conn.execute(
        "UPDATE agent_ledger SET payload = payload || '{\"tampered\": true}'::jsonb "
        "WHERE run_id = %s AND seq = 1", (run_id,),
    )
    pg_conn.commit()

    lv = client.post("/ledger/verify", params={"run_id": run_id})
    assert lv.status_code == 200
    body = lv.json()
    assert body["ok"] is False
    assert body["first_bad_seq"] == 1
