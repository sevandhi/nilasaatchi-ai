"""Wires `POST /runs` / SSE to `app.agent.run_request` / `app.agent.resume_run`
(app/agent/CONTRACT.md §1). `get_run_request()`/`get_resume_run()` return the real functions once
`app/agent/__init__.py` exports them; until then every run is driven by `stub_run_request`/
`stub_resume_run`, a deterministic simulation that emits properly-shaped `AgentEvent`s (contract §2)
— including a real, chained `agent_ledger` entry per event (app/api/ledger_stub.py) — so the
frontend and tests can be built against the real wire shape now. The swap is automatic (no code
change) once `app.agent.run_request`/`resume_run` exist.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC
from typing import Any, Protocol

from app.api import ledger_stub
from app.api.db import get_conn

try:
    from app.agent.state import now_iso
except ImportError:  # pragma: no cover
    from datetime import datetime

    def now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds")


class RunRequestFn(Protocol):
    def __call__(self, request: str, attachments: list[dict] | None = None,
                workspace_id: str | None = None, *, run_id: str | None = None,
                domain: str | None = None, lang: str | None = None,
                base_workspace: dict | None = None) -> AsyncIterator[dict[str, Any]]: ...


class ResumeRunFn(Protocol):
    def __call__(self, run_id: str, answer: Any) -> AsyncIterator[dict[str, Any]]: ...


def _force_stub() -> bool:
    """AGENT_FORCE_STUB=1 pins the API to the deterministic stub regardless of whether
    app.agent.run_request/resume_run exist — used by tests/api (see tests/api/conftest.py) so
    `make test` stays green and deterministic independent of the real agent's day-to-day state."""
    return os.environ.get("AGENT_FORCE_STUB") == "1"


def get_run_request() -> tuple[RunRequestFn, bool]:
    """Returns (fn, is_stub)."""
    if _force_stub():
        return stub_run_request, True
    try:
        mod = importlib.import_module("app.agent")
        fn = mod.run_request
    except (ImportError, AttributeError):
        return stub_run_request, True
    return fn, False


def get_resume_run() -> tuple[ResumeRunFn, bool]:
    if _force_stub():
        return stub_resume_run, True
    try:
        mod = importlib.import_module("app.agent")
        fn = mod.resume_run
    except (ImportError, AttributeError):
        return stub_resume_run, True
    return fn, False


class _StubLedger:
    """Per-run seq counter + `agent_ledger` append, standing in for what `app.agent.graph` will do
    internally before yielding each event (contract §5). Every AgentEvent — including the `ledger`
    companion event itself — gets one chained row, so `agent_ledger` for a stub run is a genuinely
    verifiable chain (see tests/api/test_ledger_stub_unit.py)."""

    def __init__(self, run_id: str, start_seq: int = 0) -> None:
        self.run_id = run_id
        self.seq = start_seq

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    async def append(self, seq: int, node: str, event_type: str, data: dict[str, Any], ts: str) -> str:
        def _run():
            with get_conn() as conn:
                return ledger_stub.append(conn, self.run_id, seq, node, event_type, data, ts)

        return await asyncio.to_thread(_run)


async def _emit(ledger: _StubLedger, node: str, event_type: str, data: dict[str, Any]
               ) -> AsyncIterator[dict[str, Any]]:
    seq = ledger.next_seq()
    ts = now_iso()
    head = await ledger.append(seq, node, event_type, data, ts)
    yield {"type": event_type, "run_id": ledger.run_id, "seq": seq, "ts": ts, "node": node, "data": data}

    lseq = ledger.next_seq()
    lts = now_iso()
    ldata = {"seq": seq, "node": node, "hash": head, "n_entries": lseq}
    await ledger.append(lseq, "ledger", "ledger", ldata, lts)
    yield {"type": "ledger", "run_id": ledger.run_id, "seq": lseq, "ts": lts, "node": "ledger", "data": ldata}


