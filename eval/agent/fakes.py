"""Scripted offline providers for agent tests and the chaos suite (no network, no quota).

A ``Script`` answers router requests by role (detected from the system prompt): plan / sql / critic / judge /
present. Tests override any role with a callable ``f(req, ctx) -> str | Exception``. Every request is logged.
Install with ``install(script)``: a Router with these providers, all credentials present, live mode (so the
router's real eligibility, privacy, repair, fallback, breaker and chaos logic run unchanged).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.router.types import ProviderError, ProviderRequest, ProviderResponse

PROVIDERS = ("gemini", "groq", "cohere", "bedrock", "ollama", "mistral", "mistral_ocr")


def role_of(req: ProviderRequest) -> str:
    sys_txt = " ".join(m["content"] for m in req.messages if m["role"] == "system" and isinstance(m["content"], str))
    if "planner of a verification agent" in sys_txt:
        return "plan"
    if "read-only PostgreSQL" in sys_txt:
        return "sql"
    if "adversarial reviewer" in sys_txt:
        return "critic"
    if "You are the judge" in sys_txt:
        return "judge"
    if "narrative for a verification workspace" in sys_txt or "Summarise the inputs" in sys_txt:
        return "present"
    return "other"


def user_json(req: ProviderRequest) -> dict:
    txt = next((m["content"] for m in reversed(req.messages) if m["role"] == "user"), "")
    if isinstance(txt, list):
        txt = " ".join(p.get("text", "") for p in txt if isinstance(p, dict))
    m = re.search(r"Input:\n(\{.*\})\s*$", txt, re.DOTALL)
    ctx = json.loads(m.group(1)) if m else {}
    ctx["_text"] = txt
    return ctx


def default_plan(req, ctx) -> str:
    slots = ctx.get("slots", {})
    tools = {t["name"] for t in ctx.get("tools", [])}
    if "crop_presence" in tools:
        a = {"polygons_ref": slots.get("polygons_ref")}
        steps = [{"id": "s1", "tool": "crop_presence", "args": a}, {"id": "s2", "tool": "fallow_streak", "args": a}]
    else:
        scope = {"villages": slots.get("villages", [])}
        steps = [{"id": "s1", "tool": "lifecycle_status", "args": scope},
                 {"id": "s2", "tool": "spatial_query", "args": {"template": "area_by_group", "group_by": "village",
                                                                **scope}}]
    return json.dumps({"goal": "fake plan", "steps": steps, "outputs": ["kpis", "table", "map", "narrative"]})


def default_sql(req, ctx) -> str:
    return json.dumps({"sql": "SELECT v.name AS village, count(*) AS n_parcels FROM parcel p JOIN village v "
                              "ON v.id = p.village_id GROUP BY v.name ORDER BY v.name", "explanation": "count"})


def default_critic(req, ctx) -> str:
    return json.dumps({"challenges": []})


def default_judge(req, ctx) -> str:
    return json.dumps({"verdicts": [{"claim_id": c["id"], "verdict": "ACCEPT", "confidence": 0.85, "reason": "ok"}
                                    for c in ctx.get("claims", [])]})


def default_present(req, ctx) -> str:
    cl = ctx.get("claims", [])
    if not cl:
        return json.dumps({"en": "Summary of the inputs.", "ta": "சுருக்கம்.", "claim_refs": []})
    c = cl[0]
    return json.dumps({"en": f"The {c['field'].replace('_', ' ')} is {c['value']} [{c['id']}].",
                       "ta": f"மதிப்பு {c['value']} [{c['id']}].", "claim_refs": [c["id"]]}, ensure_ascii=False)


@dataclass
class Script:
    plan: Callable = default_plan
    sql: Callable = default_sql
    critic: Callable = default_critic
    judge: Callable = default_judge
    present: Callable = default_present
    per_model: dict[str, Callable] = field(default_factory=dict)    # model_id -> handler (overrides role)
    log: list[dict] = field(default_factory=list)

    def answer(self, req: ProviderRequest) -> ProviderResponse:
        role = role_of(req)
        ctx = user_json(req)
        h = self.per_model.get(f"{req.model_id}:{role}") or self.per_model.get(req.model_id) or \
            getattr(self, role, None) or (lambda r, c: "{}")
        self.log.append({"model_id": req.model_id, "role": role, "messages": req.messages})
        out = h(req, ctx)
        if isinstance(out, Exception):
            raise out
        return ProviderResponse(text=out, tokens_in=sum(len(str(m["content"])) for m in req.messages) // 4,
                                tokens_out=len(out) // 4, resolved_model=req.model_name, latency_ms=5)


class _P:
    def __init__(self, s: Script):
        self.s = s

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        return self.s.answer(req)


def install(script: Script, tmp_dir) -> Any:
    """Install a Router using the scripted providers (returns it)."""
    import os

    from app.router import set_router
    from app.router.core import Router
    os.environ["ROUTER_MODE"] = "live"
    p = _P(script)
    r = Router(providers={k: p for k in PROVIDERS}, credentials=lambda prov: (True, "fake"), doctor={},
               data_path=tmp_dir)
    set_router(r)
    return r


def rate_limited(req, ctx):
    return ProviderError("rate_limit", "fake 429", status=429)
