"""Public entry points (contract: app/agent/CONTRACT.md)."""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agent.graph import RECURSION_LIMIT, build_graph, checkpointer
from app.agent.runtime import LATEST_STATE
from app.agent.state import AgentEvent, Attachment, RunState, now_iso
from app.agent.store import load_output, runs_dir
from app.domains import DEFAULT_PACK, any_to_attachment

TERMINAL = {"result", "clarify", "error"}


async def _stream(inp: dict | RunState, run_id: str) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
    terminal = False
    async with checkpointer() as (saver, kind):
        graph = build_graph().compile(checkpointer=saver)
        try:
            async for chunk in graph.astream(inp, config, stream_mode="custom"):
                if isinstance(chunk, dict) and chunk.get("type"):
                    if kind == "memory" and chunk["type"] == "result":
                        chunk.setdefault("data", {})["checkpointer"] = "memory"
                    terminal = terminal or chunk["type"] in TERMINAL
                    yield chunk
        except Exception as e:  # noqa: BLE001 - unrecoverable graph error -> terminal error event
            yield AgentEvent(type="error", run_id=run_id, seq=0, ts=now_iso(), node="graph",
                             data={"message": f"{type(e).__name__}: {str(e)[:300]}", "node": "graph",
                                   "recoverable": False}).model_dump()
            return
    if not terminal:
        yield AgentEvent(type="error", run_id=run_id, seq=0, ts=now_iso(), node="graph",
                         data={"message": "run ended without a result", "node": "graph",
                               "recoverable": False}).model_dump()


async def run_request(request: str, attachments: list[Attachment | dict] = (), workspace_id: str | None = None, *,
                      run_id: str | None = None, domain: str | None = None, lang: str | None = None,
                      base_workspace: dict | None = None) -> AsyncIterator[dict]:
    if not isinstance(request, str):
        raise TypeError("request must be a string")
    run_id = run_id or uuid.uuid4().hex
    st = RunState(run_id=run_id, workspace_id=workspace_id, request=request,
                  attachments=[any_to_attachment(a) for a in attachments], domain=domain or DEFAULT_PACK,
                  domain_explicit=bool(domain), base_workspace=base_workspace, lang=lang or "en")
    async for ev in _stream(st, run_id):
        yield ev


async def resume_run(run_id: str, answer: str | dict) -> AsyncIterator[dict]:
    ans = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    async with checkpointer() as (saver, _):
        graph = build_graph().compile(checkpointer=saver)
        snap = await graph.aget_state({"configurable": {"thread_id": run_id}})
    if not snap or not snap.values:
        yield AgentEvent(type="error", run_id=run_id, seq=0, ts=now_iso(), node="graph",
                         data={"message": "unknown run", "node": "graph", "recoverable": False}).model_dump()
        return
    vals = snap.values if isinstance(snap.values, dict) else dict(snap.values)
    st = RunState.model_validate({**{k: v for k, v in vals.items()}, "clarify_answer": ans, "status": "running",
                                  "next_node": None})
    async for ev in _stream(st, run_id):
        yield ev


def get_run_state(run_id: str) -> dict | None:
    if run_id in LATEST_STATE:
        return LATEST_STATE[run_id]
    p = runs_dir() / run_id / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    from app.agent.graph import checkpoint_dsn
    dsn = checkpoint_dsn()
    if not dsn:
        return None
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        with PostgresSaver.from_conn_string(dsn) as s:
            t = s.get_tuple({"configurable": {"thread_id": run_id}})
        if t is None:
            return None
        vals: dict[str, Any] = t.checkpoint.get("channel_values", {})
        return RunState.model_validate({k: v for k, v in vals.items() if k in RunState.model_fields}).public_dict()
    except Exception:  # noqa: BLE001
        return None


__all__ = ["get_run_state", "load_output", "resume_run", "run_request"]
