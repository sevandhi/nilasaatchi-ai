"""Schema-agnostic recall for candidate (g) whole-page VLM tables, using the bake-off normalisers."""
import json
import re
import sys

from rapidfuzz import fuzz

from spikes.ocr_bakeoff import normalize as N
from spikes.ocr_bakeoff.value_recall import _nums, _surveys

d = json.load(open(sys.argv[1]))
gold = {g["page_id"]: g for g in map(json.loads, open("eval/golden/mini.jsonl"))}
tot = {"num": [0, 0], "survey": [0, 0], "owner": [0, 0]}
per = []
for pid, g in gold.items():
    r = d.get(pid) or {}
    cells = []
    for t in ((r.get("data") or {}).get("tables") or []):
        for row in t.get("rows") or []:
            cells += [c for c in row if c]
    vals = set()
    for c in cells:
        for fn in ("parse_extent", "parse_amount"):
            f = getattr(N, fn, None)
            try:
                v = f(c) if f else None
            except Exception:
                v = None
            if isinstance(v, (int, float)):
                vals.add(round(float(v), 3))
        for m in re.findall(r"\d[\d,]*\.?\d*", c):
            try:
                vals.add(round(float(m.replace(",", "")), 3))
            except ValueError:
                pass
    gn = _nums(g)
    ps = set()
    for c in cells:
        for m in re.findall(r"\b(\d{1,4})\s*/\s*([0-9A-Za-z]{1,4})\b", c):
            ps.add(f"{m[0]}/{m[1]}")
        if re.fullmatch(r"\d{1,4}", c.strip()):
            ps.add(c.strip())
    gs = _surveys(g.get("rows", []))
    go = [x["owner"] for x in g.get("rows", []) if x.get("owner")]
    no = sum(any(fuzz.token_set_ratio(o, c) >= 85 for c in cells) for o in go)
    tot["num"][0] += len(gn & vals); tot["num"][1] += len(gn)
    tot["survey"][0] += len(gs & ps); tot["survey"][1] += len(gs)
    tot["owner"][0] += no; tot["owner"][1] += len(go)
    per.append((pid, g["doc_type"], f"{len(gn & vals)}/{len(gn)}", f"{len(gs & ps)}/{len(gs)}", f"{no}/{len(go)}"))
for p in per:
    print(*p)
print("TOTAL", " ".join(f"{k}: {a}/{b} = {100*a/max(b,1):.0f}%" for k, (a, b) in tot.items()))
