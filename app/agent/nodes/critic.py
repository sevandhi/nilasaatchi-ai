"""critic: adversarial challenges from a model whose vendor differs from the claim's producer (D-017: the router
receives producer_vendor and excludes that vendor). The critic proposes {claim_id, hypothesis, check{tool,args}};
the system runs the check with the producer's model excluded and decides whether the claim was refuted."""
from __future__ import annotations

import json
import math

from app.agent.nodes import out
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, route_since, save_pseudo
from app.agent.state import Claim, CriticChallenge, CriticCheck, CriticOutput, RunState
from app.domains import get_pack
from app.tools.context import ToolContext, kg_dsn
from app.tools.registry import ToolError

PROMPT = (__import__("pathlib").Path(__file__).resolve().parents[3] / "prompts" / "critic.md")
KIND_RANK = {"finding": 0, "discrepancy": 0, "kpi": 1, "entity": 2, "table_cell": 3, "narrative": 4}
MAX_CANDIDATES = 8
MAX_CHALLENGES = 4
TOOL_VENDOR = "deterministic-tool"


def candidates(st: RunState) -> list[Claim]:
    done = {c.claim_id for c in st.critic if not c.result.get("superseded")}
    cs = [c for c in st.claims if c.id not in done and all(ch.passed for ch in c.checks)]
    cs.sort(key=lambda c: (KIND_RANK.get(c.kind, 5), -(c.confidence or 0), c.id))
    return cs[:MAX_CANDIDATES]


def _nums(obj) -> list[float]:
    out_ = []
    if isinstance(obj, bool):
        return out_
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out_ += _nums(v)
    if isinstance(obj, list):
        for v in obj[:3]:
            out_ += _nums(v)
    return out_


def evaluate(claim: Claim, result) -> dict:
    """Generic refutation test. Numbers: the check's first comparable number must match within 1 %.
    Findings/discrepancies: the subject + field must re-appear in the check output."""
    data = result.data
    if claim.kind in ("finding", "discrepancy", "entity"):
        blob = json.dumps(data, default=str, ensure_ascii=False)
        found = claim.subject in blob and (claim.field in blob or claim.kind == "entity")
        return {"refuted": not found, "detail": "reproduced by the check" if found else
                "check output does not reproduce the claim"}
    if isinstance(claim.value, (int, float)) and not isinstance(claim.value, bool):
        seeds = [c for c in (result.claims or []) if c.field == claim.field and isinstance(c.value, (int, float))
                 and not isinstance(c.value, bool)]
        rows = (data or {}).get("rows") if isinstance(data, dict) else None
        if seeds:
            cand = [float(seeds[0].value)]
        elif isinstance(rows, list) and len(rows) == 1 and len(_nums(rows[0])) == 1:
            cand = _nums(rows[0])            # an independent single-value recomputation (e.g. a count)
        else:                                # never compare against an unrelated first number
            return {"refuted": False, "detail": "inconclusive: check returned no directly comparable value"}
        x = cand[0]
        ok = math.isclose(x, float(claim.value), rel_tol=0.01, abs_tol=0.01)
        return {"refuted": not ok, "detail": f"independent value {x} vs claimed {claim.value}"}
    return {"refuted": False, "detail": "inconclusive: non-numeric claim"}


