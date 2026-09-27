"""dispatch + execute: run ready plan steps in parallel (<= 4 at a time) through the tool registry / router;
outputs go to the run store (output_ref), the state keeps summaries only."""
from __future__ import annotations

import asyncio
import re
import time

from app.agent.nodes import out
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, route_since, save_pseudo
from app.agent.state import Attempt, PresentOutput, RunState, StepRecord, now_iso
from app.agent.store import load_output, save_output
from app.domains import get_pack
from app.tools.context import ToolContext, kg_dsn
from app.tools.registry import ToolError, ToolResult

MAX_CONCURRENCY = 4
LOW_CONFIDENCE = 0.3


def _main_list(d: dict):
    lists = [x for x in d.values() if isinstance(x, list) and x and isinstance(x[0], dict)]
    return lists[0] if len(lists) == 1 else None


def _pluck(rows: list, key: str):
    """Column of a row list: `parcel_uids` / `parcel_uid` -> [row.parcel_uid ...] (dedup, order kept)."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            x = r.get(key, r.get(key[:-1]) if key.endswith("s") else None)
            if x is not None and x not in out:
                out.append(x)
    return out


def _step(cur, p: str):
    if isinstance(cur, dict):
        if p in cur:
            return cur[p]
        ml = _main_list(cur)                    # "$s1.data.0.id" when the rows live under data.<list>
        return _step(ml, p) if ml is not None else None
    if isinstance(cur, list):
        if p.isdigit():
            return cur[int(p)] if int(p) < len(cur) else None
        return _pluck(cur, p) or None
    return None


def _resolve(v, outputs: dict[str, dict]):
    """`$<step>.<path>` references; tolerant of `[0]` indexing, an omitted main-list key and column plucking
    (generic over tools: the "main list" is the single list-of-objects in a dict)."""
    if isinstance(v, str) and v.startswith("$") and "." in v:
        sid, *path = re.sub(r"\[(\d+)\]", r".\1", v[1:].strip("{}")).split(".")
        cur = outputs.get(sid.strip("{}"))
        for p in (x for x in path if x):
            cur = _step(cur, p)
        return cur
    if isinstance(v, dict):
        return {k: _resolve(x, outputs) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x, outputs) for x in v]
    return v


async def dispatch(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    changed = True
    while changed:                       # propagate skips through the DAG
        changed = False
        for rec in st.steps.values():
            if rec.status == "pending" and any(st.steps[d].status in ("failed", "skipped")
                                               for d in rec.depends_on if d in st.steps):
                rec.status, rec.summary = "skipped", "dependency failed"
                changed = True
    st.next_node = "execute"
    remember(st)
    return out(st)


async def _run_step(st: RunState, rec: StepRecord, outputs: dict, pack, registry, pseudo, em: Emitter) -> None:
    rec.status, rec.started_at = "running", now_iso()
    args = _resolve(rec.args, outputs)
    em.emit("step_start", {"step_id": rec.id, "tool": rec.tool, "llm_task": rec.llm_task,
                           "args_preview": {k: (str(v)[:80]) for k, v in args.items()}, "attempt": len(rec.attempts) + 1})
    t0 = time.monotonic()
    res: ToolResult | None = None
    logs: list[dict] = []
    for attempt in range(2):
        n = len(rec.attempts) + 1
        try:
            if rec.tool:
                ctx = ToolContext(run_id=st.run_id, step_id=rec.id, domain=st.domain, slots=st.slots,
                                  attachments=st.attachments, excluded_models=rec.excluded, pseudo=pseudo,
                                  emit=lambda t, d: em.emit(t, d), dsn=kg_dsn())
                res = await registry.run(rec.tool, args, ctx)
                logs = ctx.route_logs
                st.telemetry.tool_calls += 1
            else:
                payload = {"system": "Summarise the inputs in <= 5 sentences. Use only numbers present in the inputs.",
                           "inputs": pseudo.pseudonymise({d: (outputs.get(d) or {}).get("summary") for d in rec.depends_on}),
                           "max_tokens": 400}
                obj, r, logs = await llm_json(em, "present", payload, PresentOutput, privacy_tier="PSEUDO",
                                              run_id=st.run_id, step_id=rec.id, exclude=rec.excluded)
                if obj is None:
                    raise ToolError("unavailable", f"summarise failed: {r.error if r else 'no model'}")
                res = ToolResult(data={"text": obj.en}, confidence=0.6, summary=obj.en[:200], model_id=r.model_id)
            rec.attempts.append(Attempt(n=n, by=rec.tool or rec.llm_task, outcome="ok",
                                        latency_ms=int((time.monotonic() - t0) * 1000)))
            break
        except ToolError as e:
            rec.attempts.append(Attempt(n=n, by=rec.tool or rec.llm_task, outcome=e.kind, error=str(e)[:300],
                                        latency_ms=int((time.monotonic() - t0) * 1000)))
            rec.error = f"{e.kind}: {str(e)[:300]}"
            if not (e.retryable and attempt == 0):
                break
            em.emit("fallback", {"step_id": rec.id, "from": rec.tool, "to": rec.tool, "reason": f"{e.kind}: retry once"})
    n0 = len(st.route_trace)
    tot = account(st, logs, rec.id)
    route = route_since(st, n0)
    rec.tokens_in, rec.tokens_out = tot["tokens_in"], tot["tokens_out"]
    rec.shadow_cost_usd, rec.actual_cost_usd = round(tot["shadow"], 6), round(tot["actual"], 6)
    rec.latency_ms = int((time.monotonic() - t0) * 1000)
    rec.ended_at = now_iso()
    if res is None:
        rec.status = "failed"
        em.emit("step_end", {"step_id": rec.id, "status": "failed", "tool": rec.tool, "error": rec.error,
                             "latency_ms": rec.latency_ms, "shadow_cost_usd": rec.shadow_cost_usd, "summary": "",
                             "actual_cost_usd": rec.actual_cost_usd, "tokens_in": rec.tokens_in,
                             "tokens_out": rec.tokens_out, "route": route})
        return
    dumped = res.model_dump(mode="json")
    rec.output_ref = save_output(st.run_id, rec.id, dumped)
    outputs[rec.id] = dumped
    rec.status, rec.error = "ok", None
    rec.summary, rec.n_rows, rec.confidence = res.summary[:300], res.n_rows, res.confidence
    if res.model_id:
        from app.router import vendor_of
        rec.model_id, rec.vendor = res.model_id, res.vendor or vendor_of(res.model_id)
    if res.confidence < LOW_CONFIDENCE:
        em.emit("fallback", {"step_id": rec.id, "from": rec.tool, "to": "review queue",
                             "reason": f"low confidence {res.confidence:.2f} (e.g. low OCR): claims go to REVIEW"})
    em.emit("step_end", {"step_id": rec.id, "status": "ok", "output_ref": rec.output_ref, "summary": rec.summary,
                         "n_rows": rec.n_rows, "latency_ms": rec.latency_ms, "model_id": rec.model_id, "tool": rec.tool,
                         "shadow_cost_usd": rec.shadow_cost_usd, "confidence": rec.confidence,
                         "actual_cost_usd": rec.actual_cost_usd, "tokens_in": rec.tokens_in,
                         "tokens_out": rec.tokens_out, "route": route})


def load_outputs(st: RunState) -> dict[str, dict]:
    outs = {}
    for sid, rec in st.steps.items():
        if rec.status == "ok" and rec.output_ref:
            try:
                outs[sid] = load_output(rec.output_ref)
            except (OSError, ValueError):
                pass
    return outs


async def execute(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    pack = get_pack(st.domain)
    registry = pack.registry()
    p = pseudo_for(st)
    em = Emitter(st, "execute", p)
    outputs = load_outputs(st)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def bounded(rec):
        async with sem:
            await _run_step(st, rec, outputs, pack, registry, p, em)

    while True:
        pending = [r for r in st.steps.values() if r.status == "pending"]
        if not pending:
            break
        ready = [r for r in pending if all(st.steps[d].status == "ok" for d in r.depends_on if d in st.steps)]
        blocked = [r for r in pending if any(st.steps[d].status in ("failed", "skipped") for d in r.depends_on
                                             if d in st.steps)]
        for r in blocked:
            r.status, r.summary = "skipped", "dependency failed"
        if not ready:
            for r in pending:
                if r.status == "pending":
                    r.status, r.summary = "skipped", "unsatisfiable dependencies"
            break
        await asyncio.gather(*(bounded(r) for r in ready))
    failed = [r.id for r in st.steps.values() if r.status == "failed"]
    retryable = [s for s in failed if len(st.steps[s].attempts) < 3]
    st.next_node = "recover" if (failed and (st.replans < 1 or retryable)) else "verify"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)