async def _pipeline(ledger: _StubLedger, request: str, run_id: str, workspace_id: str | None,
                    domain: str, lang: str) -> AsyncIterator[dict[str, Any]]:
    text = request.lower()

    if "unseen tool" in text:
        async for e in _emit(ledger, "execute", "error",
                             {"message": "no tool matches this request (stub runner: unsupported)",
                              "node": "execute", "recoverable": False}):
            yield e
        return

    step1 = {"id": "s1", "tool": "catalog_search", "args": {"request": request}, "depends_on": [],
            "expected_output_schema": "ToolResult", "verify": True, "rationale": "stub"}
    step2 = {"id": "s2", "llm_task": "present", "args": {}, "depends_on": ["s1"],
            "expected_output_schema": "ToolResult", "verify": True, "rationale": "stub"}
    async for e in _emit(ledger, "planner", "plan",
                        {"plan": {"goal": request, "steps": [step1, step2], "outputs": ["workspace"]},
                         "model_id": "stub-deterministic", "attempt": 1, "repaired": False}):
        yield e

    for step in (step1, step2):
        async for e in _emit(ledger, "execute", "step_start",
                             {"step_id": step["id"], "tool": step.get("tool"),
                              "llm_task": step.get("llm_task"), "args_preview": step["args"], "attempt": 1}):
            yield e
        if "chaos" in text and step["id"] == "s1":
            async for e in _emit(ledger, "execute", "fallback",
                                 {"step_id": step["id"], "task": step.get("tool") or step.get("llm_task"),
                                  "from": "primary", "to": "local", "reason": "429 (chaos)"}):
                yield e
        output_ref = f"run://{run_id}/{step['id']}"
        async for e in _emit(ledger, "execute", "step_end",
                             {"step_id": step["id"], "status": "ok", "output_ref": output_ref,
                              "summary": "stub result", "n_rows": 0, "latency_ms": 1,
                              "model_id": "stub-deterministic", "shadow_cost_usd": 0.0}):
            yield e

    claim = {"id": "c1", "step_id": "s1", "kind": "narrative", "subject": request[:80], "field": "summary",
            "value": None, "unit": None, "evidence": [], "producer": {"kind": "tool", "id": "catalog_search"},
            "checks": [{"name": "stub_check", "passed": True,
                       "detail": "stub runner: no deterministic checks configured"}],
            "status": "verified", "verdict": "ACCEPT", "confidence": 0.5, "context": {}}
    async for e in _emit(ledger, "verify", "verify",
                        {"claims_checked": 1, "passed": 1, "failed": 0, "claims": [claim],
                         "checks": [{"claim_id": "c1", "name": "stub_check", "passed": True,
                                    "detail": "stub runner: no deterministic checks configured"}]}):
        yield e
    async for e in _emit(ledger, "critic", "critic",
                        {"claim_id": "c1", "hypothesis": "stub runner: no critic configured",
                         "check": {"tool": "none", "args": {}}, "result": {"refuted": False, "detail": "stub"},
                         "model_id": None, "vendor": None, "producer_vendor": None,
                         "skipped": True, "reason": "stub runner has no critic wired in"}):
        yield e
    async for e in _emit(ledger, "judge", "judge",
                        {"verdicts": [{"claim_id": "c1", "verdict": "ACCEPT", "confidence": 0.5,
                                      "reason": "stub runner: placeholder judge"}], "model_id": None}):
        yield e

    workspace_spec = {
        "spec_version": "1", "workspace_id": workspace_id, "version": None,
        "title": (request[:80] or "Untitled workspace"), "domain": domain, "run_id": run_id,
        "created_at": now_iso(), "ledger_head": None, "lang": lang,
        "kpis": [],
        "map": {"bbox": None, "layers": [
            {"id": "l1", "title": "Park boundary", "kind": "reference", "ref_layer": "ref_layer_park_boundary"},
        ], "time_slider": None},
        "tables": [{"id": "t1", "title": "Stub results",
                   "columns": [{"key": "note", "label": "Note", "type": "string"}],
                   "rows": [{"note": f"Stub runner: app.agent.run_request not found for {request!r}"}],
                   "claim_ids": ["c1"], "sql": None}],
        "charts": [], "timeline": None,
        "narrative": {"en": f"Stub runner: no agent graph is wired in yet for {request!r} "
                            "(app.agent.run_request not found; see phase4-agentic-core skill). "
                            "This is a deterministic placeholder result.", "ta": None,
                     "claim_refs": ["c1"]},
        "evidence": [],
        "verification": {"summary": {"accepted": 1, "downgraded": 0, "review": 0, "rerouted": 0},
                         "claims": [{"claim_id": "c1", "verdict": "ACCEPT", "confidence": 0.5,
                                    "checks": [], "critic": None}]},
        "sql": [], "route_trace": [],
        "caveats": ["stub runner: app.agent.run_request not found (phase4-agentic-core skill)",
                   "findings are signals requiring field verification, not legal conclusions"],
    }
    async for e in _emit(ledger, "present", "result",
                        {"state": {"run_id": run_id, "status": "done"}, "workspace": workspace_spec,
                         "degraded": True}):
        yield e


async def stub_run_request(request: str, attachments: list[dict] | None = None,
                           workspace_id: str | None = None, *, run_id: str | None = None,
                           domain: str | None = None, lang: str | None = None,
                           base_workspace: dict | None = None) -> AsyncIterator[dict[str, Any]]:
    run_id = run_id or uuid.uuid4().hex
    domain = domain or "land_acquisition"
    lang = lang or "en"
    ledger = _StubLedger(run_id)
    text = request.lower()

    if "clarify" in text:
        # intake-stage clarification: paused before planning even starts (contract §2: `clarify`
        # may come from `intake` or `execute`); `resume_run` re-enters `_pipeline` below.
        async for e in _emit(ledger, "intake", "clarify",
                            {"question": "Which village is this about?", "missing": ["village"]}):
            yield e
        return

    async for e in _pipeline(ledger, request, run_id, workspace_id, domain, lang):
        yield e


async def stub_resume_run(run_id: str, answer: Any) -> AsyncIterator[dict[str, Any]]:
    def _last_seq() -> int:
        with get_conn() as conn:
            row = conn.execute("SELECT COALESCE(max(seq), 0) AS n FROM agent_ledger WHERE run_id = %s",
                              (run_id,)).fetchone()
            return row["n"]

    def _last_request() -> dict[str, Any] | None:
        with get_conn() as conn:
            return conn.execute("SELECT request, workspace_id FROM run WHERE id = %s", (run_id,)).fetchone()

    start_seq = await asyncio.to_thread(_last_seq)
    row = await asyncio.to_thread(_last_request)
    if row is None:
        raise KeyError(f"unknown run_id {run_id!r}")
    req = row["request"] if isinstance(row["request"], dict) else json.loads(row["request"])
    ledger = _StubLedger(run_id, start_seq=start_seq)
    async for e in _pipeline(ledger, f"{req.get('request', '')} {answer}".strip(), run_id,
                            row["workspace_id"], req.get("domain") or "land_acquisition",
                            req.get("lang") or "en"):
        yield e