async def critic(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    pack = get_pack(st.domain)
    reg = pack.registry()
    p = pseudo_for(st)
    em = Emitter(st, "critic", p)
    cands = candidates(st)
    groups: dict[str, list[Claim]] = {}
    # Tool claims were selected/parameterised by the planner model: the critic must not share its vendor.
    from app.router import vendor_of
    plan_models = [r.choice for r in st.route_trace if r.task == "plan" and r.choice]
    try:
        tool_vendor = (vendor_of(plan_models[-1]) if plan_models else None) or TOOL_VENDOR
    except Exception:  # noqa: BLE001 - template plan / unknown id
        tool_vendor = TOOL_VENDOR
    for c in cands:
        groups.setdefault(c.producer.vendor or tool_vendor, []).append(c)
    allowed = [t for t in reg.catalog() if t["name"] in pack.critic_tools]
    n_done = 0
    if not cands:
        em.emit("critic", {"skipped": True, "reason": "no claims eligible for challenge"})
    for vendor, cl in sorted(groups.items())[:2]:
        payload = {"system": PROMPT.read_text(encoding="utf-8"),
                   "prompt": f"User request: {p.text(st.request)}",
                   "claims": p.pseudonymise([{"id": c.id, "kind": c.kind, "subject": c.subject, "field": c.field,
                                              "value": c.value, "unit": c.unit, "producer": c.producer.kind,
                                              "checks": [f"{ch.name}:{'pass' if ch.passed else 'FAIL'}" for ch in c.checks],
                                              "evidence": [e.excerpt[:160] for e in c.evidence[:2] if e.excerpt]}
                                             for c in cl]),
                   "allowed_check_tools": allowed, "max_tokens": 900}
        obj, res, logs = await llm_json(em, "critic", payload, CriticOutput, privacy_tier="PSEUDO", run_id=st.run_id,
                                        step_id=f"critic_{vendor}", producer_vendor=vendor)
        n_route = len(st.route_trace)
        account(st, logs, f"critic_{vendor}")
        if obj is None:
            em.emit("critic", {"skipped": True, "reason": f"no eligible critic for producer vendor {vendor}: "
                                                          f"{(res.error if res else 'router refused')}"[:200],
                               "producer_vendor": vendor, "route": route_since(st, n_route)})
            continue
        if not obj.challenges and cl:   # a critic must test something: re-ask once, same model family chain
            payload2 = {**payload, "prompt": payload["prompt"] + "\n\nYou returned no challenge. You MUST return "
                        "exactly one challenge for the most material claim, with a check tool call that recomputes "
                        "it independently."}
            obj2, res2, logs2 = await llm_json(em, "critic", payload2, CriticOutput, privacy_tier="PSEUDO",
                                               run_id=st.run_id, step_id=f"critic_{vendor}_reask", producer_vendor=vendor)
            account(st, logs2, f"critic_{vendor}_reask")
            if obj2 is not None and obj2.challenges:
                obj, res = obj2, res2
        cvendor = vendor_of(res.model_id)
        critic_route = route_since(st, n_route)
        by_id = {c.id: c for c in cl}
        if not obj.challenges:
            em.emit("critic", {"skipped": True, "reason": "critic raised no challenge", "model_id": res.model_id,
                               "vendor": cvendor, "producer_vendor": vendor, "claims_reviewed": [c.id for c in cl],
                               "route": critic_route})
        for ch in obj.challenges:
            if n_done >= MAX_CHALLENGES:
                break
            claim = by_id.get(ch.claim_id)
            if claim is None or ch.check.tool not in pack.critic_tools:
                continue
            n_done += 1
            step = st.steps.get(claim.step_id)
            excl = sorted(set((step.excluded if step else []) +
                              ([claim.producer.id] if claim.producer.kind == "model" else [])))
            ctx = ToolContext(run_id=st.run_id, step_id=f"critic_{claim.id}", domain=st.domain, slots=st.slots,
                              attachments=st.attachments, excluded_models=excl, pseudo=p,
                              emit=lambda t, d: em.emit(t, d), dsn=kg_dsn())
            try:
                r = await reg.run(ch.check.tool, ch.check.args, ctx)
                verdict = evaluate(claim, r)
            except ToolError as e:
                verdict = {"refuted": False, "detail": f"check could not run: {e.kind}: {str(e)[:120]}"}
            n_check = len(st.route_trace)
            account(st, ctx.route_logs, f"critic_{claim.id}")
            cc = CriticChallenge(claim_id=claim.id, hypothesis=ch.hypothesis[:400],
                                 check=CriticCheck(tool=ch.check.tool, args=ch.check.args), result=verdict,
                                 model_id=res.model_id, vendor=cvendor, producer_vendor=vendor)
            st.critic.append(cc)
            em.emit("critic", {"claim_id": claim.id, "hypothesis": cc.hypothesis, "check": cc.check.model_dump(),
                               "result": verdict, "model_id": res.model_id, "vendor": cvendor, "producer_vendor": vendor,
                               "route": critic_route, "check_route": route_since(st, n_check)})
    st.next_node = "judge"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)
