"""python -m pipeline.findings — run the T5.3 rules on the DB and upsert the `finding` table.

Idempotent: findings are keyed by `finding_key`; a rerun updates them in place (a reviewer's
verdict is kept). Findings no longer produced are deleted, or marked `stale` if they carry a verdict.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import date

import psycopg
from dotenv import load_dotenv

from pipeline.findings import rules
from pipeline.normalize.village import normalise_village


_TA_CONFUSABLE = [("ர", "ற"), ("ற", "ர"), ("ி", "ீ"), ("ீ", "ி"), ("ழ", "ள"), ("ள", "ழ")]


def village_of(raw: str | None) -> str | None:
    """normalise_village, then one pass of Tamil OCR-confusable swaps (ர/ற, ி/ீ, ழ/ள) — all
    positions of one pair at a time, then pairs combined. Alias gap reported upstream."""
    if not raw:
        return None
    v = normalise_village(raw)
    if v:
        return v
    cands = [raw]
    for a, b in _TA_CONFUSABLE:
        cands += [c.replace(a, b) for c in cands if a in c]
        if len(cands) > 64:
            break
    for c in cands[1:]:
        v = normalise_village(c)
        if v:
            return v
    return None


def _q(conn, sql, params=None) -> list[dict]:
    cur = conn.execute(sql, params or {})
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _f(x):
    return float(x) if x is not None else None


def load_possession(conn) -> dict[str, dict]:
    rows = _q(conn, """
        SELECT e.parcel_uid, min(e.event_date) AS possession_date,
               CASE WHEN bool_or(e.link_kind <> 'block_level') THEN 'parcel' ELSE 'block' END AS level,
               (jsonb_agg(jsonb_build_object('event_id', e.event_id, 'document_id', e.document_id,
                          'extraction_id', e.extraction_id, 'link', e.link_kind, 'date', e.event_date)
                          ORDER BY (e.link_kind = 'block_level'), e.event_date))
                 AS evidence
        FROM v_parcel_events e WHERE e.stage = 'POSSESSION' GROUP BY e.parcel_uid""")
    return {r["parcel_uid"]: r for r in rows}


def load_did(conn, event: str, scope: str) -> dict[tuple[str, str], dict]:
    rows = _q(conn, """SELECT parcel_uid, metric, did, ci_lo, ci_hi, farmland_like_p, n_pre, n_post, event_date
                       FROM parcel_did WHERE event = %(e)s AND season_scope = %(s)s""", {"e": event, "s": scope})
    return {(r["parcel_uid"], r["metric"]): {k: (str(v) if k == "event_date" else _f(v) if k not in ("parcel_uid", "metric") else v)
                                              for k, v in r.items()} for r in rows}


def run(conn, today: date | None = None) -> list[rules.Finding]:
    today = today or date.today()
    scope = rules.F.get("pv3", {}).get("season_scope", "rabi")
    poss = load_possession(conn)
    did = load_did(conn, "t_possession", scope)
    parcels = {r["parcel_uid"]: r for r in _q(conn, """
        SELECT p.parcel_uid, v.name AS village, p.unit_id, p.block_id, p.area_ha_gis AS area_ha,
               p.dist_major_road_m, p.dist_substation_m FROM parcel p JOIN village v ON v.id = p.village_id""")}
    seasons = defaultdict(list)
    for r in _q(conn, "SELECT parcel_uid, ag_year, season, state FROM parcel_season WHERE parcel_uid IS NOT NULL"):
        seasons[r["parcel_uid"]].append(r)
    out: list[rules.Finding] = []

    # PV3 + PV4
    pv3_in, pv4_in = [], []
    for uid, ps in poss.items():
        p = parcels[uid]
        ev = ps["evidence"][:5]
        pv3_in.append({"parcel_uid": uid, "village": p["village"], "possession_level": ps["level"],
                       "possession_date": ps["possession_date"], "possession_evidence": ev,
                       "vigour": did.get((uid, "vigour_z")), "plough": did.get((uid, "plough_z"))})
        if ps["possession_date"]:
            y0 = rules.ag_year(ps["possession_date"])
            pv4_in.append({**p, "possession_date": ps["possession_date"], "possession_level": ps["level"],
                           "possession_evidence": ev,
                           "states_since": [s["state"] for s in seasons[uid] if s["ag_year"] >= y0]})
    out += rules.pv3_post_possession(pv3_in)
    out += rules.pv4_idle_land_bank(pv4_in, today)

    # PV1
    t31 = {k[0]: v["event_date"] for k, v in load_did(conn, "t_3_1", "all").items() if k[1] == "vigour_z"}
    cls = _q(conn, """SELECT f.parcel_uid, f.value_text, array_agg(f.extraction_id) AS ext
                      FROM parcel_fact f WHERE f.fact_type = 'classification' AND f.parcel_uid IS NOT NULL
                        AND f.value_text IN ('DRY','WET','PORAMBOKE') GROUP BY 1, 2""")
    pv1_in = [{"parcel_uid": c["parcel_uid"], "village": parcels[c["parcel_uid"]]["village"],
               "classification": c["value_text"], "t_3_1": date.fromisoformat(t31[c["parcel_uid"]]) if t31.get(c["parcel_uid"]) else None,
               "seasons": seasons[c["parcel_uid"]], "evidence": [{"extraction_id": e} for e in c["ext"][:5]]} for c in cls]
    out += rules.pv1_classification(pv1_in)

    # EXTENT
    claims = defaultdict(list)
    for r in _q(conn, """SELECT f.parcel_uid, f.value_num AS extent_ha, f.extraction_id, e.document_id, e.page_no,
                                COALESCE((e.row_json->>'part')::boolean, false) AS part, f.match_score
                         FROM parcel_fact f JOIN extraction e ON e.id = f.extraction_id
                         WHERE f.fact_type IN ('extent_ha','total_extent_ha') AND f.parcel_uid IS NOT NULL AND f.value_num > 0"""):
        claims[r["parcel_uid"]].append({**r, "extent_ha": _f(r["extent_ha"]), "match_score": _f(r["match_score"])})
    out += rules.extent_mismatch([{"parcel_uid": u, "village": parcels[u]["village"], "area_ha": parcels[u]["area_ha"],
                                   "claims": c} for u, c in claims.items()])

    # COMPENSATION
    docs = defaultdict(list)
    for r in _q(conn, r"""
        SELECT DISTINCT ON (e.content_hash, e.record_no)   -- duplicate PDFs carry identical pages
               e.id AS extraction_id, e.document_id, e.page_no,
               NULLIF(e.row_json->>'extent_ac','')::numeric AS extent_ac,
               COALESCE(NULLIF(e.row_json->>'land_amount_rs','')::numeric,
                        NULLIF(e.row_json->>'amount_rs','')::numeric - COALESCE(NULLIF(e.row_json->>'tree_amount_rs','')::numeric, 0))
                 AS land_amount_rs,
               (SELECT f.parcel_uid FROM parcel_fact f WHERE f.extraction_id = e.id AND f.parcel_uid IS NOT NULL LIMIT 1) AS parcel_uid,
               (SELECT f.match_village FROM parcel_fact f WHERE f.extraction_id = e.id AND f.match_village IS NOT NULL LIMIT 1) AS village
        FROM extraction e
        WHERE e.doc_type IN ('AWARD_7_2','AWARD_7_3','FORM_F') AND e.schema_name IN ('parcel_row','form_f')
          AND e.row_json->>'extent_ac' ~ '^[0-9.]+$'
          AND COALESCE(e.row_json->>'land_amount_rs', e.row_json->>'amount_rs') ~ '^[0-9.]+$'
        ORDER BY e.content_hash, e.record_no, e.id"""):
        if r["extent_ac"] and r["land_amount_rs"] and r["land_amount_rs"] > 0:
            docs[r["document_id"]].append({**r, "extent_ac": float(r["extent_ac"]), "land_amount_rs": float(r["land_amount_rs"])})
    out += rules.compensation_mismatch(docs)

    # DOC VERSION
    src, pages = [], defaultdict(set)
    rows = _q(conn, """SELECT e.id AS extraction_id, e.document_id, e.page_no, e.doc_type, d.path, d.folder_label AS folder, e.row_json
                       FROM extraction e JOIN document d ON d.id = e.document_id
                       WHERE e.schema_name = 'village_totals' AND e.doc_type IN ('GO','AS','LPS')""")
    for r in rows:
        j = r["row_json"]
        v = village_of(j.get("village")) or village_of(j.get("village_raw"))
        tot = j.get("total_ha")
        if tot is None and j.get("patta_ha") is not None:
            tot = float(j.get("patta_ha") or 0) + float(j.get("poramboke_ha") or 0)
        if v and tot:
            src.append({**r, "village": v, "total_ha": float(tot)})
            pages[(r["document_id"], r["page_no"])].add(v)
    min_v = int(rules.F.get("doc_version", {}).get("min_villages_on_page", 5))
    src = [s for s in src if len(pages[(s["document_id"], s["page_no"])]) >= min_v]   # scheme-wide tables only
    # one value per village per page (first row)
    seen, uniq = set(), []
    for s in sorted(src, key=lambda s: s["extraction_id"]):
        k = (s["village"], s["document_id"], s["page_no"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    fmb_union = {r["name"]: round(float(r["ha"]), 4) for r in _q(conn, """
        SELECT v.name, geo_area_ha(ST_Union(p.geom)) AS ha FROM parcel p JOIN village v ON v.id = p.village_id GROUP BY 1""")}
    out += rules.doc_version_conflict(uniq, fmb_union)

    # FMB QUALITY
    out += rules.fmb_quality(_q(conn, "SELECT * FROM fmb_qa_overlap"), _q(conn, "SELECT * FROM fmb_qa_outside_survey"))
    return out


def upsert(conn, findings: list[rules.Finding]) -> None:
    cols = ["finding_key", "category", "severity", "confidence", "parcel_uid", "village", "unit_id", "block_id",
            "title", "metrics", "paper_evidence", "planet_evidence", "caveats", "evidence_level", "rule_version"]
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _f (LIKE finding INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy(f"COPY _f ({', '.join(cols)}) FROM STDIN") as cp:
            for f in findings:
                d = f.row()
                cp.write_row([d["key"], d["category"], d["severity"], d["confidence"], d["parcel_uid"], d["village"],
                              d["unit_id"], d["block_id"], d["title"],
                              *(json.dumps(d[k], default=str, ensure_ascii=False) for k in ("metrics", "paper_evidence", "planet_evidence")),
                              d["caveats"], d["evidence_level"], rules.RULE_VERSION])
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
        cur.execute(f"""INSERT INTO finding ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _f
                        ON CONFLICT (finding_key) DO UPDATE SET {upd}, status = 'open', updated_at = now()""")
        cur.execute("DELETE FROM finding f WHERE verdict IS NULL AND NOT EXISTS (SELECT 1 FROM _f WHERE _f.finding_key = f.finding_key)")
        cur.execute("UPDATE finding f SET status = 'stale' WHERE NOT EXISTS (SELECT 1 FROM _f WHERE _f.finding_key = f.finding_key)")
        cur.execute("DROP TABLE _f")


def main() -> None:
    load_dotenv()
    t0 = time.time()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        fs = run(conn)
        upsert(conn, fs)
        conn.commit()
    c = Counter((f.category, f.severity) for f in fs)
    for cat in sorted({k[0] for k in c}):
        print(f"{cat}: " + "  ".join(f"{s}={c[(cat, s)]}" for s in ("high", "medium", "low") if c[(cat, s)]))
    print(f"total {len(fs)}  runtime {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
