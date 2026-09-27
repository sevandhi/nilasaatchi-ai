"""Forced-failure suite (plan §5.8): 429, bad JSON, SQL error, low OCR, critic success -> reroute, quota."""
import json

from app.router import chaos
from tests.agent.conftest import SCANNED, result_of, run_events


def _fallbacks(evs):
    return [e["data"] for e in evs if e["type"] == "fallback"]


def _plan_model(evs):
    return next(e["data"]["model_id"] for e in evs if e["type"] == "plan")


def test_429_falls_back(fake):
    chaos.set_chaos("gemini:429")
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="c429")
    assert result_of(evs)
    assert _plan_model(evs) == "groq-qwen-vl"
    assert any(f.get("reason") == "rate_limit" and f["from"] == "gemini-flash-lite" for f in _fallbacks(evs))


def test_bad_json_repair_then_alternate(fake):
    fake.per_model["gemini-flash-lite:plan"] = lambda req, ctx: '{"goal": "x", "steps": [ truncated'
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="cjson")
    r = result_of(evs)
    assert r and _plan_model(evs) == "groq-qwen-vl"
    tr = next(t for t in r["state"]["route_trace"] if t["task"] == "plan")
    outcomes = [a["outcome"] for a in tr["attempts"]]
    models = [a["model_id"] for a in tr["attempts"]]
    assert "schema_invalid" in outcomes and "schema_invalid_after_repair" in outcomes
    assert outcomes[-1] == "ok" and models[-1] == "groq-qwen-vl"
    assert set(models[:-1]) == {"gemini-flash-lite"}          # repaired once on the same model, then fell back


def test_invalid_plan_repair_once(fake):
    calls = {"n": 0}

    def plan(req, ctx):
        calls["n"] += 1
        tool = "no_such_tool" if calls["n"] == 1 else "lifecycle_status"
        return json.dumps({"goal": "g", "steps": [{"id": "s1", "tool": tool, "args": {}}], "outputs": []})
    fake.plan = plan
    r = result_of(run_events("Which parcels in Ramasamypuram are stalled?", run_id="crep"))
    assert r["state"]["plan_meta"]["repaired"] is True and calls["n"] == 2


def test_sql_error_repair(fake):
    calls = {"n": 0}

    def sql(req, ctx):
        calls["n"] += 1
        q = "SELECT count(*) AS n FROM parcel WHERE nonexistent_col = 1" if calls["n"] == 1 else \
            "SELECT count(*) AS n_parcels FROM parcel p JOIN village v ON v.id=p.village_id WHERE v.name='Ramasamypuram'"
        return json.dumps({"sql": q})
    fake.sql = sql
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": "How many parcels in Ramasamypuram?"}}], "outputs": []})
    evs = run_events("How many parcels are in Ramasamypuram?", run_id="csql")
    r = result_of(evs)
    s1 = r["state"]["steps"]["s1"]
    assert s1["status"] == "ok" and "1 repair" in s1["summary"]
    assert any(f.get("reason") == "sql_error" for f in _fallbacks(evs))
    kpi = next(k for k in r["workspace"]["kpis"] if "n parcels" in k["label"])
    assert kpi["value"] == 20 and kpi["status"] == "verified"


def test_low_ocr_goes_to_review(fake):
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "extract_document", "args": {"attachment_id": "a1"}}], "outputs": ["table"]})
    evs = run_events("Extract this award and match it to parcels", run_id="cocr",
                     attachments=[{"id": "a1", "name": "scan.pdf", "path": SCANNED}])
    r = result_of(evs)
    assert any("review queue" in str(f.get("to")) for f in _fallbacks(evs))
    assert {v["verdict"] for v in r["state"]["verdicts"].values()} == {"REVIEW"}


