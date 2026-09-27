"""Shared runtime for graph nodes: event emission (+ ledger append), LLM JSON calls with schema validation and
fallback, pseudonymiser per run, telemetry / route-trace accounting. Domain-agnostic."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.state import AgentEvent, RouteTraceEntry, RunState, llm_schema, now_iso

log = logging.getLogger("app.agent")
_SEQ_LOCK = threading.Lock()
_FALLBACK_SEQ: dict[str, int] = {}
LATEST_STATE: dict[str, dict] = {}          # run_id -> public dict (fast path for get_run_state)


# ------------------------------------------------------------------------------------ pseudonymiser
@lru_cache(maxsize=8)
def known_names(dsn: str) -> tuple[str, ...]:
    """Every owner-name spelling in the local KG (never leaves the machine)."""
    from app.tools.db import query
    try:
        rows = query(dsn, "SELECT canonical_name AS n FROM owner UNION SELECT unnest(variants) FROM owner "
                          "UNION SELECT relation_name FROM owner WHERE relation_name IS NOT NULL "
                          "UNION SELECT raw_text FROM extraction_owner WHERE raw_text IS NOT NULL",
                     readonly_role=False)
    except Exception as e:  # noqa: BLE001 - no owner table yet: markers-only pseudonymisation
        log.warning("known_names unavailable: %s", e)
        return ()
    return tuple(sorted({r["n"].strip() for r in rows if r["n"] and len(r["n"].strip()) >= 3}))


_NOISE = re.compile(r"[0-9()%/:=]")


def clean_names(names, non_name_words=frozenset()) -> tuple[str, ...]:
    """Drop OCR noise mis-filed as owner names (amounts in words, table headers, bare suffixes such as 'கள்')
    so they never mask ordinary request words. Real names are short, digit-free and not document vocabulary."""
    stop = {w.casefold() for w in non_name_words}
    out = []
    for n in names:
        words = n.split()
        if len(n) < 4 or len(words) > 5 or _NOISE.search(n) or any(w.casefold() in stop for w in words):
            continue
        out.append(n)
    return tuple(out)


def pseudo_for(state: RunState):
    from app.domains import get_pack
    from app.router.gateway import Pseudonymiser
    from app.tools.context import kg_dsn
    try:
        names = known_names(kg_dsn())
    except RuntimeError:
        names = ()
    try:
        stop = get_pack(state.domain).non_name_words
    except Exception:  # noqa: BLE001 - unknown domain: generic filter only
        stop = frozenset()
    return Pseudonymiser(clean_names(names, stop), state=state.private.get("pseudo"))


def save_pseudo(state: RunState, p) -> dict:
    priv = dict(state.private)
    priv["pseudo"] = p.to_state()
    state.private = priv
    return priv


# ------------------------------------------------------------------------------------ events + ledger
def _writer():
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except Exception:  # noqa: BLE001 - outside a graph run (unit tests)
        return None


class Emitter:
    """Emits AgentEvents to the stream and appends each one to the hash-chained ledger."""

    def __init__(self, state: RunState, node: str, pseudo=None, sink: list | None = None):
        self.run_id = state.run_id
        self.node = node
        self.pseudo = pseudo
        self.writer = _writer()
        self.sink = sink
        self.head: tuple[int, str] | None = None
        self.n = 0

    def emit(self, etype: str, data: dict, *, ledger_payload: dict | None = None) -> AgentEvent:
        ui = self.pseudo.pseudonymise(data, amounts=False) if self.pseudo else data
        led = ledger_payload if ledger_payload is not None else (
            self.pseudo.pseudonymise(data, amounts=True) if self.pseudo else data)
        seq = None
        try:
            from app.ledger import get_ledger
            e = get_ledger().append(self.run_id, self.node, etype, {"type": etype, "data": led})
            seq, self.head = e.seq, (e.seq, e.hash)
        except Exception as ex:  # noqa: BLE001 - ledger down: keep streaming, mark it
            log.warning("ledger append failed: %s", ex)
            with _SEQ_LOCK:
                _FALLBACK_SEQ[self.run_id] = _FALLBACK_SEQ.get(self.run_id, 100000) + 1
                seq = _FALLBACK_SEQ[self.run_id]
        ev = AgentEvent(type=etype, run_id=self.run_id, seq=seq, ts=now_iso(), node=self.node,
                        data=json.loads(json.dumps(ui, ensure_ascii=False, default=str)))
        self.n += 1
        if self.writer is not None:
            self.writer(ev.model_dump())
        if self.sink is not None:
            self.sink.append(ev.model_dump())
        return ev

    def close(self) -> str | None:
        """Emit the per-node `ledger` event (current head before this event); returns the head hash."""
        if self.head is None:
            return None
        seq, h = self.head
        self.emit("ledger", {"node": self.node, "head_seq": seq, "head_hash": h, "n_entries": self.n})
        return self.head[1]


# ------------------------------------------------------------------------------------ LLM calls
def fallback_events(em: Emitter, task: str, route_log: dict, step_id: str | None = None) -> None:
    prev = None
    for a in route_log.get("attempts", []):
        if prev and a["model_id"] != prev["model_id"] and prev["outcome"] not in ("ok", "repair_ok"):
            em.emit("fallback", {"step_id": step_id, "task": task, "from": prev["model_id"], "to": a["model_id"],
                                 "reason": prev.get("error_kind") or prev["outcome"]})
        prev = a


async def llm_json(em: Emitter, task: str, payload: dict, model_cls: type[BaseModel], *, privacy_tier: str,
                   run_id: str, step_id: str, producer_vendor: str | None = None, exclude: list[str] | None = None,
                   only_model: str | None = None, attempts: int = 2, schema: dict | None = None):
    """Router call -> JSON-schema validation (router repairs once) -> Pydantic validation; on a Pydantic failure the
    model is excluded and the call falls back to the next eligible model. Returns (obj|None, result|None, logs).
    ``schema`` overrides the schema derived from ``model_cls`` (e.g. the per-pack planner schema)."""
    from app.router import RouterConfigError, call
    from app.tools.context import agent_excluded_models
    excl = sorted(set(exclude or []) | set(agent_excluded_models()))
    logs: list[dict] = []
    res = None
    for _ in range(attempts):
        try:
            res = await asyncio.to_thread(
                call, task, {**payload, "run_id": run_id, "step_id": step_id},
                schema=schema if schema is not None else llm_schema(model_cls),
                privacy_tier=privacy_tier, producer_vendor=producer_vendor, exclude_models=excl or None,
                only_model=only_model)
        except RouterConfigError as e:
            log.warning("router config error for %s: %s", task, e)
            return None, None, logs
        logs.append(res.route_log)
        fallback_events(em, task, res.route_log, step_id)
        if not res.ok or res.data is None:
            return None, res, logs
        try:
            return model_cls.model_validate(res.data), res, logs
        except ValidationError as e:
            em.emit("fallback", {"step_id": step_id, "task": task, "from": res.model_id, "to": "next eligible model",
                                 "reason": f"schema_invalid: {str(e.errors(include_url=False)[:1])[:160]}"})
            excl.append(res.model_id)
            if only_model:
                return None, res, logs
    return None, res, logs


# ------------------------------------------------------------------------------------ accounting
def route_since(state: RunState, n0: int) -> list[dict]:
    """Route-trace entries added since index n0 (streamed live on step_end / critic / judge events)."""
    return [e.model_dump(mode="json") for e in state.route_trace[n0:]]


def account(state: RunState, logs: list[dict], step_id: str) -> dict[str, float]:
    """Route-trace entries + telemetry from router logs. Returns {tokens_in, tokens_out, shadow, actual, latency}."""
    from app.router import get_router
    from app.router.scorer import actual_cost, estimate_shadow_cost
    reg = get_router().registry
    tot = {"tokens_in": 0, "tokens_out": 0, "shadow": 0.0, "actual": 0.0, "latency": 0}
    for lg in logs:
        shadow = actual = 0.0
        lat = 0
        for a in lg.get("attempts", []):
            if a.get("outcome") not in ("ok", "repair_ok"):      # schema_invalid* entries repeat the call's tokens
                lat += int(a.get("latency_ms") or 0) if a.get("outcome", "").endswith("error") else 0
                continue
            tin, tout = int(a.get("tokens_in") or 0), int(a.get("tokens_out") or 0)
            lat += int(a.get("latency_ms") or 0)
            try:
                spec = reg.model(a["model_id"])
                s = estimate_shadow_cost(spec, tin, tout, 0)
                shadow += s
                actual += actual_cost(spec, s, 0)
            except Exception:  # noqa: BLE001
                pass
            tot["tokens_in"] += tin
            tot["tokens_out"] += tout
        tot["shadow"] += shadow
        tot["actual"] += actual
        tot["latency"] += lat
        choice = lg.get("choice")
        sc = (lg.get("scores") or {}).get(choice, {}) if choice else {}
        reason = (f"chose {choice} (score {sc.get('total', 0):.3f}; order {lg.get('order')})" if choice
                  else f"no model succeeded (terminal={lg.get('terminal')})")
        if lg.get("excluded"):
            reason += f"; excluded {lg['excluded']}"
        state.route_trace.append(RouteTraceEntry(
            step_id=step_id, task=lg.get("task", ""), choice=choice, reason=reason,
            candidates=lg.get("candidates", []), filtered=lg.get("filtered", []),
            attempts=[{k: a.get(k) for k in ("model_id", "outcome", "error_kind", "latency_ms", "tokens_in",
                                             "tokens_out")} for a in lg.get("attempts", [])],
            latency_ms=lat, shadow_cost_usd=round(shadow, 6),
            scores={m: round(float(v.get("total", 0) if isinstance(v, dict) else v), 6)
                    for m, v in (lg.get("scores") or {}).items()},
            privacy_tier=lg.get("privacy_tier"), effective_tier=lg.get("effective_tier"),
            tokens_in=sum(int(a.get("tokens_in") or 0) for a in lg.get("attempts", [])
                          if a.get("outcome") in ("ok", "repair_ok")),
            tokens_out=sum(int(a.get("tokens_out") or 0) for a in lg.get("attempts", [])
                           if a.get("outcome") in ("ok", "repair_ok")),
            actual_cost_usd=round(actual, 6)))
        state.telemetry.llm_calls += 1
    state.telemetry.tokens_in += tot["tokens_in"]
    state.telemetry.tokens_out += tot["tokens_out"]
    state.telemetry.shadow_cost_usd = round(state.telemetry.shadow_cost_usd + tot["shadow"], 6)
    state.telemetry.actual_cost_usd = round(state.telemetry.actual_cost_usd + tot["actual"], 6)
    return tot


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def remember(state: RunState) -> None:
    state.touch()
    LATEST_STATE[state.run_id] = state.public_dict()
