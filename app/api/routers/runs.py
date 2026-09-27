"""POST /runs, GET /runs/{id}/events (SSE), GET /runs/{id}, POST /runs/{id}/clarify.

Wire format follows app/agent/CONTRACT.md §2 (`AgentEvent`: `{type, run_id, seq, ts, node, data}`).
`app.api.runner.get_run_request()`/`get_resume_run()` return the real agent graph's functions once
`app.agent` exports them; until then every run is driven by the deterministic stub in
app/api/runner.py.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.api.db import get_conn
from app.api.run_store import RunNotFound, get_run_store
from app.api.runner import get_resume_run, get_run_request, now_iso
from app.api.schemas import (
    ClarifyRequest,
    LedgerVerifyResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunListResponse,
    RunPublicState,
    RunSummary,
    RunTraceResponse,
)

router = APIRouter(tags=["runs"])


async def _to_thread(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


async def _persist_and_publish(store, run_id: str, envelope: dict) -> None:
    def _persist():
        with get_conn() as conn:
            store.append_event(conn, run_id, envelope)

    await _to_thread(_persist)
    await store.publish(run_id, envelope)


def _humanize_chaos(spec: str) -> str:
    """"gemini:down,groq:slow" -> "gemini down, groq slow" (the UI's chaos toggle label)."""
    return ", ".join(part.strip().replace(":", " ") for part in spec.split(",") if part.strip())


async def _drive_new(run_id: str, request: str, attachments: list[dict], workspace_id: str | None,
                     domain: str | None, lang: str | None, chaos: str | None = None) -> None:
    store = get_run_store()
    if chaos:
        # seq=0: strictly before the agent's own seq-1 first event, so it always sorts first on
        # replay; a `status` event is explicitly forward-compatible / informational-only per
        # app/agent/CONTRACT.md §2 ("Additional informational type `status` ... may appear").
        await _persist_and_publish(store, run_id, {
            "type": "status", "run_id": run_id, "seq": 0, "ts": now_iso(), "node": "api",
            "data": {"message": f"chaos: {_humanize_chaos(chaos)}", "chaos": chaos}})
    fn, _is_stub = get_run_request()
    from app.router.chaos import set_chaos

    if chaos:
        set_chaos(chaos)
    try:
        async for envelope in fn(request, attachments, workspace_id, run_id=run_id, domain=domain,
                                 lang=lang):
            await _persist_and_publish(store, run_id, envelope)
    except Exception as e:  # noqa: BLE001  any run-time failure becomes a terminal `error` event
        await _persist_and_publish(store, run_id, {
            "type": "error", "run_id": run_id, "seq": 999999999, "ts": "", "node": "api",
            "data": {"message": str(e), "node": "api", "recoverable": False}})
    finally:
        if chaos:
            set_chaos(None)  # scoped to this run only; subsequent runs fall back to ROUTER_CHAOS env


async def _drive_resume(run_id: str, answer: str) -> None:
    store = get_run_store()
    fn, _is_stub = get_resume_run()
    try:
        async for envelope in fn(run_id, answer):
            await _persist_and_publish(store, run_id, envelope)
    except Exception as e:  # noqa: BLE001
        await _persist_and_publish(store, run_id, {
            "type": "error", "run_id": run_id, "seq": 999999999, "ts": "", "node": "api",
            "data": {"message": str(e), "node": "api", "recoverable": False}})


@router.post("/runs", response_model=RunCreateResponse)
async def create_run(body: RunCreateRequest) -> RunCreateResponse:
    run_id = uuid.uuid4().hex
    store = get_run_store()
    attachments = [a.model_dump(mode="json") for a in body.attachments]

    def _create():
        with get_conn() as conn:
            store.create(conn, run_id, body.request, attachments, body.workspace_id,
                        domain=body.domain, lang=body.lang)

    await _to_thread(_create)
    asyncio.create_task(_drive_new(run_id, body.request, attachments, body.workspace_id,
                                   body.domain, body.lang, chaos=body.chaos))
    return RunCreateResponse(run_id=run_id)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    store = get_run_store()

    def _replay():
        with get_conn() as conn:
            store.get(conn, run_id)  # raises RunNotFound
            return store.events_since(conn, run_id, after_seq=-1)

    # Subscribe *before* replaying, so events published between the replay query and the
    # subscribe call are buffered in the queue rather than lost (closed by the seq filter below).
    q = store.subscribe(run_id)
    try:
        history = await _to_thread(_replay)
    except RunNotFound as e:
        store.unsubscribe(run_id, q)
        raise HTTPException(status_code=404, detail="run not found") from e

    # A terminal `result`/`error` event is always immediately followed by one trailing `ledger`
    # companion event (app/api/runner.py), so the *last* history row is not reliably the terminal
    # one — check the whole history instead.
    already_finished = any(row["event"] in ("result", "error") for row in history)

    async def gen():
        last_seq = 0
        for row in history:
            last_seq = row["seq"]
            payload = row["payload"]
            payload = payload if isinstance(payload, dict) else json.loads(payload)
            yield {"event": row["event"], "id": str(row["seq"]), "data": json.dumps(payload, default=str)}
        if already_finished:
            store.unsubscribe(run_id, q)
            return  # run already finished: replay is the whole story
        try:
            while True:
                envelope = await q.get()
                if envelope.get("seq", 0) <= last_seq:
                    continue  # already sent via replay (race between replay and subscribe)
                yield {"event": envelope["type"], "id": str(envelope["seq"]),
                      "data": json.dumps(envelope, default=str)}
                if envelope["type"] in ("result", "error"):
                    # A terminal event is normally followed by one trailing `ledger` companion
                    # (app/api/runner.py); wait briefly for it, but don't hang if it never comes
                    # (e.g. a driver-level exception published a bare `error` with no ledger entry).
                    try:
                        nxt = await asyncio.wait_for(q.get(), timeout=2.0)
                        if nxt["type"] == "ledger":
                            yield {"event": "ledger", "id": str(nxt["seq"]),
                                  "data": json.dumps(nxt, default=str)}
                    except TimeoutError:
                        pass
                    break
        finally:
            store.unsubscribe(run_id, q)

    return EventSourceResponse(gen())


@router.get("/runs/{run_id}", response_model=RunPublicState)
async def get_run(run_id: str) -> RunPublicState:
    store = get_run_store()

    def _get():
        with get_conn() as conn:
            return store.get(conn, run_id)

    try:
        row = await _to_thread(_get)
    except RunNotFound as e:
        raise HTTPException(status_code=404, detail="run not found") from e
    state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
    return RunPublicState(**{**state, "status": row["status"], "created_at": row["created_at"],
                             "updated_at": row["updated_at"], "workspace_id": row["workspace_id"]})


@router.post("/runs/{run_id}/clarify", response_model=RunCreateResponse)
async def clarify_run(run_id: str, body: ClarifyRequest) -> RunCreateResponse:
    store = get_run_store()

    def _get():
        with get_conn() as conn:
            return store.get(conn, run_id)

    try:
        row = await _to_thread(_get)
    except RunNotFound as e:
        raise HTTPException(status_code=404, detail="run not found") from e
    if row["status"] != "clarify":
        raise HTTPException(status_code=409, detail=f"run is {row['status']!r}, not awaiting clarification")
    asyncio.create_task(_drive_resume(run_id, body.answer))
    return RunCreateResponse(run_id=run_id)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(status: str | None = None, limit: int = 50, offset: int = 0) -> RunListResponse:
    """Run history (ui-spec.md "Agent console": "past runs with status, duration and cost")."""
    where, params = [], []
    if status:
        where.append("status = %s")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    def _run():
        with get_conn() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM run {clause}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT id, workspace_id, status, request->>'request' AS request, created_at, "
                f"updated_at FROM run {clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
            return total, rows

    total, rows = await _to_thread(_run)
    return RunListResponse(items=[RunSummary(**r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(run_id: str) -> RunTraceResponse:
    """Router candidates/scores/filtered reasons, critic/judge detail and the ledger hash for one
    run, assembled after the fact from `run_event` (+ `router_log` correlated by run_id, when
    present) — see app/agent/CONTRACT.md and RunTraceResponse's docstring (app/api/schemas.py)."""
    store = get_run_store()

    def _run():
        with get_conn() as conn:
            store.get(conn, run_id)  # raises RunNotFound
            events = store.events_since(conn, run_id, after_seq=-1)
            ledger_rows = conn.execute(
                "SELECT seq, hash FROM agent_ledger WHERE run_id = %s ORDER BY seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            return events, ledger_rows

    try:
        events, ledger_row = await _to_thread(_run)
    except RunNotFound as e:
        raise HTTPException(status_code=404, detail="run not found") from e

    critic, judge, fallbacks, route_trace = [], [], [], []
    for row in events:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        data = payload.get("data", {})
        if row["event"] == "critic":
            critic.append(data)
        elif row["event"] == "judge":
            judge.extend(data.get("verdicts", []))
        elif row["event"] == "fallback":
            fallbacks.append(data)
        elif row["event"] == "result":
            route_trace = (data.get("workspace") or {}).get("route_trace") or []

    def _router_log():
        import sqlite3

        from app.api.config import REPO_ROOT

        path = REPO_ROOT / "data" / "router_log.sqlite"
        if not path.exists():
            return []
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT step_id, task, model_id, outcome, latency_ms, shadow_cost_usd, record_json "
                "FROM router_log WHERE run_id = ? ORDER BY id", (run_id,),
            ).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["record"] = json.loads(d.pop("record_json"))
            except Exception:  # noqa: BLE001
                d["record"] = None
            out.append(d)
        return out

    router_log_rows = await _to_thread(_router_log)
    return RunTraceResponse(
        run_id=run_id, route_trace=route_trace, critic=critic, judge=judge, fallbacks=fallbacks,
        ledger_head=(ledger_row or {}).get("hash"), router_log=router_log_rows,
    )


@router.post("/ledger/verify", response_model=LedgerVerifyResponse)
async def verify_ledger(run_id: str) -> LedgerVerifyResponse:
    """`python -m app.ledger verify --run <run_id>` as an HTTP endpoint (phase4-agentic-core skill:
    the agent console's "Verify" button). Uses the canonical `app.ledger.Ledger` (agent-architect,
    same `agent_ledger` table/hash rule the stub runner and the real graph both write to) rather
    than reimplementing verification here."""

    def _run():
        from app.ledger import get_ledger

        led = get_ledger()
        entries = led.entries(run_id)
        if not entries:
            return None
        rep = led.verify(run_id)
        return rep, entries[-1].hash

    result = await _to_thread(_run)
    if result is None:
        raise HTTPException(status_code=404, detail="no ledger entries for this run_id")
    rep, head_hash = result
    first_bad_seq = rep.first_bad["seq"] if rep.first_bad else None
    return LedgerVerifyResponse(run_id=run_id, ok=rep.ok, first_bad_seq=first_bad_seq,
                                n_entries=rep.entries_checked, head_hash=head_hash)
