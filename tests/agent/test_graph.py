"""End-to-end graph runs on the fixture KG with scripted providers (offline, deterministic)."""
import json
from pathlib import Path

import pytest

from app.agent.state import WorkspaceSpec
from tests.agent.conftest import PLOTS, result_of, resume_events, run_events


def _types(evs):
    return [e["type"] for e in evs]


def test_end_to_end_valid_workspace_and_ledger(fake):
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="g1")
    r = result_of(evs)
    assert r is not None, _types(evs)
    ws = WorkspaceSpec.model_validate(r["workspace"])
    assert ws.kpis and ws.tables and ws.map.layers
    assert all(k.claim_id for k in ws.kpis)
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    for t in ("plan", "step_start", "step_end", "verify", "critic", "judge", "result", "ledger"):
        assert t in _types(evs), t
    from app.ledger import get_ledger
    rep = get_ledger().verify("g1")
    assert rep.ok and rep.entries_checked == len(evs)
    res_seq = next(e["seq"] for e in evs if e["type"] == "result")
    prev = next(e for e in get_ledger().entries("g1") if e.seq == res_seq - 1)
    assert r["state"]["ledger_head"] == prev.hash == r["workspace"]["ledger_head"]   # head before the result


def test_narrative_numbers_reference_claims(fake):
    r = result_of(run_events("Which parcels in Ramasamypuram are stalled?", run_id="g2"))
    n = r["state"]["narrative"]
    assert n["claim_refs"] and all(f"[{c}]" in n["en"] for c in n["claim_refs"])


def test_presenter_new_number_falls_back_to_template(fake):
    import json as _j
    fake.present = lambda req, ctx: _j.dumps({"en": "There are 987654 parcels [c_s1_0].", "ta": "", "claim_refs": []})
    evs = run_events("Which parcels in Ramasamypuram are stalled?", run_id="g3")
    r = result_of(evs)
    assert "987654" not in r["state"]["narrative"]["en"]
    assert any(e["type"] == "fallback" and e["data"].get("to") == "template narrative" for e in evs)


def test_clarify_then_resume(fake):
    evs = run_events("What is the status of survey 233?", run_id="g4")
    assert evs[-1]["type"] == "clarify" if evs[-1]["type"] != "ledger" else evs[-2]["type"] == "clarify"
    evs2 = resume_events("g4", "Melathattaparai")
    r = result_of(evs2)
    assert r and r["state"]["slots"]["parcel_uids"] == ["Melathattaparai|233"]


def test_agri_pack_same_graph(fake):
    evs = run_events("Crop presence per season 2022-2024 and fallow streaks of at least 2 seasons for these plots",
                     attachments=[{"id": "a1", "name": "plots.geojson", "path": PLOTS}], run_id="g5")
    r = result_of(evs)
    assert r["state"]["domain"] == "agri_claims"
    tools = {s["tool"] for s in r["state"]["steps"].values()}
    assert tools <= {"crop_presence", "fallow_streak", "satellite_timeseries", "satellite_chip", "spatial_query"}
    assert any(l["id"] == "uploads" for l in r["workspace"]["map"]["layers"])


def test_route_trace_deterministic(fake, agent_env):
    from eval.agent.fakes import install

    def trace(run_id):
        install(fake, agent_env / f"router_{run_id}")     # same registry + fresh quota/telemetry snapshot
        r = result_of(run_events("Which parcels in Ramasamypuram are stalled?", run_id=run_id))
        return [(t["step_id"], t["task"], t["choice"]) for t in r["state"]["route_trace"]]
    assert trace("d1") == trace("d2")


