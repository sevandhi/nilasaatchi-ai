"""planner: request + slots + tool catalog -> validated ExecutionPlan (repair once -> alternate model ->
the pack's deterministic default plan).

The model answers against a per-pack JSON schema (``planner_schema``: `tool` is an enum of the pack's tools), so the
router's schema validation + single repair catches unknown / missing tools. Semantic validation (args vs the tool's
input model, dependencies, acyclicity) follows; required args the model left out are filled from same-named slots
(recorded in ``plan_meta.slot_filled``), never overriding a value the model wrote.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from app.agent.nodes import out
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, save_pseudo
from app.agent.state import (
    LLM_TASKS,
    ExecutionPlan,
    PlannerOutput,
    PlanStep,
    RunState,
    Slots,
    StepRecord,
    planner_schema,
)
from app.domains import get_pack
from app.domains.common_slots import SCOPE_SLOTS
from app.tools.registry import ToolError

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
REF = re.compile(r"^\$\{?([A-Za-z][A-Za-z0-9_]*)[.\[]")
OK_OUTCOMES = ("ok", "repair_ok", "batch_cache_ok", "repair_batch_cache_ok")
ANSWERED = OK_OUTCOMES + ("schema_invalid", "schema_invalid_after_repair")


def _refs(v) -> set[str]:
    if isinstance(v, str):
        m = REF.match(v)
        return {m.group(1)} if m else set()
    if isinstance(v, dict):
        return set().union(*[_refs(x) for x in v.values()]) if v else set()
    if isinstance(v, list):
        return set().union(*[_refs(x) for x in v]) if v else set()
    return set()


def _as_plan(plan: PlannerOutput | ExecutionPlan) -> ExecutionPlan:
    return plan.to_plan() if isinstance(plan, PlannerOutput) else plan


def fill_from_slots(plan: ExecutionPlan, registry, slots: Slots | None) -> tuple[ExecutionPlan, list[str]]:
    """Fill REQUIRED tool args the model omitted, and optional scope args (SCOPE_SLOTS) left empty,
     from the same-named slot (``parcel_uids`` -> ``parcel_uids``;
    a singular arg ``parcel_uid`` <- the only element of ``parcel_uids``). Generic over tools and domains."""
    if slots is None:
        return plan, []
    sd = slots.model_dump()
    sd.update({k: v for k, v in (slots.extra or {}).items() if k not in sd})
    notes: list[str] = []
    steps = []
    for s in plan.steps:
        s = s.model_copy(deep=True)
        if s.tool and s.tool in registry:
            fields = registry.get(s.tool).input_model.model_fields
            for name, f in fields.items():
                if name in SCOPE_SLOTS and not f.is_required() and s.args.get(name) in (None, []) \
                        and sd.get(name) not in (None, []):
                    s.args[name] = sd[name]          # optional scope arg omitted (some models emit args={})
                    notes.append(f"{s.id}.{name}")
                    continue
                if not f.is_required() or name in s.args:
                    continue
                v = sd.get(name)
                if v in (None, [], {}, ""):
                    many = sd.get(name + "s")
                    v = many[0] if isinstance(many, list) and len(many) == 1 else None
                if v not in (None, [], {}, ""):
                    s.args[name] = v
                    notes.append(f"{s.id}.{name}")
        steps.append(s)
    return plan.model_copy(update={"steps": steps}), notes


def validate_plan(plan: PlannerOutput | ExecutionPlan, registry) -> tuple[ExecutionPlan | None, list[str]]:
    """Semantic validation: unique ids, known tools, args valid (reference-valued args excepted), deps exist,
    references covered by depends_on (auto-added), acyclic."""
    plan = _as_plan(plan)
    errs: list[str] = []
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids):
        errs.append("duplicate step ids")
    steps = []
    for s in plan.steps:
        s = s.model_copy(deep=True)
        if s.tool:
            if s.tool not in registry or not registry.get(s.tool).planner_visible:
                errs.append(f"{s.id}: unknown tool {s.tool!r}")
            else:
                ref_keys = {k for k, v in s.args.items() if _refs(v)}
                try:
                    registry.validate_args(s.tool, {k: v for k, v in s.args.items() if k not in ref_keys})
                except ToolError as e:
                    bad = [m for m in re.findall(r"'loc': \('([^']+)'", str(e)) if m not in ref_keys]
                    if bad or not ref_keys:
                        fields = registry.get(s.tool).input_model.model_fields
                        req = [n for n, f in fields.items() if f.is_required()]
                        errs.append(f"{s.id}: {str(e)[:300]} -- {s.tool} accepts only {sorted(fields)}; "
                                    f"required: {req}")
        elif s.llm_task not in LLM_TASKS:
            errs.append(f"{s.id}: llm_task must be one of {sorted(LLM_TASKS)}")
        for d in s.depends_on:
            if d not in ids:
                errs.append(f"{s.id}: depends_on unknown step {d}")
        for r in _refs(s.args):
            if r not in ids:
                errs.append(f"{s.id}: argument references unknown step {r}")
            elif r not in s.depends_on:
                s.depends_on.append(r)
        steps.append(s)
    graph = {s.id: set(s.depends_on) for s in steps}
    seen, stack = set(), set()

    def cyc(n) -> bool:
        if n in stack:
            return True
        if n in seen:
            return False
        stack.add(n)
        r = any(cyc(d) for d in graph.get(n, ()) if d in graph)
        stack.discard(n)
        seen.add(n)
        return r
    if any(cyc(n) for n in graph):
        errs.append("dependency cycle")
    if errs:
        return None, errs
    return ExecutionPlan(goal=plan.goal, steps=steps, outputs=plan.outputs), []


def check_plan(obj: PlannerOutput | ExecutionPlan, registry, slots: Slots | None):
    plan, filled = fill_from_slots(_as_plan(obj), registry, slots)
    ok, errs = validate_plan(plan, registry)
    return ok, errs, filled


def plan_schema(registry) -> dict:
    return planner_schema([t["name"] for t in registry.catalog()])


def planner_payload(st: RunState, pack, registry, pseudo, extra: str = "") -> dict:
    system = (PROMPTS / "planner.md").read_text(encoding="utf-8") + "\n" + pack.prompt("planner") + \
        "\nGlossary: " + pack.glossary
    ctx = {"slots": st.slots.model_dump(exclude={"polygons_ref"}) | (
        {"polygons_ref": st.slots.polygons_ref} if st.slots.polygons_ref else {}),
        "assumptions": st.assumptions, "tools": registry.catalog()}
    if st.base_workspace:
        ctx["current_workspace"] = {"title": st.base_workspace.get("title"),
                                    "tables": [t.get("title") for t in st.base_workspace.get("tables", [])]}
    return {"system": system, "prompt": f"Request: {pseudo.text(st.request)}{extra}", **ctx, "max_tokens": 1500}


def answer_stats(route_log: dict | None, chosen: str | None) -> dict:
    """From the router's attempt log: the first model that actually answered (errors / 429 / skips are
    availability, not plan quality), whether the chosen model needed the router's JSON repair."""
    atts = (route_log or {}).get("attempts", [])
    first = next((a["model_id"] for a in atts if a.get("outcome") in ANSWERED), None)
    mine = [a.get("outcome") for a in atts if a.get("model_id") == chosen]
    return {"first_answering": first, "router_repaired": any(o and o.startswith("repair") for o in mine)}


