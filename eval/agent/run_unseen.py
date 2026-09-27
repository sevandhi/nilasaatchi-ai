"""Unseen-suite harness (eval/queries.yaml). References come from SQL at run time; nothing is stored in app/.

    PYTHONPATH=. uv run python -m eval.agent.run_unseen --kg real|fixture [--only u01] [--check-refs]

--check-refs only executes every reference_sql / polygons_sql against the KG (no model calls) to prove the suite
is runnable. Automatic metrics per query: result, spec validity, tools used vs expected, answer match, clarify /
refusal behaviour, PII leaks; the 0-4 rubric is filled in by qa-evaluator from the saved workspaces.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import yaml

from eval.agent.run_dev import _pii_tap, score

ROOT = Path(__file__).resolve().parents[2]


def _kg(which: str) -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if which == "fixture":
        from eval.agent.fixture_db import ensure_fixture_db
        return ensure_fixture_db()
    return os.environ["DATABASE_URL"]


def _polygons(q: dict, kg: str) -> list[dict]:
    if not q.get("polygons_sql"):
        return []
    from app.tools.db import query
    rows = query(kg, q["polygons_sql"], readonly_role=False)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"plot_id": r["plot_id"]}, "geometry": json.loads(r["geojson"])} for r in rows]}
    f = Path(tempfile.mkdtemp()) / f"{q['id']}_plots.geojson"
    f.write_text(json.dumps(fc))
    return [{"id": "a1", "name": f.name, "path": str(f), "media_type": "application/geo+json"}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kg", choices=["real", "fixture"], default="real")
    ap.add_argument("--only")
    ap.add_argument("--check-refs", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "unseen.json"))
    a = ap.parse_args()
    kg = _kg(a.kg)
    os.environ["AGENT_KG_URL"] = kg
    qs = yaml.safe_load((ROOT / "eval" / "queries.yaml").read_text(encoding="utf-8"))["unseen"]
    if a.only:
        qs = [q for q in qs if q["id"] in set(a.only.split(","))]
    if a.check_refs:
        from app.tools.db import query
        bad = 0
        for q in qs:
            for key in ("numbers_sql", "parcels_sql"):
                sql = (q.get("expect") or {}).get(key)
                if sql:
                    try:
                        n = len(query(kg, sql, readonly_role=False))
                        print(q["id"], key, "rows", n)
                    except Exception as e:  # noqa: BLE001
                        bad += 1
                        print(q["id"], key, "ERROR", str(e).splitlines()[0])
            if q.get("polygons_sql"):
                print(q["id"], "polygons", len(_polygons(q, kg)[0:1]) and len(json.loads(Path(_polygons(q, kg)[0]["path"]).read_text())["features"]))
        print(f"{len(qs)} queries, {bad} reference errors")
        return 1 if bad else 0
    from app.agent import run_request
    from app.agent.runtime import known_names
    audit: list[dict] = []
    _pii_tap([n for n in known_names(kg) if len(n) > 3], audit)
    rows = []
    for q in qs:
        async def go():
            return [e async for e in run_request(q["request"], _polygons(q, kg), run_id=f"unseen-{q['id']}-{int(time.time())}")]
        evs = asyncio.run(go())
        r = score(q, evs, kg)
        exp = q.get("expect", {})
        used = {t.split(":")[0] for t in r.get("tools", [])}
        r["tools_expected_hit"] = bool(set(exp.get("tools_any", [])) & used) if exp.get("tools_any") else None
        if exp.get("clarify"):
            r["behaviour_ok"] = r["clarified"]
        elif exp.get("cannot_answer"):
            r["behaviour_ok"] = None       # judged by qa from the narrative (must decline / downgrade)
        rows.append(r)
        print(json.dumps({k: r.get(k) for k in ("id", "result", "clarified", "spec_valid", "tools_expected_hit",
                                                  "answer_match")}, ensure_ascii=False))
    summary = {"queries": len(rows), "pii_leaks": sum(1 for x in audit if x["hits"]),
               "answer_match": f"{sum(1 for r in rows if r.get('answer_match'))}/"
                               f"{sum(1 for r in rows if r.get('answer_match') is not None)}",
               "tools_expected_hit": f"{sum(1 for r in rows if r.get('tools_expected_hit'))}/"
                                     f"{sum(1 for r in rows if r.get('tools_expected_hit') is not None)}"}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "queries": rows}, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
