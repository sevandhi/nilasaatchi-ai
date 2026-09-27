"""Task-1 agent eval: the 8 `agent_eval` queries of eval/queries.yaml end-to-end, LIVE, on the real KG.

    PYTHONPATH=. uv run python -m eval.agent.run [--only a1,a3] [--chaos gemini:down] [--out data/eval/agent_run.json]

Free models only (Gemini flash-lite / Groq / Cohere trial); shadow cost is reported, actual spend should be $0.
Reference answers come from `expect.numbers_sql` / `expect.parcels_sql` evaluated here at run time (never in app/).
Metrics per query: plan validity, tool success, expected-tool hit, verified-claim rate, critic challenge run
(vendor != producer), fallbacks, answer match, wall latency, tokens, shadow cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import yaml

from eval.agent.run_dev import run_one, score

ROOT = Path(__file__).resolve().parents[2]


def summarise(q: dict, evs: list[dict], wall_s: float, kg: str) -> dict:
    s = score({**q, "expect": {k: v for k, v in q.get("expect", {}).items() if k != "tools_any"}}, evs, kg)
    res = next((e["data"] for e in evs if e["type"] == "result"), None)
    fb = [e["data"] for e in evs if e["type"] == "fallback"]
    crit = [e["data"] for e in evs if e["type"] == "critic" and not e["data"].get("skipped")]
    plan = next((e["data"] for e in evs if e["type"] == "plan"), {})
    row = {"id": q["id"], "story": q.get("story"), "result": s.get("result"), "plan_valid": s.get("plan_valid"),
           "planner": plan.get("model_id"), "tools": s.get("tools", []),
           "tool_ok": f"{s.get('tool_ok', 0)}/{s.get('tool_steps', 0)}",
           "expected_tool": bool(set(q["expect"].get("tools_any", [])) & {t.split(":")[0] for t in s.get("tools", [])}),
           "answer_match": s.get("answer_match"), "fallbacks": [f"{f.get('task') or f.get('step_id')}:{f['from']}->"
                                                                 f"{f['to']}({f['reason']})" for f in fb],
           "critic": [{"vendor": c["vendor"], "producer_vendor": c["producer_vendor"], "refuted": c["result"]["refuted"],
                       "tool": c["check"]["tool"]} for c in crit],
           "wall_s": round(wall_s, 1), "error": s.get("error")}
    if res:
        st = res["state"]
        n = len(st["claims"])
        acc = sum(1 for v in st["verdicts"].values() if v["verdict"] == "ACCEPT")
        row.update(claims=n, verified_rate=round(acc / n, 3) if n else None, verdicts=s.get("verdicts"),
                   narrative_check=s.get("narrative_check"), tokens=st["telemetry"]["tokens_in"] + st["telemetry"]["tokens_out"],
                   shadow_usd=st["telemetry"]["shadow_cost_usd"], actual_usd=st["telemetry"]["actual_cost_usd"],
                   llm_calls=st["telemetry"]["llm_calls"], models=sorted({r["choice"] for r in st["route_trace"] if r.get("choice")}),
                   narrative=st["narrative"]["en"][:300])
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--chaos", default="", help="e.g. gemini:down (sets ROUTER_CHAOS)")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "agent_run.json"))
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    kg = os.environ["DATABASE_URL"]
    os.environ.setdefault("AGENT_KG_URL", kg)
    os.environ.setdefault("AGENT_CHECKPOINTER", "memory")
    if a.chaos:
        os.environ["ROUTER_CHAOS"] = a.chaos
    qs = yaml.safe_load((ROOT / "eval" / "queries.yaml").read_text(encoding="utf-8"))["agent_eval"]
    if a.only:
        qs = [q for q in qs if q["id"] in a.only.split(",")]
    rows = []
    for q in qs:
        t0 = time.monotonic()
        try:
            evs, _ = asyncio.run(run_one(q))
        except Exception as e:  # noqa: BLE001 - report, keep going
            evs = [{"type": "error", "data": {"message": f"{type(e).__name__}: {e}"}}]
        rows.append(summarise(q, evs, time.monotonic() - t0, kg))
        r = rows[-1]
        print(f"{r['id']} plan={r['plan_valid']} tools={r['tool_ok']} exp_tool={r['expected_tool']} "
              f"match={r['answer_match']} verified={r.get('verified_rate')} critic={len(r['critic'])} "
              f"fb={len(r['fallbacks'])} {r['wall_s']}s ${r.get('shadow_usd')}", flush=True)
    ok = [r for r in rows if r["result"]]
    agg = {"n": len(rows), "results": len(ok),
           "plan_validity": sum(bool(r["plan_valid"]) for r in rows) / len(rows),
           "tool_success": sum(int(r["tool_ok"].split("/")[0]) for r in rows) /
                           max(1, sum(int(r["tool_ok"].split("/")[1]) for r in rows)),
           "expected_tool_rate": sum(r["expected_tool"] for r in rows) / len(rows),
           "answer_match_rate": sum(bool(r["answer_match"]) for r in rows) / len(rows),
           "verified_claim_rate": round(sum(r.get("verified_rate") or 0 for r in ok) / max(1, len(ok)), 3),
           "critic_challenges_run": sum(len(r["critic"]) for r in rows),
           "critic_vendor_independent": all(c["vendor"] != c["producer_vendor"] for r in rows for c in r["critic"]),
           "p50_wall_s": sorted(r["wall_s"] for r in rows)[len(rows) // 2],
           "shadow_usd_total": round(sum(r.get("shadow_usd") or 0 for r in rows), 5),
           "actual_usd_total": round(sum(r.get("actual_usd") or 0 for r in rows), 5), "chaos": a.chaos or None}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": agg, "rows": rows}, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(agg, indent=1))
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