async def planner(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    pack = get_pack(st.domain)
    reg = pack.registry()
    schema = plan_schema(reg)
    p = pseudo_for(st)
    em = Emitter(st, "planner", p)
    payload = planner_payload(st, pack, reg, p)
    meta = {"valid_first": False, "first_call_valid": False, "repaired": False, "repairs": 0,
            "fallback_model": False, "template": False, "errors": [], "slot_filled": []}
    plan, model_id = None, None
    obj, res, logs = await llm_json(em, "plan", payload, PlannerOutput, privacy_tier="PSEUDO", run_id=st.run_id,
                                    step_id="plan", schema=schema)
    account(st, logs, "plan")
    st.plan_attempts += 1
    tried: list[str] = [a["model_id"] for lg in logs for a in lg.get("attempts", [])]
    if obj is not None:
        model_id = res.model_id
        stats = answer_stats(res.route_log, model_id)
        on_first = stats["first_answering"] == model_id and len(logs) == 1
        meta["repairs"] = int(stats["router_repaired"])
        plan, errs, filled = check_plan(obj, reg, st.slots)
        meta["slot_filled"] = filled
        if plan is not None:
            meta["valid_first"] = on_first and not stats["router_repaired"]
            meta["first_call_valid"] = meta["valid_first"] and not filled
            meta["repaired"] = on_first and stats["router_repaired"]
            meta["fallback_model"] = not on_first
        else:                                              # semantic repair once on the same model
            meta["errors"] += errs
            fix = {**payload, "prompt": payload["prompt"] + "\n\nYour previous plan was invalid:\n- " +
                   "\n- ".join(errs[:6]) + "\nReturn a corrected plan."}
            obj2, res2, logs2 = await llm_json(em, "plan", fix, PlannerOutput, privacy_tier="PSEUDO",
                                               run_id=st.run_id, step_id="plan_repair", only_model=model_id,
                                               schema=schema)
            account(st, logs2, "plan_repair")
            st.plan_attempts += 1
            meta["repairs"] += 1
            if obj2 is not None:
                plan, errs2, filled = check_plan(obj2, reg, st.slots)
                meta["slot_filled"] = filled
                meta["repaired"] = plan is not None and on_first and meta["repairs"] <= 1
                meta["fallback_model"] = plan is not None and not on_first
                meta["errors"] += errs2
    if plan is None and tried:                             # alternate model
        em.emit("fallback", {"step_id": "plan", "task": "plan", "from": model_id or tried[-1],
                             "to": "alternate model", "reason": "invalid plan after repair"})
        obj3, res3, logs3 = await llm_json(em, "plan", payload, PlannerOutput, privacy_tier="PSEUDO",
                                           run_id=st.run_id, step_id="plan_fallback",
                                           exclude=sorted(set(tried + ([model_id] if model_id else []))),
                                           schema=schema)
        account(st, logs3, "plan_fallback")
        st.plan_attempts += 1
        if obj3 is not None:
            plan, errs3, filled = check_plan(obj3, reg, st.slots)
            meta["slot_filled"] = filled
            meta["fallback_model"] = plan is not None
            meta["errors"] += errs3
            model_id = res3.model_id if plan is not None else model_id
    if plan is None and pack.default_plan is not None:     # router terminal: deterministic plan
        try:
            plan, errs4, _ = check_plan(ExecutionPlan.model_validate(pack.default_plan(st.slots)), reg, st.slots)
        except ValidationError as e:
            plan, errs4 = None, [str(e)[:200]]
        meta["template"] = plan is not None
        meta["errors"] += errs4
        st.degraded = True
        em.emit("fallback", {"step_id": "plan", "task": "plan", "from": model_id or "planner models",
                             "to": "deterministic default plan", "reason": "no valid model plan"})
    meta["model_id"] = model_id
    st.plan_meta = meta
    if plan is None:
        st.status = "failed"
        st.errors.append({"node": "planner", "message": "no valid plan"})
        st.next_node = "end"
        em.emit("error", {"message": "could not build a valid plan", "node": "planner", "recoverable": False})
    else:
        st.plan = plan
        st.steps = {s.id: StepRecord(id=s.id, tool=s.tool, llm_task=s.llm_task, args=s.args,
                                     depends_on=s.depends_on, verify=s.verify) for s in plan.steps}
        st.next_node = "dispatch"
        em.emit("plan", {"plan": plan.model_dump(), "model_id": model_id, "attempt": st.plan_attempts,
                         "repaired": meta["repaired"], "template": meta["template"],
                         "slot_filled": meta["slot_filled"]})
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)


__all__ = ["PlanStep", "check_plan", "fill_from_slots", "plan_schema", "planner", "validate_plan"]
