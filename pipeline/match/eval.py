"""python -m pipeline.match.eval [--n 50] [--seed 7] — matcher evaluation against the FMB layer.

(1) match rate by village and doc type (matchable = has a survey ref and a resolved village);
(2) a seeded sample of accepted extent facts checked against the FMB layer: the parcel exists in the
    fact's village, its survey number equals the document's, and the document extent is <= 1.1 x
    area_ha_gis (owner shares may be smaller; geodesic areas, D-023) and within 10 % when whole.
Writes data/eval/match_eval.json.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]
LINKED = ("accepted", "survey_level", "list", "list_partial", "block_level")

RATE_SQL = """
SELECT {grp} AS k, count(*) AS rows,
       count(*) FILTER (WHERE match_status NOT IN ('no_ref','no_village')) AS matchable,
       count(*) FILTER (WHERE match_status = ANY(%(linked)s)) AS linked,
       count(*) FILTER (WHERE match_status = 'accepted') AS accepted,
       count(*) FILTER (WHERE match_status = 'ambiguous') AS ambiguous,
       count(*) FILTER (WHERE match_status = 'no_candidate') AS no_candidate,
       count(*) FILTER (WHERE match_candidates::text LIKE '%%other_village_hint%%') AS other_village_hint
FROM {table} {join} GROUP BY 1 ORDER BY 1 NULLS LAST"""

SAMPLE_SQL = """
SELECT f.id, f.match_village AS village, f.village AS raw_village,
       f.match_candidates::text LIKE '%%reresolved_from%%' AS reresolved, f.survey_no, f.sub_div, f.value_num AS extent_ha, f.doc_type, p.parcel_uid,
       v.name AS parcel_village, split_part(p.kide, '/', 1) AS fmb_survey, p.area_ha_gis, f.match_score
FROM parcel_fact f JOIN parcel p ON p.parcel_uid = f.parcel_uid JOIN village v ON v.id = p.village_id
WHERE f.match_status = 'accepted' AND f.fact_type = 'extent_ha' AND f.value_num > 0
ORDER BY md5(f.id::text || %(seed)s) LIMIT %(n)s"""


def rates(conn, table: str, grp: str, join: str = "") -> list[dict]:
    cur = conn.execute(RATE_SQL.format(grp=grp, table=table, join=join), {"linked": list(LINKED)})
    cols = [d.name for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["match_rate"] = round(d["linked"] / d["matchable"], 4) if d["matchable"] else None
        d["ambiguous_pct"] = round(100 * d["ambiguous"] / d["matchable"], 2) if d["matchable"] else None
        out.append(d)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", default="7")
    a = ap.parse_args()
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rep = {
            "facts_by_village": rates(conn, "parcel_fact", "match_village"),
            "facts_by_doc_type": rates(conn, "parcel_fact", "doc_type"),
            "events_by_village": rates(conn, "acquisition_event", "match_village"),
            "events_by_stage": rates(conn, "acquisition_event", "stage"),
        }
        cur = conn.execute(SAMPLE_SQL, {"seed": a.seed, "n": a.n})
        cols = [d.name for d in cur.description]
        sample = [dict(zip(cols, r)) for r in cur.fetchall()]
    checks = []
    for s in sample:
        area, ext = float(s["area_ha_gis"] or 0), float(s["extent_ha"])
        checks.append({**{k: (float(v) if hasattr(v, "is_finite") else v) for k, v in s.items()},
                       "village_ok": s["village"] == s["parcel_village"],
                       "survey_ok": s["survey_no"] is not None and s["fmb_survey"] == s["survey_no"].lstrip("0"),
                       "extent_le_area": ext <= 1.1 * area + 0.01,
                       "extent_within_10pct": area > 0 and abs(ext - area) / area <= 0.10})
    n = len(checks) or 1
    rep["sample"] = {"n": len(checks),
                     "village_ok": sum(c["village_ok"] for c in checks) / n,
                     "reresolved_in_sample": sum(bool(c["reresolved"]) for c in checks),
                     "survey_ok": sum(c["survey_ok"] for c in checks) / n,
                     "extent_le_1.1x_area": sum(c["extent_le_area"] for c in checks) / n,
                     "extent_within_10pct": sum(c["extent_within_10pct"] for c in checks) / n,
                     "failures": [c for c in checks if not (c["village_ok"] and c["survey_ok"] and c["extent_le_area"])]}
    out = ROOT / "data" / "eval" / "match_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    for key in ("facts_by_village", "facts_by_doc_type", "events_by_village"):
        print(f"\n{key}: k | rows | matchable | linked | rate | ambiguous%")
        for r in rep[key]:
            print(f"  {r['k']} | {r['rows']} | {r['matchable']} | {r['linked']} | {r['match_rate']} | {r['ambiguous_pct']}")
    s = rep["sample"]
    print(f"\nsample n={s['n']} village_ok={s['village_ok']:.2f} survey_ok={s['survey_ok']:.2f} "
          f"extent<=1.1x={s['extent_le_1.1x_area']:.2f} within10%={s['extent_within_10pct']:.2f} "
          f"failures={len(s['failures'])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
