"""Planner schema / repair / slot-fill, plan-time arg checks, reroute cap, telemetry (offline, scripted providers)."""
import json

from app.agent.nodes.planner import check_plan, validate_plan
from app.agent.state import ExecutionPlan, PlannerOutput, Slots, planner_schema
from app.domains import get_pack
from tests.agent.conftest import PLOTS, result_of, run_events

LAND = get_pack("land_acquisition")
REG = LAND.registry()


def test_planner_schema_enum_is_the_pack_catalog():
    s = planner_schema([t["name"] for t in REG.catalog()])
    enum = s["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
    assert "compose_workspace" not in enum and "summarise" in enum and "lifecycle_status" in enum
    agri = get_pack("agri_claims").registry()
    enum2 = planner_schema([t["name"] for t in agri.catalog()])["properties"]["steps"]["items"]["properties"]["tool"]
    assert "lifecycle_status" not in enum2["enum"] and "crop_presence" in enum2["enum"]


def test_summarise_maps_to_llm_task():
    plan = PlannerOutput.model_validate({"goal": "g", "steps": [
        {"id": "s1", "tool": "lifecycle_status", "args": {}},
        {"id": "s2", "tool": "summarise", "args": {}, "depends_on": ["s1"], "llm_task": None}]}).to_plan()
    assert plan.steps[1].llm_task == "summarise" and plan.steps[1].tool is None
    assert validate_plan(plan, REG)[0] is not None


def test_required_args_filled_from_slots_never_overridden():
    slots = Slots(villages=["Melathattaparai"], parcel_uids=["Melathattaparai|233"])
    plan = ExecutionPlan.model_validate({"goal": "g", "steps": [
        {"id": "s1", "tool": "evidence_pack", "args": {}},
        {"id": "s2", "tool": "satellite_timeseries", "args": {}},
        {"id": "s3", "tool": "evidence_pack", "args": {"parcel_uid": "Melathattaparai|172"}}]})
    ok, errs, filled = check_plan(plan, REG, slots)
    assert not errs and filled == ["s1.parcel_uid", "s2.parcel_uids"]
    assert ok.steps[0].args["parcel_uid"] == "Melathattaparai|233"
    assert ok.steps[1].args["parcel_uids"] == ["Melathattaparai|233"]
    assert ok.steps[2].args["parcel_uid"] == "Melathattaparai|172"
    # ambiguous (2 parcels) -> the singular arg is NOT guessed; the plan stays invalid (-> repair)
    ok2, errs2, _ = check_plan(plan, REG, Slots(parcel_uids=["A|1", "A|2"]))
    assert ok2 is None and any("parcel_uid" in e and "required" in e for e in errs2)


def test_spatial_query_template_args_checked_at_plan_time():
    for args, needle in [({"layer": "substations", "distance_m": 2000}, "template"),
                         ({"template": "buffer_within", "layer": "substations"}, "distance_m"),
                         ({"template": "distance_to_nearest"}, "layer"),
                         ({"template": "intersects_layer", "layer": "rivers"}, "unknown layer")]:
        plan = ExecutionPlan.model_validate({"goal": "g", "steps": [{"id": "s1", "tool": "spatial_query",
                                                                     "args": args}]})
        ok, errs = validate_plan(plan, REG)
        assert ok is None and any(needle in e for e in errs), (args, errs)


def test_extra_arg_error_lists_allowed_args():
    plan = ExecutionPlan.model_validate({"goal": "g", "steps": [
        {"id": "s1", "tool": "lifecycle_status", "args": {"stages": ["POSSESSION"]}}]})
    _, errs = validate_plan(plan, REG)
    assert errs and "accepts only" in errs[0] and "only_stalled" in errs[0]


def test_toolless_step_is_repaired_by_router_on_same_model(fake):
    """A plan with a step lacking `tool` fails the schema; the router repairs once on the same model."""
    calls = {"n": 0}

    def plan(req, ctx):
        calls["n"] += 1
        steps = [{"id": "s1", "tool": "lifecycle_status", "args": {"villages": ["Ramasamypuram"]}}]
        if calls["n"] == 1:
            steps.append({"id": "s2", "tool": None, "llm_task": None, "args": {}})     # seen live
        return json.dumps({"goal": "g", "steps": steps, "outputs": ["table"]})
    fake.plan = plan
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="tp1")
    r = result_of(evs)
    pm = r["state"]["plan_meta"]
    assert calls["n"] == 2 and pm["repaired"] is True and pm["valid_first"] is False and pm["template"] is False
    assert pm["model_id"] == "gemini-flash-lite"
    tr = next(t for t in r["state"]["route_trace"] if t["task"] == "plan")
    assert [a["outcome"] for a in tr["attempts"]] == ["ok", "schema_invalid", "repair_ok"]


