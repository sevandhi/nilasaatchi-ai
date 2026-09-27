"""verify: turn tool claim seeds into Claims and run the deterministic checks named by the tools (plus
bbox-contains-value for page evidence and a source-confidence check). Unknown checks fail closed."""
from __future__ import annotations

import asyncio

from app.agent.nodes import out
from app.agent.nodes.execute import LOW_CONFIDENCE, load_outputs
from app.agent.runtime import Emitter, pseudo_for, remember, save_pseudo
from app.agent.state import CheckResult, Claim, Producer, RunState
from app.domains import get_pack
from app.tools import checks as generic_checks
from app.tools.context import kg_dsn

MAX_CLAIMS = 150


def _run_checks(claims: list[Claim], registry: dict, dsn: str, low_conf_steps: set[str]) -> None:
    generic_checks.clear_cache()
    for c in claims:
        names = list(dict.fromkeys(c.context.pop("_checks", [])))
        if any(e.kind == "page_bbox" and e.ref.startswith("extraction:") for e in c.evidence):
            names.append("bbox_contains_value")
        res = []
        for n in names:
            fn = registry.get(n)
            if fn is None:
                res.append(CheckResult(name=n, passed=False, detail="unknown check (fail closed)"))
                continue
            try:
                res.append(fn(c, dsn))
            except Exception as e:  # noqa: BLE001
                res.append(CheckResult(name=n, passed=False, detail=f"check error: {type(e).__name__}: {str(e)[:120]}"))
        if c.step_id in low_conf_steps:
            res.append(CheckResult(name="source_confidence", passed=False,
                                   detail=f"source confidence below {LOW_CONFIDENCE} (e.g. low OCR)"))
        c.checks = res


async def verify(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    pack = get_pack(st.domain)
    p = pseudo_for(st)
    em = Emitter(st, "verify", p)
    outputs = load_outputs(st)
    have = {c.step_id for c in st.claims}
    new: list[Claim] = []
    low = set()
    for sid, rec in st.steps.items():
        if rec.status != "ok" or not rec.verify or sid in have or sid not in outputs:
            continue
        if (rec.confidence or 1) < LOW_CONFIDENCE:
            low.add(sid)
        o = outputs[sid]
        prod = Producer(kind="model", id=rec.model_id, vendor=rec.vendor) if rec.model_id else \
            Producer(kind="tool", id=rec.tool or rec.llm_task or "?", vendor=None)
        for i, seed in enumerate(o.get("claims") or []):
            ctx = dict(seed.get("context") or {})
            ctx["_checks"] = seed.get("checks") or []
            new.append(Claim(id=f"c_{sid}_{i}", step_id=sid, kind=seed.get("kind", "kpi"), subject=str(seed["subject"]),
                             field=seed["field"], value=seed.get("value"), unit=seed.get("unit"),
                             evidence=seed.get("evidence") or [], producer=prod, context=ctx,
                             confidence=min(float(seed.get("confidence", 0.8)), float(o.get("confidence") or 1))))
    new = new[:max(0, MAX_CLAIMS - len(st.claims))]
    await asyncio.to_thread(_run_checks, new, pack.checks, kg_dsn(), low)
    for c in new:
        if all(ch.passed for ch in c.checks):
            c.status = "verified" if c.checks else "pending"
    st.claims = st.claims + new
    passed = sum(1 for c in new for ch in c.checks if ch.passed)
    failed = sum(1 for c in new for ch in c.checks if not ch.passed)
    em.emit("verify", {"claims_checked": len(new), "passed": passed, "failed": failed,
                       "checks": [{"claim_id": c.id, "name": ch.name, "passed": ch.passed, "detail": ch.detail[:160]}
                                  for c in new for ch in c.checks][:60]})
    st.next_node = "critic"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)