def test_critic_success_reroutes_to_alternate_model(fake):
    wrong = "SELECT count(*) AS n_parcels FROM parcel"                     # forgets the village filter (40)
    right = ("SELECT count(*) AS n_parcels FROM parcel p JOIN village v ON v.id = p.village_id "
             "WHERE v.name = 'Ramasamypuram'")                             # 20
    fake.per_model["groq-qwen-vl:sql"] = lambda req, ctx: json.dumps({"sql": wrong})
    fake.sql = lambda req, ctx: json.dumps({"sql": right})
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": "How many parcels are in Ramasamypuram?"}}],
        "outputs": ["kpis"]})
    fake.critic = lambda req, ctx: json.dumps({"challenges": [
        {"claim_id": c["id"], "hypothesis": "the count ignores the village filter",
         "check": {"tool": "sql_query", "args": {"question": "Count parcels whose village is Ramasamypuram"}}}
        for c in ctx["claims"]][:1]})
    evs = run_events("How many parcels are in Ramasamypuram?", run_id="ccrit")
    r = result_of(evs)
    st = r["state"]
    assert st["reroutes"] == 1
    crit = st["critic"]
    assert crit[0]["result"]["refuted"] is True and crit[0]["vendor"] != crit[0]["producer_vendor"]
    assert crit[-1]["result"]["refuted"] is False
    v = st["verdicts"]["c_s1_0"]
    assert v["verdict"] == "ACCEPT"
    assert st["steps"]["s1"]["model_id"] != "groq-qwen-vl" and "groq-qwen-vl" in st["steps"]["s1"]["excluded"]
    assert next(c for c in st["claims"] if c["id"] == "c_s1_0")["value"] == 20
    assert any(f.get("reason", "").startswith("critic_refuted") for f in _fallbacks(evs))


def test_critic_vendor_differs_from_producer(fake):
    fake.critic = lambda req, ctx: json.dumps({"challenges": []})
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": "How many parcels?"}}], "outputs": []})
    run_events("How many parcels are there?", run_id="cvend")
    critic_models = {m["model_id"] for m in fake.log if m["role"] == "critic"}
    sql_models = {m["model_id"] for m in fake.log if m["role"] == "sql"}
    from app.router import vendor_of
    assert critic_models and not ({vendor_of(m) for m in critic_models} & {vendor_of(m) for m in sql_models})


def test_quota_exhausted_uses_local_model(fake, monkeypatch):
    from app.router import get_router
    from app.router.quota import QuotaCheck
    q = get_router().quota
    orig = q.check

    def check(model_id, *a, **kw):
        if model_id != "local-qwen":
            return QuotaCheck(ok=False, reason="quota exhausted (test)", pressure=1.0)
        return orig(model_id, *a, **kw)
    monkeypatch.setattr(q, "check", check)
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="cquota")
    assert result_of(evs) and _plan_model(evs) == "local-qwen"


def test_all_planners_down_uses_default_plan(fake):
    chaos.set_chaos("*:down")
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="cdown")
    r = result_of(evs)
    assert r and r["state"]["plan_meta"]["template"] is True and r["state"]["degraded"] is True
    assert {v["verdict"] for v in r["state"]["verdicts"].values()} <= {"DOWNGRADE", "REVIEW"}


def test_env_chaos_gemini_down_planner_falls_back(fake, monkeypatch):
    """ROUTER_CHAOS=gemini:down (env, as in `make eval-agent CHAOS=gemini:down`): planner reroutes off Gemini."""
    chaos.set_chaos(None)
    monkeypatch.setenv("ROUTER_CHAOS", "gemini:down")
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="cenvdown")
    assert result_of(evs)
    assert _plan_model(evs) == "groq-qwen-vl"
    assert any(f["from"] == "gemini-flash-lite" for f in _fallbacks(evs))
    assert not any(m["model_id"].startswith("gemini") for m in fake.log)


def test_tool_claims_critic_excludes_planner_vendor(fake):
    """Tool-produced claims were parameterised by the planner (Gemini): the critic must be another vendor."""
    from app.router import vendor_of
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="cplanv")
    assert _plan_model(evs) == "gemini-flash-lite"
    crit = [m["model_id"] for m in fake.log if m["role"] == "critic"]
    assert crit and all(vendor_of(m) != "google" for m in crit)
