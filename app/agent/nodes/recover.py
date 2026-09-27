"""recover: the plan §5.8 fallback matrix, generic over tools.

- judge REROUTE: re-run the claim's step with the producing model excluded (max 2 reroutes per run);
- provider/tool unavailable or timeout: retry the step once with the failing model excluded;
- SQL error after repairs / bad args / not found: one hard replan for the failed step (router `plan` task with
  hard_replan=True, which unlocks the stronger planner) -> template tools; else continue degraded."""
from __future__ import annotations

from app.agent.nodes import out
from app.agent.nodes.planner import check_plan, plan_schema, planner_payload
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, save_pseudo
from app.agent.state import MAX_REROUTES, PlannerOutput, RunState, StepRecord
from app.domains import get_pack

RETRY_KINDS = ("unavailable", "timeout", "failed")


def _rename_refs(v, rename: dict[str, str]):
    if isinstance(v, str) and v.startswith("$") and "." in v:
        sid, rest = v[1:].split(".", 1)
        return f"${rename.get(sid, sid)}.{rest}"
    if isinstance(v, dict):
        return {k: _rename_refs(x, rename) for k, x in v.items()}
    if isinstance(v, list):
        return [_rename_refs(x, rename) for x in v]
    return v


def _downstream(st: RunState, roots: set[str]) -> set[str]:
    res = set(roots)
    changed = True
    while changed:
        changed = False
        for sid, rec in st.steps.items():
            if sid not in res and set(rec.depends_on) & res:
                res.add(sid)
                changed = True
    return res


def _reset(st: RunState, sids: set[str]) -> None:
    for sid in sids:
        rec = st.steps[sid]
        rec.status, rec.output_ref, rec.summary, rec.error = "pending", None, "", None
    st.claims = [c for c in st.claims if c.step_id not in sids]
    keep = {c.id for c in st.claims}
    st.verdicts = {k: v for k, v in st.verdicts.items() if k in keep}
    for c in st.critic:                       # keep the history; superseded challenges no longer apply
        if c.claim_id not in keep or any(c.claim_id.startswith(f"c_{sid}_") for sid in sids):
            c.result = {**c.result, "superseded": True}


async def recover(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    pack = get_pack(st.domain)
    p = pseudo_for(st)
    em = Emitter(st, "recover", p)
    if st.reroute_steps:
        roots = set()
        for sid in st.reroute_steps:
            rec = st.steps.get(sid)
            if rec is None:
                continue
            bad = rec.model_id or rec.tool
            if rec.model_id and rec.model_id not in rec.excluded:
                rec.excluded.append(rec.model_id)
            em.emit("fallback", {"step_id": sid, "task": rec.tool, "from": bad, "to": "alternate model",
                                 "reason": "critic_refuted / check disagreed (reroute)"})
            roots.add(sid)
        _reset(st, _downstream(st, roots))
        st.reroutes += 1
        st.reroute_steps = []
        st.next_node = "dispatch"
    else:
        failed = [r for r in st.steps.values() if r.status == "failed"]
        replan_for: list[StepRecord] = []
        for rec in failed:
            kind = (rec.error or "").split(":")[0]
            if kind in RETRY_KINDS and len(rec.attempts) < 3:
                if rec.model_id and rec.model_id not in rec.excluded:
                    rec.excluded.append(rec.model_id)
                em.emit("fallback", {"step_id": rec.id, "from": rec.model_id or rec.tool, "to": "retry",
                                     "reason": kind})
                _reset(st, _downstream(st, {rec.id}))
            else:
                replan_for.append(rec)
        if replan_for and st.replans < 1:
            st.replans += 1
            reg = pack.registry()
            info = "; ".join(f"step {r.id} ({r.tool} {r.args}) failed: {r.error}" for r in replan_for)
            payload = planner_payload(st, pack, reg, p, extra=f"\n\nThese steps failed: {info}\nPlan ONLY replacement "
                                      "steps (prefer parameterised templates / specific tools). Use new ids r1, r2, ...")
            payload["hard_replan"] = True
            obj, res, logs = await llm_json(em, "plan", payload, PlannerOutput, privacy_tier="PSEUDO",
                                            run_id=st.run_id, step_id="replan", schema=plan_schema(reg))
            account(st, logs, "replan")
            plan, errs, _ = (check_plan(obj, reg, st.slots) if obj else (None, ["no replan"], []))
            if plan is not None:
                existing = set(st.steps)
                rename: dict[str, str] = {}
                for s in plan.steps:
                    rename[s.id] = s.id if s.id not in existing else f"r{len(existing) + len(rename) + 1}"
                for s in plan.steps:
                    sid = rename[s.id]
                    deps = [rename.get(d, d) for d in s.depends_on if d in rename or
                            (d in st.steps and st.steps[d].status == "ok")]
                    args = _rename_refs(s.args, rename)
                    st.steps[sid] = StepRecord(id=sid, tool=s.tool, llm_task=s.llm_task, args=args,
                                               depends_on=deps, verify=s.verify)
                em.emit("fallback", {"step_id": ",".join(r.id for r in replan_for), "from": "failed steps",
                                     "to": [rename[s.id] for s in plan.steps],
                                     "reason": "replan after tool/SQL failure"})
            else:
                st.degraded = True
                st.missing_info = st.missing_info + []
                em.emit("fallback", {"step_id": ",".join(r.id for r in replan_for), "from": "replan",
                                     "to": "continue degraded", "reason": "; ".join(errs)[:200]})
        elif replan_for:
            st.degraded = True
        st.next_node = "dispatch" if any(r.status == "pending" for r in st.steps.values()) else "verify"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)


__all__ = ["MAX_REROUTES", "recover"]