def test_slot_fill_recorded_in_plan_event(fake):
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "evidence_pack", "args": {}}], "outputs": ["timeline"]})
    evs = run_events("Verify Melathattaparai survey 233: documents vs satellite", run_id="tp2")
    plan_ev = next(e for e in evs if e["type"] == "plan")
    assert plan_ev["data"]["slot_filled"] == ["s1.parcel_uid"]
    r = result_of(evs)
    assert r["state"]["steps"]["s1"]["status"] == "ok" and r["state"]["plan_meta"]["valid_first"] is True
    assert r["state"]["plan_meta"]["first_call_valid"] is False


def test_fallow_streak_uses_slot_polygons(fake):
    """Regression: fallow_streak without polygons_ref fell over (min_seasons rejected by PlotsIn)."""
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "fallow_streak", "args": {"min_seasons": 2}}], "outputs": ["table"]})
    evs = run_events("Fallow streaks of at least 2 seasons for these plots", run_id="tp3",
                     attachments=[{"id": "a1", "name": "plots.geojson", "path": PLOTS}])
    r = result_of(evs)
    assert r["state"]["steps"]["s1"]["status"] == "ok", r["state"]["steps"]["s1"]


def test_reroute_capped_at_two_then_review(fake, monkeypatch):
    """A claim the critic refutes every time: 2 reroutes (each excluding the producing model), then REVIEW."""
    monkeypatch.setenv("AGENT_EXCLUDE_MODELS", "")        # full sql chain (3 fake models) for the cap test
    wrong = "SELECT count(*) AS n_parcels FROM parcel"
    right = ("SELECT count(*) AS n_parcels FROM parcel p JOIN village v ON v.id = p.village_id "
             "WHERE v.name = 'Ramasamypuram'")
    fake.sql = lambda req, ctx: json.dumps({"sql": right if "whose village" in ctx["_text"] else wrong})
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": "How many parcels are in Ramasamypuram?"}}],
        "outputs": ["kpis"]})
    fake.critic = lambda req, ctx: json.dumps({"challenges": [
        {"claim_id": c["id"], "hypothesis": "ignores the village filter",
         "check": {"tool": "sql_query", "args": {"question": "Count parcels whose village is Ramasamypuram"}}}
        for c in ctx["claims"]][:1]})
    evs = run_events("How many parcels are in Ramasamypuram?", run_id="tp4")
    st = result_of(evs)["state"]
    assert st["reroutes"] == 2
    assert st["verdicts"]["c_s1_0"]["verdict"] == "REVIEW"
    assert len(st["steps"]["s1"]["excluded"]) == 2
    refuted = [c for c in st["critic"] if c["result"].get("refuted")]
    assert len(refuted) == 3 and all(c["vendor"] != c["producer_vendor"] for c in st["critic"])
    ws = result_of(evs)["workspace"]
    kpi = next(k for k in ws["kpis"] if k["claim_id"] == "c_s1_0")
    assert kpi["status"] == "review"                      # visibly downgraded in the UI


def test_telemetry_and_shadow_cost_add_up(fake):
    r = result_of(run_events("Which parcels in Ramasamypuram are stalled?", run_id="tp5"))
    st = r["state"]
    tel, tr = st["telemetry"], st["route_trace"]
    assert tel["llm_calls"] == len(tr) and tel["tool_calls"] >= 1
    assert tel["tokens_in"] > 0 and tel["shadow_cost_usd"] > 0
    assert abs(tel["shadow_cost_usd"] - sum(t["shadow_cost_usd"] for t in tr)) < 1e-5
    assert tel["actual_cost_usd"] == 0                    # free tiers only in this run
    assert {t["task"] for t in tr} >= {"plan", "judge", "present"}
    for t in tr:
        assert t["reason"] and t["candidates"]


def test_critic_call_without_producer_vendor_fails_closed(fake):
    """D-017: the router refuses a critic call that does not name the producer's vendor."""
    import pytest

    from app.router import RouterConfigError, get_router
    with pytest.raises(RouterConfigError):
        get_router().call("critic", {"prompt": "challenge"}, privacy_tier="PSEUDO")
