"""P4 agent eval: run the dev queries end-to-end headless on the fixture KG and score them.

    PYTHONPATH=. uv run python -m eval.agent.run_dev [--only d01,d02] [--mode live|record|replay] [--out FILE]

Metrics: WorkspaceSpec schema validity (Pydantic + exported JSON Schema), plan validity (a model plan valid first
time or after one repair), tool success (final and first-attempt), verified-claim rate, answer match against the
harness's reference SQL, PII audit (owner names in any PSEUDO/PUBLIC router payload), route distribution, cost.
Mode `record` writes VCR fixtures to tests/agent/fixtures/router so tests can replay without quota.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "agent" / "fixtures" / "router"


def _pii_tap(names: list[str], audit: list[dict]):
    from app.router.core import Router
    from app.router.gateway import residual_names
    orig = Router.call

    def call(self, task, payload, **kw):
        tier = kw.get("privacy_tier", "PUBLIC")
        if tier in ("PSEUDO", "PUBLIC"):
            hits = residual_names(payload, names)
            audit.append({"task": task, "tier": tier, "hits": hits})
        return orig(self, task, payload, **kw)
    Router.call = call


FIXED_TODAY = "2026-09-27"          # analysis date pinned for record/replay (day counts must replay identically)


def isolate(mode: str, clean: bool = True) -> Path:
    """Deterministic record/replay: pinned analysis date, a fresh router state dir (quota, telemetry p50, breaker)
    and the doctor snapshot stored next to the fixtures (the resolved model name is part of the request key)."""
    import shutil
    import tempfile
    fx = Path(os.environ.setdefault("ROUTER_FIXTURE_DIR", str(FIXTURES)))
    meta = fx.parent / "router_meta"
    if mode == "record":
        if fx.exists() and clean:
            shutil.rmtree(fx)                     # stale recordings never match again; start clean
        meta.mkdir(parents=True, exist_ok=True)
        doc = ROOT / "data" / "doctor.json"
        if doc.exists():
            shutil.copy(doc, meta / "doctor.json")
        (meta / "meta.json").write_text(json.dumps({"today": FIXED_TODAY, "recorded_at": time.strftime("%FT%T%z")}))
    os.environ.setdefault("AGENT_TODAY", json.loads((meta / "meta.json").read_text())["today"]
                          if (meta / "meta.json").exists() else FIXED_TODAY)
    os.environ.setdefault("AGENT_DATA_DIR", str(ROOT / "data"))
    iso = Path(tempfile.mkdtemp(prefix=f"agent_{mode}_router_"))
    if (meta / "doctor.json").exists():
        shutil.copy(meta / "doctor.json", iso / "doctor.json")
    os.environ["ROUTER_DATA_DIR"] = str(iso)
    os.environ["ROUTER_AWS_CE_REFRESH"] = "0"
    return iso


def _all_cells(ws: dict) -> list:
    vals = [k["value"] for k in ws.get("kpis", [])]
    for t in ws.get("tables", []):
        for r in t["rows"]:
            vals += list(r.values())
    return vals


def _num_in(v, cells) -> bool:
    for c in cells:
        try:
            if abs(float(c) - float(v)) <= max(0.005 * abs(float(v)), 1e-4):
                return True
        except (TypeError, ValueError):
            continue
    return False


def score(q: dict, evs: list[dict], kg: str) -> dict:
    from jsonschema import Draft202012Validator

    from app.agent.state import SCHEMA_DIR, WorkspaceSpec
    from app.tools.db import query
    res = next((e["data"] for e in evs if e["type"] == "result"), None)
    exp = q.get("expect", {})
    out = {"id": q["id"], "result": res is not None, "clarified": any(e["type"] == "clarify" for e in evs)}
    if res is None:
        out["error"] = next((e["data"].get("message") for e in evs if e["type"] == "error"), "no result")
        return out
    st, ws = res["state"], res["workspace"]
    try:
        WorkspaceSpec.model_validate(ws)
        errs = list(Draft202012Validator(json.loads((SCHEMA_DIR / "WorkspaceSpec.json").read_text())).iter_errors(ws))
        out["spec_valid"] = not errs
    except Exception as e:  # noqa: BLE001
        out["spec_valid"], out["spec_error"] = False, str(e)[:200]
    pm = st.get("plan_meta", {})
    out["plan_valid"] = bool(pm.get("valid_first") or pm.get("repaired"))
    out["plan_meta"] = {k: pm.get(k) for k in ("valid_first", "first_call_valid", "repaired", "fallback_model",
                                                "template", "model_id")}
    out["plan_errors"] = pm.get("errors", [])[:3]
    tool_steps = [s for s in st["steps"].values() if s.get("tool")]
    out["tool_steps"] = len(tool_steps)
    out["tool_ok"] = sum(1 for s in tool_steps if s["status"] == "ok")
    out["tool_first_ok"] = sum(1 for s in tool_steps if s["attempts"] and s["attempts"][0]["outcome"] == "ok")
    out["tools"] = [f"{s['tool']}:{s['status']}" for s in tool_steps]
    out["tool_errors"] = [s.get("error") for s in tool_steps if s["status"] != "ok"]
    v = Counter(x["verdict"] for x in st["verdicts"].values())
    out["verdicts"] = dict(v)
    out["claims"] = len(st["claims"])
    out["reroutes"] = st["reroutes"]
    out["domain_ok"] = st["domain"] == exp.get("domain", st["domain"])
    out["narrative_check"] = pm.get("narrative_check", {}).get("passed")
    cells = _all_cells(ws)
    match = []
    if exp.get("numbers_sql"):
        row = query(kg, exp["numbers_sql"], readonly_role=False)[0]
        match.append(all(_num_in(x, cells) for x in row.values()))
    if exp.get("parcels_sql"):
        uids = {r["parcel_uid"] for r in query(kg, exp["parcels_sql"], readonly_role=False)}
        shown = {str(c) for c in cells}
        out["ref_parcels"], out["ref_found"] = len(uids), len(uids & shown)
        if uids:
            match.append(uids <= shown)
        else:
            out["ref_empty"] = True          # empty reference set: judged by the qa rubric, not auto-matched
    out["answer_match"] = all(match) if match else None
    out["latency_ms"] = st["telemetry"]["latency_ms"]
    out["shadow_cost_usd"] = st["telemetry"]["shadow_cost_usd"]
    out["actual_cost_usd"] = st["telemetry"]["actual_cost_usd"]
    out["routes"] = [(t["task"], t["choice"]) for t in st["route_trace"]]
    out["fallbacks"] = [f"{e['data'].get('from')}->{e['data'].get('to')} ({e['data'].get('reason')})"
                        for e in evs if e["type"] == "fallback"][:6]
    out["fixture_misses"] = sum(1 for t in st["route_trace"] for x in t["attempts"]
                                if x.get("error_kind") == "fixture_missing")
    return out


async def run_one(q: dict) -> tuple[list[dict], float]:
    from app.agent import resume_run, run_request
    atts = [{**a, "path": str(ROOT / a["path"])} for a in q.get("attachments", [])]
    t0 = time.monotonic()
    evs = [e async for e in run_request(q["request"], atts, run_id=f"dev-{q['id']}-{int(time.time())}")]
    if any(e["type"] == "clarify" for e in evs) and q.get("clarify_answer"):
        rid = evs[0]["run_id"]
        evs += [e async for e in resume_run(rid, q["clarify_answer"])]
    return evs, time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--mode", default=os.environ.get("ROUTER_MODE", "live"))
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "agent_dev.json"))
    ap.add_argument("--queries", default=str(Path(__file__).with_name("dev_queries.yaml")))
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["ROUTER_MODE"] = a.mode
    if a.mode in ("record", "replay"):
        isolate(a.mode, clean=not a.only)
    from eval.agent.fixture_db import ensure_fixture_db
    kg = ensure_fixture_db()
    os.environ["AGENT_KG_URL"] = kg
    from app.agent.runtime import known_names
    names = [n for n in known_names(kg) if len(n) > 3]
    audit: list[dict] = []
    _pii_tap(names, audit)
    qs = yaml.safe_load(Path(a.queries).read_text(encoding="utf-8"))["queries"]
    if a.only:
        keep = set(a.only.split(","))
        qs = [q for q in qs if q["id"] in keep]
    rows = []
    for q in qs:
        evs, secs = asyncio.run(run_one(q))
        r = score(q, evs, kg)
        r["wall_s"] = round(secs, 1)
        rows.append(r)
        print(json.dumps({k: r.get(k) for k in ("id", "result", "spec_valid", "plan_valid", "tools", "verdicts",
                                                  "answer_match", "reroutes", "wall_s")}, ensure_ascii=False))
    ran = [r for r in rows if r["result"]]
    ts = sum(r.get("tool_steps", 0) for r in ran)
    summary = {
        "queries": len(rows), "results": len(ran),
        "spec_valid": sum(1 for r in ran if r.get("spec_valid")),
        "plan_validity": round(sum(1 for r in ran if r.get("plan_valid")) / max(1, len(ran)), 3),
        "plan_valid_first_model_call": round(sum(1 for r in ran if (r.get("plan_meta") or {}).get("first_call_valid"))
                                             / max(1, len(ran)), 3),
        "tool_success": round(sum(r.get("tool_ok", 0) for r in ran) / max(1, ts), 3),
        "tool_first_attempt_success": round(sum(r.get("tool_first_ok", 0) for r in ran) / max(1, ts), 3),
        "verified_claim_rate": round(sum(r["verdicts"].get("ACCEPT", 0) for r in ran) /
                                     max(1, sum(r.get("claims", 0) for r in ran)), 3),
        "answer_match": f"{sum(1 for r in ran if r.get('answer_match'))}/{sum(1 for r in ran if r.get('answer_match') is not None)}",
        "pii_payloads_checked": len(audit), "pii_leaks": sum(1 for x in audit if x["hits"]),
        "route_distribution": dict(Counter(f"{t}:{c}" for r in ran for t, c in r.get("routes", []))),
        "shadow_cost_usd": round(sum(r.get("shadow_cost_usd", 0) for r in ran), 5),
        "actual_cost_usd": round(sum(r.get("actual_cost_usd", 0) for r in ran), 5),
        "p50_wall_s": sorted(r["wall_s"] for r in rows)[len(rows) // 2] if rows else None,
        "mode": a.mode,
        "fixture_misses": sum(r.get("fixture_misses", 0) for r in ran),
        "agent_today": os.environ.get("AGENT_TODAY"),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "queries": rows}, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    ok = summary["spec_valid"] == len(ran) and summary["plan_validity"] >= 0.95 and summary["tool_success"] >= 0.9 \
        and summary["pii_leaks"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
