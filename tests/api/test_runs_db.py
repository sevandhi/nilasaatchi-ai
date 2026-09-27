"""End-to-end run lifecycle against the stub runner (app/api/runner.py), db-marked because runs
are persisted in Postgres (run, run_event, agent_ledger). Covers every SSE event type from
app/agent/CONTRACT.md §2 except `status` (informational-only, not emitted by the stub)."""
from __future__ import annotations

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


def test_run_completes_and_replays_every_event_type(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    state = _wait_for_status(client, run_id, {"done", "failed"})
    assert state["status"] == "done"
    assert state["contract_version"] == "1"
    assert state["plan"]["steps"]
    assert len(state["steps"]) == 2
    assert {s["status"] for s in state["steps"].values()} == {"ok"}
    assert state["claims"]
    assert state["verdicts"]["c1"]["verdict"] == "ACCEPT"
    assert state["workspace"]["tables"]
    assert state["ledger_head"]  # the stub ledger produced a real chained hash

    events = client.get(f"/runs/{run_id}/events")
    assert events.status_code == 200
    body = events.text
    for kind in ("plan", "step_start", "step_end", "verify", "critic", "judge", "ledger", "result"):
        assert f"event: {kind}" in body, body


def test_chaos_triggers_a_fallback_event(client):
    r = client.post("/runs", json={"request": "chaos test request"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})
    body = client.get(f"/runs/{run_id}/events").text
    assert "event: fallback" in body


def test_clarify_then_resume(client):
    r = client.post("/runs", json={"request": "please clarify this one"})
    run_id = r.json()["run_id"]
    state = _wait_for_status(client, run_id, {"clarify", "done", "failed"})
    assert state["status"] == "clarify"
    assert state["missing_info"][0]["slot"] == "village"

    r2 = client.post(f"/runs/{run_id}/clarify", json={"answer": "Allikulam"})
    assert r2.status_code == 200
    state = _wait_for_status(client, run_id, {"done", "failed"})
    assert state["status"] == "done"
    # resume continues the same ledger chain rather than restarting it
    assert client.get(f"/runs/{run_id}/events").text.count("event: plan") == 1


def test_clarify_rejected_when_run_not_awaiting_it(client):
    r = client.post("/runs", json={"request": "list allikulam parcels"})
    run_id = r.json()["run_id"]
    _wait_for_status(client, run_id, {"done", "failed"})
    r2 = client.post(f"/runs/{run_id}/clarify", json={"answer": "x"})
    assert r2.status_code == 409


def test_unsupported_request_yields_error_event(client):
    r = client.post("/runs", json={"request": "use unseen tool xyz"})
    run_id = r.json()["run_id"]
    state = _wait_for_status(client, run_id, {"done", "failed"})
    assert state["status"] == "failed"
    assert "no tool matches" in state["errors"][-1]["message"]


def test_unknown_run_404(client):
    assert client.get("/runs/does-not-exist").status_code == 404
    assert client.get("/runs/does-not-exist/events").status_code == 404
