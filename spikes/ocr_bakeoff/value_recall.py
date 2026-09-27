"""Schema-agnostic reading accuracy: share of gold numeric values / survey refs / owners recovered anywhere on the page."""
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

NUM_FIELDS = ("extent_ha", "extent_ac", "cents", "amount_rs", "patta_ha", "poramboke_ha", "tirvai_rs")


def _nums(obj):
    out = set()
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in NUM_FIELDS and isinstance(v, (int, float)) and v:
                    out.add(round(float(v), 3))
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(obj)
    return out


def _pred_nums(parsed):
    vals = set()
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            vals.add(round(float(o), 3))
        elif isinstance(o, str):
            for m in re.findall(r"\d[\d,]*\.?\d*", o.replace(",", "")):
                try:
                    vals.add(round(float(m), 3))
                except ValueError:
                    pass
    walk(parsed)
    return vals


def _surveys(rows):
    return {f"{r.get('survey_no')}/{r.get('sub_div') or ''}".strip("/") for r in rows if r.get("survey_no")}


def main(results_path: str, cand: str) -> None:
    gold = {g["page_id"]: g for g in map(json.loads, open("eval/golden/mini.jsonl"))}
    res = json.load(open(results_path))["per_page"]
    tot = {"num": [0, 0], "survey": [0, 0], "owner": [0, 0]}
    for pid, g in gold.items():
        c = res.get(pid, {}).get("candidates", {}).get(cand)
        if not c:
            continue
        parsed = c.get("parsed") or {}
        rows = parsed.get("rows", []) if isinstance(parsed, dict) else []
        gn, pn = _nums(g), _pred_nums(parsed)
        tot["num"][0] += len(gn & pn); tot["num"][1] += len(gn)
        gs, ps = _surveys(g.get("rows", [])), _surveys(rows)
        tot["survey"][0] += len(gs & ps); tot["survey"][1] += len(gs)
        go = [r["owner"] for r in g.get("rows", []) if r.get("owner")]
        po = [str(r.get("owner") or "") + " " + str(r.get("classification") or "") for r in rows]
        for o in go:
            tot["owner"][1] += 1
            if any(fuzz.token_set_ratio(o, p) >= 85 for p in po):
                tot["owner"][0] += 1
    print(cand.ljust(18), " ".join(f"{k}: {a}/{b} = {100*a/max(b,1):.0f}%" for k, (a, b) in tot.items()))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