def test_no_owner_names_in_pseudo_payloads(fake, kg_dsn):
    """Owner names from the KG never reach PSEUDO-tier roles (planner, critic, judge, presenter) or the ledger."""
    from app.agent.runtime import known_names
    from app.router.gateway import residual_names
    fake.sql = lambda req, ctx: json.dumps({"sql": (
        "SELECT f.value_text AS owner, f.parcel_uid, a.amount_rs FROM parcel_fact f JOIN (SELECT parcel_uid, "
        "value_num AS amount_rs FROM parcel_fact WHERE fact_type='amount_rs') a USING (parcel_uid) "
        "WHERE f.fact_type='owner' AND f.village='Ramasamypuram' ORDER BY 2 LIMIT 5")})
    fake.plan = lambda req, ctx: json.dumps({"goal": "owners", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": ctx["_text"][:200]}}], "outputs": ["table"]})
    evs = run_events("Who owns the Ramasamypuram parcels, e.g. Thiru. Arumugam S/o Ganesan, and how much were "
                     "they paid?", run_id="pii1")
    assert result_of(evs)
    names = [n for n in known_names(kg_dsn) if len(n) > 3] + ["Arumugam", "Ganesan"]
    pseudo_msgs = [m for m in fake.log if m["role"] in ("plan", "critic", "judge", "present")]
    assert pseudo_msgs
    assert residual_names([m["messages"] for m in pseudo_msgs], names) == []
    from app.ledger import get_ledger
    assert residual_names([e.payload for e in get_ledger().entries("pii1")], names) == []


@pytest.mark.db
def test_postgres_checkpointer_roundtrip(fake, monkeypatch):
    monkeypatch.delenv("AGENT_CHECKPOINTER", raising=False)
    from app.agent import runtime
    from app.agent.api import get_run_state
    r = result_of(run_events("Which parcels in Ramasamypuram are stalled?", run_id="pg-ckpt-test"))
    assert "checkpointer" not in r          # postgres used
    runtime.LATEST_STATE.clear()
    import os
    p = Path(os.environ["AGENT_DATA_DIR"]) / "runs" / "pg-ckpt-test" / "state.json"
    p.unlink()
    st = get_run_state("pg-ckpt-test")
    assert st and st["status"] == "done"


def test_graph_code_is_domain_agnostic():
    words = ["acquisition", "award", "possession", "patta", "survey", "village", "parcel_fact", "crop", "fallow",
             "sql_query", "lifecycle", "paper_vs_planet", "gazette", "sipcot"]
    root = Path(__file__).resolve().parents[2] / "app" / "agent"
    files = list((root / "nodes").glob("*.py")) + [root / n for n in ("graph.py", "runtime.py", "api.py", "store.py")]
    for f in files:
        txt = f.read_text(encoding="utf-8").lower()
        hits = [w for w in words if w in txt]
        assert not hits, f"{f.name} mentions domain vocabulary {hits}"


def test_agri_pack_has_no_acquisition_vocabulary():
    root = Path(__file__).resolve().parents[2] / "app" / "domains" / "agri_claims"
    for f in root.rglob("*"):
        if f.suffix in (".py", ".md"):
            txt = f.read_text(encoding="utf-8").lower()
            for w in ("acquisition", "award", "possession", "patta", "gazette", "compensation", "sipcot"):
                assert w not in txt, f"{f.name}: {w}"


def test_events_stream_route_log_live(fake):
    """v1.1: step_end / critic / judge carry the router route (why this model) before the terminal result."""
    import json

    from tests.agent.conftest import run_events
    fake.plan = lambda req, ctx: json.dumps({"goal": "g", "steps": [
        {"id": "s1", "tool": "sql_query", "args": {"question": "How many parcels?"}}], "outputs": ["kpis"]})
    evs = run_events("How many parcels are in Ramasamypuram?", run_id="troute")
    ends = [e["data"] for e in evs if e["type"] == "step_end"]
    assert ends and all("route" in d for d in ends)
    llm = [r for d in ends for r in d["route"]]
    assert llm and all({"candidates", "filtered", "scores", "choice", "attempts", "latency_ms", "tokens_in",
                        "shadow_cost_usd", "actual_cost_usd", "privacy_tier"} <= set(r) for r in llm)
    assert any(r["scores"] for r in llm)
    j = next(e["data"] for e in evs if e["type"] == "judge")
    assert "route" in j and "verdicts" in j
    crit = [e["data"] for e in evs if e["type"] == "critic"]
    assert crit and all("route" in c for c in crit)
