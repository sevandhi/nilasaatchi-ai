"""judge: verdict per claim (ACCEPT / DOWNGRADE / REROUTE / REVIEW) with calibrated confidence. The model judges
the most important claims; deterministic guard-rails always apply (a failed check can never be ACCEPTed; a refuted
model claim is REROUTEd while reroutes < 2, else REVIEW); remaining claims get deterministic verdicts."""
from __future__ import annotations

from pathlib import Path

from app.agent.nodes import out
from app.agent.nodes.critic import KIND_RANK
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, route_since, save_pseudo
from app.agent.state import MAX_REROUTES, JudgeOutput, RunState, Verdict

PROMPT = Path(__file__).resolve().parents[3] / "prompts" / "judge.md"
MAX_JUDGED = 30


def deterministic(c, refuted: bool, can_reroute: bool) -> Verdict:
    failed = [ch for ch in c.checks if not ch.passed]
    if any(ch.name == "source_confidence" for ch in failed):
        return Verdict(claim_id=c.id, verdict="REVIEW", confidence=0.3, reason="low source confidence")
    if refuted:
        if can_reroute and c.producer.kind == "model":
            return Verdict(claim_id=c.id, verdict="REROUTE", confidence=0.3, reason="critic challenge succeeded")
        return Verdict(claim_id=c.id, verdict="REVIEW", confidence=0.35, reason="critic challenge succeeded")
    if failed:
        if can_reroute and c.producer.kind == "model" and any(ch.name == "rerun_sql" for ch in failed):
            return Verdict(claim_id=c.id, verdict="REROUTE", confidence=0.3, reason="recomputation disagrees")
        return Verdict(claim_id=c.id, verdict="DOWNGRADE", confidence=min(0.5, c.confidence),
                       reason="failed: " + ", ".join(ch.name for ch in failed)[:80])
    if not c.checks:
        return Verdict(claim_id=c.id, verdict="DOWNGRADE", confidence=min(0.6, c.confidence), reason="no check available")
    return Verdict(claim_id=c.id, verdict="ACCEPT", confidence=round(min(0.9, c.confidence + 0.05), 3),
                   reason="deterministic checks passed")


async def judge(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    p = pseudo_for(st)
    em = Emitter(st, "judge", p)
    can_reroute = st.reroutes < MAX_REROUTES
    refuted = {c.claim_id for c in st.critic if c.result.get("refuted") and not c.result.get("superseded")}
    pending = [c for c in st.claims if c.id not in st.verdicts]
    det = {c.id: deterministic(c, c.id in refuted, can_reroute) for c in pending}
    ranked = sorted(pending, key=lambda c: (c.id not in refuted, all(ch.passed for ch in c.checks),
                                            KIND_RANK.get(c.kind, 5), c.id))[:MAX_JUDGED]
    model_id, judge_route = None, []
    if ranked:
        crit = {c.claim_id: c for c in st.critic if not c.result.get("superseded")}
        payload = {"system": PROMPT.read_text(encoding="utf-8"), "prompt": f"User request: {p.text(st.request)}",
                   "claims": p.pseudonymise([{
                       "id": c.id, "kind": c.kind, "subject": c.subject, "field": c.field, "value": c.value,
                       "producer": c.producer.kind, "prior_confidence": round(c.confidence, 2),
                       "checks": [{"name": ch.name, "passed": ch.passed, "detail": ch.detail[:100]} for ch in c.checks],
                       "critic": ({"hypothesis": crit[c.id].hypothesis[:200], **crit[c.id].result} if c.id in crit else None)}
                       for c in ranked]), "max_tokens": 2500}
        obj, res, logs = await llm_json(em, "judge", payload, JudgeOutput, privacy_tier="PSEUDO", run_id=st.run_id,
                                        step_id="judge")
        n_route = len(st.route_trace)
        account(st, logs, "judge")
        judge_route = route_since(st, n_route)
        if obj is not None:
            model_id = res.model_id
            for j in obj.verdicts:
                if j.claim_id not in det:
                    continue
                d = det[j.claim_id]
                v = Verdict(claim_id=j.claim_id, verdict=j.verdict, confidence=j.confidence, reason=j.reason[:200])
                # guard-rails: deterministic evidence wins over the model when they conflict
                if d.verdict in ("REROUTE", "REVIEW", "DOWNGRADE") and v.verdict == "ACCEPT":
                    v = d
                if v.verdict == "REROUTE" and (not can_reroute or next(c for c in pending if c.id == j.claim_id)
                                               .producer.kind != "model"):
                    v = Verdict(claim_id=v.claim_id, verdict="REVIEW" if j.claim_id in refuted else "DOWNGRADE",
                                confidence=min(v.confidence, 0.5), reason=v.reason + " (reroute not possible)")
                if d.verdict == "ACCEPT":
                    v.confidence = round(min(v.confidence, d.confidence + 0.05), 3)
                det[j.claim_id] = v
        else:
            for k, v in det.items():            # router terminal: deterministic_only_downgrade
                if v.verdict == "ACCEPT":
                    det[k] = Verdict(claim_id=k, verdict="DOWNGRADE", confidence=min(v.confidence, 0.6),
                                     reason="judge unavailable: deterministic checks only")
    status = {"ACCEPT": "verified", "DOWNGRADE": "downgraded", "REVIEW": "review", "REROUTE": "review"}
    reroute = set()
    for c in st.claims:
        if c.id in det:
            v = det[c.id]
            c.verdict, c.confidence, c.status = v.verdict, v.confidence, status[v.verdict]
            if v.verdict == "REROUTE":
                reroute.add(c.step_id)
            else:
                st.verdicts[c.id] = v
    em.emit("judge", {"verdicts": [det[c.id].model_dump() for c in pending][:80], "model_id": model_id,
                      "n_claims": len(pending), "route": judge_route})
    if reroute and can_reroute:
        st.reroute_steps = sorted(reroute)
        st.next_node = "recover"
    else:
        st.next_node = "present"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)
