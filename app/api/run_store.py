"""Run persistence (db/migrations/0011_workspace_run.sql: run, run_event) + a per-process pub/sub
fan-out for live SSE subscribers.

Every `AgentEvent` (app/agent/CONTRACT.md §2: `{type, run_id, seq, ts, node, data}`) is written to
`run_event` in the order it was yielded by `run_request`/`resume_run` — `seq`/`ts`/`node` are
agent-assigned (or, for the stub runner, assigned by app/api/runner.py, which also chains each event
into `agent_ledger` itself — see app/api/ledger_stub.py). This module does **not** compute ledger
hashes; it only relays and persists, so it needs no change when the real graph starts emitting
events (contract §5: "the backend stores result.data.workspace ...").

`run.state` is a best-effort reconstruction of `RunState.public_dict()` (contract §3), rebuilt
incrementally from each event's `data`, until `app.agent.get_run_state(run_id)` is wired in
(app/api/runner.py) as the single source of truth for `GET /runs/{id}`.

The in-memory pub/sub (asyncio.Queue per run_id) assumes a single API process (the local/demo
deployment via `make api`/`uvicorn`). Multi-worker deployments would need a shared broker (e.g.
Postgres LISTEN/NOTIFY) — noted as an open question in the phase report.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg

STATUS_FOR_EVENT = {"clarify": "clarify", "result": "done", "error": "failed"}


class RunNotFound(KeyError):
    pass


def _empty_state(run_id: str, request: str, attachments: list[dict], workspace_id: str | None,
                 domain: str | None, lang: str | None) -> dict[str, Any]:
    return {
        "run_id": run_id, "workspace_id": workspace_id, "contract_version": "1", "request": request,
        "lang": lang or "en", "domain": domain or "land_acquisition", "attachments": attachments,
        "slots": {}, "missing_info": [], "assumptions": [], "plan": None, "steps": {}, "claims": [],
        "critic": [], "verdicts": {}, "reroutes": 0, "workspace": None, "narrative": {},
        "route_trace": [], "telemetry": {}, "ledger_head": None, "errors": [], "degraded": False,
    }


class RunStore:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    # -- persistence (sync; call via asyncio.to_thread from the async driver) -----------------
    def create(self, conn: psycopg.Connection, run_id: str, request: str, attachments: list[dict],
               workspace_id: str | None, *, domain: str | None = None, lang: str | None = None) -> None:
        req_payload = {"request": request, "attachments": attachments, "domain": domain, "lang": lang}
        state = _empty_state(run_id, request, attachments, workspace_id, domain, lang)
        conn.execute(
            "INSERT INTO run (id, workspace_id, request, status, state) VALUES (%s, %s, %s, 'queued', %s)",
            (run_id, workspace_id, json.dumps(req_payload), json.dumps(state, default=str)),
        )
        conn.commit()

    def get(self, conn: psycopg.Connection, run_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM run WHERE id = %s", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return row

    def append_event(self, conn: psycopg.Connection, run_id: str, envelope: dict[str, Any]) -> None:
        """Persist one AgentEvent envelope (seq is agent-assigned) and update `run`."""
        conn.execute(
            "INSERT INTO run_event (run_id, seq, event, payload) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (run_id, seq) DO NOTHING",
            (run_id, envelope["seq"], envelope["type"], json.dumps(envelope, default=str)),
        )
        self._apply_run_update(conn, run_id, envelope)
        conn.commit()

    def _apply_run_update(self, conn: psycopg.Connection, run_id: str, envelope: dict[str, Any]) -> None:
        event, node, data = envelope["type"], envelope["node"], envelope.get("data", {})
        run = conn.execute("SELECT state FROM run WHERE id = %s", (run_id,)).fetchone()
        state = run["state"] if run else {}

        if event == "plan":
            state["plan"] = data.get("plan")
        elif event == "step_start":
            steps = state.setdefault("steps", {})
            steps[data.get("step_id")] = {"id": data.get("step_id"), "tool": data.get("tool"),
                                          "llm_task": data.get("llm_task"), "status": "running",
                                          "args": data.get("args_preview")}
        elif event == "step_end":
            steps = state.setdefault("steps", {})
            s = steps.setdefault(data.get("step_id"), {"id": data.get("step_id")})
            s.update(status=data.get("status", "ok"), output_ref=data.get("output_ref"),
                    summary=data.get("summary"), n_rows=data.get("n_rows"),
                    latency_ms=data.get("latency_ms"), model_id=data.get("model_id"),
                    shadow_cost_usd=data.get("shadow_cost_usd"), error=data.get("error"))
        elif event == "verify":
            if data.get("claims"):
                state["claims"] = data["claims"]
        elif event == "critic":
            state.setdefault("critic", []).append(data)
        elif event == "judge":
            verdicts = state.setdefault("verdicts", {})
            for v in data.get("verdicts", []):
                verdicts[v["claim_id"]] = v
        elif event == "clarify":
            state["missing_info"] = [{"slot": m, "blocking": True, "question": data.get("question", "")}
                                     for m in data.get("missing", [])] or state.get("missing_info", [])
        elif event == "result":
            state["workspace"] = data.get("workspace")
            state["degraded"] = data.get("degraded", False)
        elif event == "error":
            state.setdefault("errors", []).append({"node": data.get("node", node),
                                                   "message": data.get("message")})
        elif event == "ledger":
            state["ledger_head"] = data.get("hash")

        # `ledger` is a trailing, informational companion emitted after every other event
        # (app/api/runner.py) — it must never regress a status a preceding event already set
        # (e.g. downgrade "done"/"failed" back to "running").
        status = None if event == "ledger" else STATUS_FOR_EVENT.get(event, "running")
        error = data.get("message") if event == "error" else None
        result = json.dumps(data, default=str) if event == "result" else None
        conn.execute(
            "UPDATE run SET status = COALESCE(%s, status), state = %s, error = COALESCE(%s, error), "
            "result = COALESCE(%s::jsonb, result), updated_at = now() WHERE id = %s",
            (status, json.dumps(state, default=str), error, result, run_id),
        )

    def events_since(self, conn: psycopg.Connection, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT seq, event, payload FROM run_event WHERE run_id = %s AND seq > %s ORDER BY seq",
            (run_id, after_seq),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- pub/sub (live SSE only; replay always comes from Postgres) ---------------------------
    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id, [])
        if q in subs:
            subs.remove(q)

    async def publish(self, run_id: str, envelope: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(run_id, [])):
            await q.put(envelope)


_store: RunStore | None = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store


def reset_run_store() -> None:
    global _store
    _store = None
