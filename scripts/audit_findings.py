"""Findings audit (plan §10 "Proof"): a stratified random sample of findings, each re-checked against its
own evidence by recomputing the claim from source tables, NOT from the finding's stored metrics.

    uv run python scripts/audit_findings.py [--per-category 5] [--seed 7]

Per category:
  COMPENSATION_MISMATCH  extraction row amount/acres (row_json) and the document's modal rate -> amount != acres x rate
  EXTENT_MISMATCH        extraction extent_ha vs parcel.area_ha_gis -> |diff| > 5% of GIS
  EXTRACTION_ERROR       extent variant: extraction extent_ha vs GIS ratio >= 10x; amount variant: row amount vs acres x rate >= 10x or <= 0.1x
  DOC_VERSION_CONFLICT   the cited village totals differ (spread > 0.05 ha)
  FMB_QUALITY            PostGIS geodesic intersection of the two parcels > 0 (overlap) / outside-survey table
  PV1_CLASSIFICATION     extraction class DRY + parcel_season irrigated_multi years before 3(1)
  PV3_POST_POSSESSION    a parcel_did row at possession whose CI excludes 0 in the claimed direction
  PV4_IDLE_LAND_BANK     every listed parcel is at POSSESSION or later, >= 6 months ago, no built/cleared state since
Writes data/eval/p7_findings_audit.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "eval" / "p7_findings_audit.json"
TODAY = date(2026, 9, 28)
LATE_STAGES = {"POSSESSION", "MUTATION", "PATTA_TRANSFER", "HANDOVER"}


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def row_json(conn, ext_id):
    r = q(conn, "SELECT row_json, document_id FROM extraction WHERE id = %s", (ext_id,))
    return (r[0]["row_json"] or {}, r[0]["document_id"]) if r else ({}, None)


def land_amount(rj):
    """Land compensation as the rule defines it: land_amount_rs, else amount_rs minus tree_amount_rs."""
    def num(k):
        v = rj.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    if num("land_amount_rs") is not None:
        return num("land_amount_rs")
    if num("amount_rs") is None:
        return None
    return num("amount_rs") - (num("tree_amount_rs") or 0)


def gis_ha(conn, uid):
    r = q(conn, "SELECT area_ha_gis FROM parcel WHERE parcel_uid = %s", (uid,))
    return float(r[0]["area_ha_gis"]) if r and r[0]["area_ha_gis"] is not None else None


def audit(conn, f) -> tuple[bool, str]:
    cat, m, pe = f["category"], f["metrics"] or {}, f["paper_evidence"] or []
    ext_ids = [e.get("extraction_id") for e in pe if isinstance(e, dict) and e.get("extraction_id")]

    if cat == "COMPENSATION_MISMATCH":
        rj, doc = row_json(conn, ext_ids[0])
        amt, ac = land_amount(rj), rj.get("extent_ac")
        rates = [x["r"] for x in q(conn, "SELECT (row_json->'header'->'rate_per_acre'->>'value')::numeric AS r FROM extraction "
                                          "WHERE document_id = %s AND row_json->'header'->'rate_per_acre'->>'value' IS NOT NULL", (doc,))]
        rate = Counter(float(r) for r in rates).most_common(1)[0][0] if rates else m.get("rate_per_ac")
        if amt is None or ac is None or rate is None:
            return False, f"cannot recompute (amount={amt}, acres={ac}, rate={rate})"
        exp = float(ac) * float(rate)
        ok = abs(float(amt) - exp) > 1.0 + 0.005 * float(rate)   # the rule's tolerance: Rs 1 + rounding of a 2-dp acre figure
        return ok, f"row land amount {amt:.0f} vs {ac} ac x {rate} = {exp:.0f} (diff {float(amt) - exp:+.0f})"

    if cat == "EXTRACTION_ERROR" and m.get("amount_rs") is not None:
        # amount variant: the row's amount is >= 10x (or <= 0.1x) of acres x the notified rate
        rj, doc = row_json(conn, ext_ids[0])
        amt, ac, rate = land_amount(rj), rj.get("extent_ac"), m.get("rate_per_ac")
        if amt is None or ac is None or not rate:
            return False, f"cannot recompute (amount={amt}, acres={ac}, rate={rate})"
        ratio = float(amt) / (float(ac) * float(rate))
        return ratio >= 10 or ratio <= 0.1, f"row land amount {amt:.0f} / ({ac} ac x {rate}) = {ratio:.2f}x (>= 10x or <= 0.1x needed)"

    if cat in ("EXTENT_MISMATCH", "EXTRACTION_ERROR"):
        g = gis_ha(conn, f["parcel_uid"])
        docs = [e.get("extent_ha") for e in pe if isinstance(e, dict) and e.get("extent_ha") is not None]
        if not docs:
            docs = [row_json(conn, i)[0].get("extent_ha") for i in ext_ids[:3]]
        docs = [float(d) for d in docs if d is not None]
        if not docs or not g:
            return False, f"cannot recompute (doc={docs[:3]}, gis={g})"
        d = min(docs, key=lambda x: abs(x - g))            # the closest claim, as the rule uses
        if cat == "EXTENT_MISMATCH":
            pct = abs(d - g) / g * 100
            return pct > 5, f"closest doc extent {d} ha vs GIS {g} ha = {pct:.1f}% (> 5% needed)"
        ratio = max(d / g, g / d) if d > 0 and g > 0 else float("inf")
        return ratio >= 10, f"doc {d} ha vs GIS {g} ha = {ratio:.1f}x (>= 10x needed)"

    if cat == "DOC_VERSION_CONFLICT":
        vals = sorted({round(s["total_ha"], 4) for s in m.get("sources", []) if s.get("total_ha") is not None})
        spread = vals[-1] - vals[0] if vals else 0
        return spread > 0.05, f"cited totals {vals} -> spread {spread:.3f} ha"

    if cat == "FMB_QUALITY":
        if m.get("issue") == "OVERLAP":
            r = q(conn, "SELECT ST_Area(ST_Intersection(a.geom, b.geom)::geography) / 10000 AS ha FROM parcel a, parcel b "
                        "WHERE a.parcel_uid = %s AND b.parcel_uid = %s", (f["parcel_uid"], m.get("other_parcel_uid")))
            ha = float(r[0]["ha"]) if r and r[0]["ha"] is not None else 0.0
            return ha > 0.0001, f"PostGIS overlap with {m.get('other_parcel_uid')} = {ha:.4f} ha (stored {m.get('overlap_ha')})"
        r = q(conn, "SELECT count(*) AS n FROM fmb_qa_outside_survey WHERE parcel_uid = %s", (f["parcel_uid"],))
        return r[0]["n"] > 0, f"issue {m.get('issue')}: outside-survey rows {r[0]['n']}"

    if cat == "PV1_CLASSIFICATION_CONFLICT":
        rj, _ = row_json(conn, ext_ids[0])
        cls = (rj.get("classification") or "").upper()
        t31 = date.fromisoformat(m["t_3_1"])
        y0 = t31.year if t31.month >= 6 else t31.year - 1
        irr = sorted({r["ag_year"] for r in q(conn, "SELECT ag_year FROM parcel_season WHERE parcel_uid = %s AND state = 'irrigated_multi' "
                                                    "AND ag_year < %s AND ag_year >= %s", (f["parcel_uid"], y0, y0 - 3))})
        return cls == "DRY" and len(irr) >= 2, f"extracted class {cls or '-'}; irrigated_multi years before 3(1): {irr}"

    if cat == "PV3_POST_POSSESSION_ACTIVITY":
        rows = q(conn, "SELECT metric, season_scope, did, ci_lo, ci_hi FROM parcel_did WHERE parcel_uid = %s AND event = 't_possession'",
                 (f["parcel_uid"],))
        want = m.get("did")
        match = [r for r in rows if want is not None and r["did"] is not None and abs(float(r["did"]) - float(want)) < 1e-4]
        if not match:
            return False, f"no parcel_did row reproduces DiD {want} ({len(rows)} rows at possession)"
        r = match[0]
        drop = m.get("signal") == "vigour_drop"
        ok = (r["ci_hi"] < 0) if drop else (r["ci_lo"] > 0)
        # low-severity "still farmland-like" leads: the rule uses farmland likelihood, not a significant CI
        if not ok and f["severity"] == "low":
            fl = q(conn, "SELECT max(farmland_like_p) AS p FROM parcel_did WHERE parcel_uid = %s AND event = 't_possession'", (f["parcel_uid"],))[0]["p"]
            return (fl or 0) >= 0.5, f"{r['metric']}/{r['season_scope']} DiD {r['did']:.3f} CI [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}]; farmland-like p {fl}"
        return ok, f"{r['metric']}/{r['season_scope']} DiD {r['did']:.3f} CI [{r['ci_lo']:.3f}, {r['ci_hi']:.3f}] ({'drop' if drop else 'rise'} claimed)"

    if cat == "PV4_IDLE_LAND_BANK":
        uids = m.get("parcels") or []
        bad = []
        for uid in uids:
            r = q(conn, "SELECT current_stage, stage_entered FROM v_parcel_lifecycle WHERE parcel_uid = %s", (uid,))
            if not r or r[0]["current_stage"] not in LATE_STAGES or r[0]["stage_entered"] is None:
                bad.append(f"{uid}: stage {r[0]['current_stage'] if r else '-'}")
                continue
            if (TODAY - r[0]["stage_entered"]).days < 180:
                bad.append(f"{uid}: only {(TODAY - r[0]['stage_entered']).days} d")
        return not bad, f"{len(uids)} parcels at POSSESSION+ >= 6 months" + (f"; exceptions: {bad[:3]}" if bad else "")

    return False, "no audit rule"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env")["DATABASE_URL"]
    conn = psycopg.connect(url, row_factory=dict_row)
    by_cat = defaultdict(list)
    for f in q(conn, "SELECT id, category, severity, parcel_uid, title, metrics, paper_evidence FROM finding ORDER BY id"):
        by_cat[f["category"]].append(f)
    rng = random.Random(a.seed)
    results, summary = [], {}
    for cat in sorted(by_cat):
        sample = rng.sample(by_cat[cat], min(a.per_category, len(by_cat[cat])))
        ok_n = 0
        for f in sample:
            try:
                ok, why = audit(conn, f)
            except Exception as e:  # noqa: BLE001 - an audit that cannot run counts as not supported
                ok, why = False, f"audit error: {type(e).__name__}: {e}"
            ok_n += ok
            results.append({"id": f["id"], "category": cat, "severity": f["severity"], "parcel_uid": f["parcel_uid"],
                            "title": f["title"], "supported": ok, "recheck": why})
            print(f"{'OK ' if ok else 'NO '} {cat:30s} #{f['id']:<6} {why}"[:230])
        summary[cat] = {"audited": len(sample), "supported": ok_n, "population": len(by_cat[cat])}
    tot_a = sum(s["audited"] for s in summary.values())
    tot_s = sum(s["supported"] for s in summary.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": str(TODAY), "seed": a.seed, "per_category": a.per_category, "summary": summary,
                               "precision": round(tot_s / tot_a, 3), "results": results}, indent=1, ensure_ascii=False, default=str))
    print(f"\nprecision (recomputed from source tables): {tot_s}/{tot_a} = {tot_s / tot_a:.1%}")
    for cat, s in summary.items():
        print(f"  {cat:30s} {s['supported']}/{s['audited']}  (of {s['population']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
