"""land_acquisition domain tools: lifecycle_status, match_parcels, paper_vs_planet (interim), evidence_pack.

paper_vs_planet and match_parcels are INTERIM implementations with stable interfaces. TODO(P5): delegate to
pipeline.discrepancy (PV1-PV6 + document rules, thresholds in config/thresholds.yaml) and pipeline.match
(OCR-confusion variants, block bonus, top-3 with margin) when the gis-engineer lands them; the tool signatures
and ToolResult shapes below stay the same.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import Evidence
from app.tools.context import today
from app.tools.db import query
from app.tools.registry import ClaimSeed, Tool, ToolError, ToolResult

STAGE_ORDER = ["GO_AS_LPS", "SEC_3_1", "SEC_3_2", "EXEMPTION", "PRICE_NEGOTIATION", "POSSESSION_NOTICE",
               "AWARD", "PAYMENT", "POSSESSION", "MUTATION"]
MANDATORY = ["SEC_3_1", "SEC_3_2", "AWARD", "POSSESSION"]          # land-domain-knowledge §2 (stages 1,2,6,8)
NEXT_AFTER = {"SEC_3_1": "SEC_3_2", "SEC_3_2": "PRICE_NEGOTIATION", "PRICE_NEGOTIATION": "AWARD",
              "POSSESSION_NOTICE": "AWARD", "AWARD": "PAYMENT", "PAYMENT": "POSSESSION", "POSSESSION": "MUTATION",
              "MUTATION": None, "EXEMPTION": None, "GO_AS_LPS": "SEC_3_1"}
STALL_PAIRS = {"AWARD": "awarded_not_paid", "PAYMENT": "paid_no_possession", "POSSESSION": "possession_no_mutation"}
FIELD = "signal requiring field verification"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(_In):
    villages: list[str] = Field(default_factory=list)
    blocks: list[int] = Field(default_factory=list)
    parcel_uids: list[str] = Field(default_factory=list)


def _scope_sql(s: Scope) -> tuple[str, list]:
    w, p = [], []
    if s.villages:
        w.append("v.name = ANY(%s)")
        p.append(s.villages)
    if s.blocks:
        w.append("pa.block_id = ANY(%s)")
        p.append(s.blocks)
    if s.parcel_uids:
        w.append("pa.parcel_uid = ANY(%s)")
        p.append(s.parcel_uids)
    return " AND ".join(w) or "true", p


# ------------------------------------------------------------------------------------------ lifecycle_status
class LifecycleIn(Scope):
    as_of: str | None = Field(None, description="ISO date for days-in-stage (default today)")
    stalled_days: int = Field(90, ge=1, description="days without a next event before a parcel counts as stalled")
    only_stalled: bool = False


def lifecycle_status(a: LifecycleIn, ctx) -> ToolResult:
    scope, params = _scope_sql(a)
    rows = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, e.stage, e.event_date, e.document_id "
                         "FROM parcel pa JOIN village v ON v.id = pa.village_id "
                         "LEFT JOIN acquisition_event e ON e.parcel_uid = pa.parcel_uid "
                         f"WHERE {scope} ORDER BY pa.parcel_uid, e.event_date NULLS LAST, e.id", params)
    if not rows:
        raise ToolError("not_found", "no parcels in scope")
    as_of = date.fromisoformat(a.as_of) if a.as_of else today()
    parcels: dict[str, dict] = {}
    for r in rows:
        p = parcels.setdefault(r["parcel_uid"], {"parcel_uid": r["parcel_uid"], "village": r["village"],
                                                 "block": r["block_id"], "events": []})
        if r["stage"]:
            p["events"].append({"stage": r["stage"], "date": r["event_date"], "document_id": r["document_id"]})
    out, matrix = [], {}
    for p in parcels.values():
        ev = p["events"]
        stages = [e["stage"] for e in ev]
        cur = max(stages, key=STAGE_ORDER.index) if stages else None
        last = max((e["date"] for e in ev if e["date"]), default=None)
        days = (as_of - date.fromisoformat(last)).days if last else None
        blockers = []
        if cur:
            reach = STAGE_ORDER.index(cur)
            blockers = [m for m in MANDATORY if STAGE_ORDER.index(m) < reach and m not in stages]
        flags = []
        if blockers:
            flags.append("STAGE_ORDER_VIOLATION")
        if "EXEMPTION" in stages and ({"AWARD", "PAYMENT"} & set(stages)):
            flags.append("EXEMPTION_CONFLICT")
        stalled = cur in STALL_PAIRS and days is not None and days >= a.stalled_days
        if stalled:
            flags.append("STALLED")
        rec = {"parcel_uid": p["parcel_uid"], "village": p["village"], "block": p["block"], "current_stage": cur,
               "last_event_date": last, "days_in_stage": days, "next_expected": NEXT_AFTER.get(cur) if cur else "SEC_3_1",
               "blockers": blockers, "flags": flags, "stall_kind": STALL_PAIRS.get(cur) if stalled else None,
               "n_events": len(ev), "events": ev}
        out.append(rec)
        key = (p["village"], cur or "NO_EVENT")
        matrix[key] = matrix.get(key, 0) + 1
    if a.only_stalled:
        out = [r for r in out if "STALLED" in r["flags"]]
    funnel = {s: sum(1 for r in out if r["current_stage"] and STAGE_ORDER.index(r["current_stage"]) >= STAGE_ORDER.index(s))
              for s in MANDATORY + ["PAYMENT", "MUTATION"]}
    ev = [Evidence(kind="sql", ref="acquisition_event", excerpt="per-parcel ordered acquisition events")]
    n_stalled = sum(1 for r in out if "STALLED" in r["flags"])
    claims = [ClaimSeed(kind="kpi", subject="scope", field="n_stalled", value=n_stalled, evidence=ev,
                        checks=["lifecycle_recount"], context={"scope": a.model_dump(), "flag": "STALLED"}),
              ClaimSeed(kind="kpi", subject="scope", field="n_parcels", value=len(parcels), evidence=ev,
                        checks=["count_matches_rows"], context={"rows": len(parcels)})]
    for r in out:
        for f in r["flags"]:
            claims.append(ClaimSeed(kind="discrepancy", subject=r["parcel_uid"], field=f, value=True, evidence=[
                Evidence(kind="document", ref=f"document:{e['document_id']}", document_id=e["document_id"],
                         excerpt=f"{e['stage']} {e['date']}") for e in r["events"] if e["document_id"]][:4],
                checks=["stage_order_legal" if f == "STAGE_ORDER_VIOLATION" else "lifecycle_recount"],
                context={"events": r["events"], "flag": f, "scope": {"parcel_uids": [r["parcel_uid"]]},
                         "stalled_days": a.stalled_days, "as_of": as_of.isoformat()}))
    return ToolResult(data={"parcels": out, "village_stage_matrix": [{"village": k[0], "stage": k[1], "n": v}
                                                                    for k, v in sorted(matrix.items())],
                            "funnel": funnel}, evidence=ev, n_rows=len(out), confidence=0.85,
                      caveats=["events come from extracted documents; missing events may be unextracted pages"],
                      summary=f"{len(out)} parcels, {n_stalled} stalled", claims=claims[:60])


# ------------------------------------------------------------------------------------------ match_parcels
class MatchRow(_In):
    village: str
    survey_no: str
    sub_div: str | None = None
    extent_ha: float | None = None
    block: int | None = None


class MatchIn(_In):
    rows: list[MatchRow] = Field(min_length=1, max_length=500)


def match_parcels(a: MatchIn, ctx) -> ToolResult:
    """INTERIM matcher (T5.1 steps 1-3 + block bonus). TODO(P5): pipeline.match adds OCR-confusion variants."""
    from pipeline.gis.aliases import load_aliases
    al = load_aliases()
    out, ev = [], []
    for r in a.rows:
        vil = al.canonical_village(r.village) or r.village
        sub = (r.sub_div or "").upper().replace(" ", "")
        kide = f"{r.survey_no}/{sub}" if sub else r.survey_no
        cands = query(ctx.kg, "SELECT p.parcel_uid, p.kide, p.sub_div, p.block_id, p.area_ha_gis FROM parcel p "
                              "JOIN village v ON v.id = p.village_id WHERE v.name = %s AND p.survey_no = %s "
                              "ORDER BY p.kide", (vil, r.survey_no))
        scored = []
        for c in cands:
            if c["kide"] == kide:
                s, why = 1.0, "exact KIDE"
            elif (c["sub_div"] or "") == sub:
                s, why = 0.9, "survey + subdivision"
            elif not sub:
                s, why = 0.6, "parent survey (survey_level)"
            else:
                continue
            if r.block is not None and c["block_id"] == r.block:
                s, why = min(1.0, s + 0.05), why + " + block"
            if r.extent_ha and c["area_ha_gis"]:
                rel = abs(r.extent_ha - float(c["area_ha_gis"])) / max(float(c["area_ha_gis"]), 1e-6)
                s -= min(0.2, rel / 2)
                why += f"; extent diff {rel:.0%}"
            scored.append({"parcel_uid": c["parcel_uid"], "score": round(s, 3), "reason": why})
        scored.sort(key=lambda x: (-x["score"], x["parcel_uid"]))
        top = scored[:3]
        margin = top[0]["score"] - top[1]["score"] if len(top) > 1 else 1.0
        status = "matched" if top and top[0]["score"] >= 0.8 and margin >= 0.1 else ("ambiguous" if top else "unmatched")
        out.append({**r.model_dump(), "village": vil, "status": status,
                    "parcel_uid": top[0]["parcel_uid"] if status == "matched" else None, "candidates": top})
        ev.append(Evidence(kind="layer_feature", ref=f"parcel:{top[0]['parcel_uid']}" if top else f"parcel:none:{vil}|{kide}",
                           excerpt=status))
    n = sum(1 for o in out if o["status"] == "matched")
    return ToolResult(data={"matches": out}, evidence=ev[:50], n_rows=len(out), confidence=0.8,
                      caveats=["interim matcher: no OCR-confusion variants yet", "never matches across villages"],
                      summary=f"{n}/{len(out)} matched")


# ------------------------------------------------------------------------------------------ paper_vs_planet
CATEGORIES = ["PV1_CLASSIFICATION_CONFLICT", "PV3_POST_POSSESSION_ACTIVITY", "PV4_IDLE_LAND_BANK",
              "PV5_PRE_NOTIFICATION_CHANGE", "EXTENT_MISMATCH", "COMPENSATION_MISMATCH", "STALLED",
              "STAGE_ORDER_VIOLATION", "EXEMPTION_CONFLICT"]


class PvpIn(Scope):
    categories: list[Literal[tuple(CATEGORIES)]] = Field(default_factory=list,  # type: ignore[valid-type]
                                                         description="empty = all interim categories")
    min_confidence: float = Field(0.0, ge=0, le=1)
    limit: int = Field(200, ge=1, le=2000, description="max findings returned (highest confidence first)")


def paper_vs_planet(a: PvpIn, ctx) -> ToolResult:
    """INTERIM findings engine. TODO(P5): replace the body with pipeline.discrepancy.run(scope, categories)."""
    try:
        from pipeline.discrepancy import run as p5_run  # type: ignore[attr-defined]
    except ImportError:
        p5_run = None
    if p5_run is not None:
        return p5_run(a, ctx)
    cats = set(a.categories or CATEGORIES)
    scope, params = _scope_sql(a)
    findings = []
    base = ("FROM parcel pa JOIN village v ON v.id = pa.village_id ")
    facts = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, pa.area_ha_gis, "
                          "max(f.value_num) FILTER (WHERE f.fact_type='extent_ha') AS extent_ha, "
                          "max(f.value_num) FILTER (WHERE f.fact_type='extent_ac') AS extent_ac, "
                          "max(f.value_num) FILTER (WHERE f.fact_type='amount_rs') AS amount_rs, "
                          "max(f.value_num) FILTER (WHERE f.fact_type='rate_per_acre') AS rate, "
                          "max(f.value_text) FILTER (WHERE f.fact_type='classification') AS classification, "
                          "max(f.extraction_id) FILTER (WHERE f.fact_type='extent_ha') AS extraction_id "
                          + base + "LEFT JOIN parcel_fact f ON f.parcel_uid = pa.parcel_uid "
                          f"WHERE {scope} GROUP BY 1,2,3,4 ORDER BY 1", params)
    ext_bbox = {r["id"]: r for r in query(ctx.kg, "SELECT id, page_id, page_no, document_id, bbox FROM extraction "
                                                  "WHERE id = ANY(%s)", ([f["extraction_id"] for f in facts if f["extraction_id"]],))}

    def paper_ev(f):
        e = ext_bbox.get(f["extraction_id"])
        return [Evidence(kind="page_bbox", ref=f"extraction:{e['id']}", page=e["page_no"], document_id=e["document_id"],
                         bbox=e["bbox"], excerpt="document row")] if e else []

    for f in facts:
        pe = paper_ev(f)
        if "EXTENT_MISMATCH" in cats and f["extent_ha"] and f["area_ha_gis"]:
            rel = abs(float(f["extent_ha"]) - float(f["area_ha_gis"])) / float(f["area_ha_gis"])
            if rel > 0.05:
                findings.append(_finding("EXTENT_MISMATCH", f, pe, {"doc_ha": f["extent_ha"], "gis_ha": f["area_ha_gis"],
                                                                    "rel_diff": round(rel, 4)}, 0.8,
                                         ["document extent vs geodesic FMB area; FMB polygons can overlap (D-023)"],
                                         checks=["extent_vs_gis"]))
        if "COMPENSATION_MISMATCH" in cats and f["amount_rs"] and f["rate"] and f["extent_ac"]:
            exp_amt = float(f["extent_ac"]) * float(f["rate"])
            if abs(float(f["amount_rs"]) - exp_amt) > max(100, 0.005 * exp_amt):
                findings.append(_finding("COMPENSATION_MISMATCH", f, pe, {"amount_rs": f["amount_rs"], "extent_ac": f["extent_ac"],
                                                                          "rate_per_acre": f["rate"], "expected_rs": round(exp_amt)},
                                         0.85, ["rate taken from the same block's award/DLPNC"], checks=["compensation_arith"]))
    if cats & {"PV1_CLASSIFICATION_CONFLICT"}:
        rows = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, f.value_text AS classification, "
                             "f.extraction_id, count(*) FILTER (WHERE s.state='irrigated_multi') AS n_irrig, "
                             "count(*) FILTER (WHERE s.state='cropped') AS n_crop, count(s.*) AS n_seasons "
                             + base + "JOIN parcel_fact f ON f.parcel_uid = pa.parcel_uid AND f.fact_type='classification' "
                             "LEFT JOIN parcel_season s ON s.parcel_uid = pa.parcel_uid AND s.ag_year BETWEEN 2019 AND 2021 "
                             f"AND s.season <> 'annual' WHERE {scope} GROUP BY 1,2,3,4,5", params)
        for r in rows:
            cls = (r["classification"] or "").lower()
            bad = (cls == "wet" and r["n_irrig"] < 2) or (cls == "dry" and r["n_irrig"] >= 2) or \
                  (cls == "poramboke" and r["n_crop"] >= 2)
            if bad:
                findings.append(_finding("PV1_CLASSIFICATION_CONFLICT", {**r, "extraction_id": r["extraction_id"]},
                                         paper_ev({"extraction_id": r["extraction_id"]}) if r["extraction_id"] in ext_bbox else [],
                                         {"classification": cls, "irrigated_multi_seasons": r["n_irrig"],
                                          "cropped_seasons": r["n_crop"], "seasons_2019_2021": r["n_seasons"]}, 0.6,
                                         [FIELD, "3 ag-years before 3(1) (2019-2021)"], checks=["satellite_counts_recount"]))
    if "PV3_POST_POSSESSION_ACTIVITY" in cats:
        rows = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, d.did, d.ci_lo, d.ci_hi, "
                             "d.farmland_like_p, d.event_date, d.n_post " + base +
                             "JOIN parcel_did d ON d.parcel_uid = pa.parcel_uid AND d.event='t_possession' "
                             f"AND d.metric='vigour_z' WHERE {scope} AND d.farmland_like_p >= 0.7 AND d.ci_lo <= 0 "
                             "ORDER BY d.farmland_like_p DESC", params)
        for r in rows:
            findings.append(_finding("PV3_POST_POSSESSION_ACTIVITY", r, [], {
                "did": r["did"], "ci": [r["ci_lo"], r["ci_hi"]], "farmland_like_p": r["farmland_like_p"],
                "possession_date": r["event_date"], "n_post_seasons": r["n_post"]},
                min(0.8, float(r["farmland_like_p"])),
                [FIELD, "difference-in-differences vs non-acquired control farmland (D-035)",
                 "few post-possession seasons" if (r["n_post"] or 0) < 2 else ""], checks=["did_ci_consistent"]))
    if "PV4_IDLE_LAND_BANK" in cats:
        rows = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, pa.area_ha_gis, pa.dist_major_road_m, "
                             "pa.dist_substation_m, max(e.event_date) AS possession_date, "
                             "count(s.*) FILTER (WHERE s.state='cleared_or_built') AS n_built " + base +
                             "JOIN acquisition_event e ON e.parcel_uid = pa.parcel_uid AND e.stage='POSSESSION' "
                             "LEFT JOIN parcel_season s ON s.parcel_uid = pa.parcel_uid AND s.ag_year >= 2024 "
                             f"WHERE {scope} GROUP BY 1,2,3,4,5,6 HAVING max(e.event_date) <= %s::date - 180 "
                             "AND count(s.*) FILTER (WHERE s.state='cleared_or_built') = 0", [*params, today().isoformat()])
        for r in rows:
            findings.append(_finding("PV4_IDLE_LAND_BANK", r, [], {"area_ha": r["area_ha_gis"],
                                     "possession_date": r["possession_date"], "dist_major_road_m": r["dist_major_road_m"],
                                     "dist_substation_m": r["dist_substation_m"]}, 0.7, [FIELD], checks=[]))
    if "PV5_PRE_NOTIFICATION_CHANGE" in cats:
        rows = query(ctx.kg, "SELECT pa.parcel_uid, v.name AS village, pa.block_id, w1.dominant_state AS pre_state, "
                             "w2.dominant_state AS pending_state " + base +
                             "JOIN parcel_event_window w1 ON w1.parcel_uid = pa.parcel_uid AND w1.window_name='pre_notification' "
                             "JOIN parcel_event_window w2 ON w2.parcel_uid = pa.parcel_uid AND w2.window_name='pending' "
                             f"WHERE {scope} AND ((w1.dominant_state='bare_fallow' AND w2.dominant_state IN "
                             "('perennial_veg','cleared_or_built','irrigated_multi')))", params)
        for r in rows:
            findings.append(_finding("PV5_PRE_NOTIFICATION_CHANGE", r, [], {"pre_notification": r["pre_state"],
                                     "pending": r["pending_state"]}, 0.55, [FIELD, "window dates may use fallback dates"],
                                     checks=[]))
    if cats & {"STALLED", "STAGE_ORDER_VIOLATION", "EXEMPTION_CONFLICT"}:
        lc = lifecycle_status(LifecycleIn(villages=a.villages, blocks=a.blocks, parcel_uids=a.parcel_uids), ctx)
        for p in lc.data["parcels"]:
            for fl in p["flags"]:
                if fl in cats:
                    findings.append(_finding(fl, p, [Evidence(kind="document", ref=f"document:{e['document_id']}",
                                                              document_id=e["document_id"], excerpt=f"{e['stage']} {e['date']}")
                                                     for e in p["events"] if e["document_id"]][:4],
                                             {"current_stage": p["current_stage"], "days_in_stage": p["days_in_stage"],
                                              "blockers": p["blockers"]}, 0.8, [],
                                             checks=["stage_order_legal" if fl == "STAGE_ORDER_VIOLATION" else "lifecycle_recount"],
                                             ctx_extra={"events": p["events"], "flag": fl,
                                                        "scope": {"parcel_uids": [p["parcel_uid"]]}}))
    findings = [f for f in findings if f["confidence"] >= a.min_confidence]
    n_all = len(findings)
    findings = sorted(findings, key=lambda f: -f["confidence"])[:a.limit]
    claims = [ClaimSeed(kind="finding", subject=f["parcel_uid"], field=f["category"], value=f["observed"],
                        evidence=f["evidence"], checks=f["checks"], context={**f["observed"], **f.get("ctx", {}),
                                                                             "parcel_uid": f["parcel_uid"]},
                        confidence=f["confidence"]) for f in findings]
    by_cat: dict[str, int] = {}
    for f in findings:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
    claims.append(ClaimSeed(kind="kpi", subject="scope", field="n_findings", value=len(findings),
                            checks=["count_matches_rows"], context={"rows": len(findings)}))
    for f in findings:
        f.pop("ctx", None)
        f["evidence"] = [e.model_dump() for e in f["evidence"]]
    return ToolResult(data={"findings": findings, "by_category": by_cat}, n_rows=len(findings), confidence=0.7,
                      evidence=[Evidence(kind="sql", ref="paper_vs_planet:interim", excerpt="interim rules")],
                      caveats=["INTERIM rules (P5 findings engine pending)", FIELD], degraded=True,
                      summary=f"{len(findings)} of {n_all} findings: {by_cat}", claims=claims[:80])


def _finding(cat, row, ev, observed, conf, caveats, checks, ctx_extra=None):
    return {"category": cat, "parcel_uid": row["parcel_uid"], "village": row.get("village"), "block": row.get("block_id", row.get("block")),
            "observed": {k: (v if not hasattr(v, "isoformat") else v.isoformat()) for k, v in observed.items()},
            "evidence": ev, "confidence": round(float(conf), 3), "caveats": [c for c in caveats if c],
            "verdict": None, "checks": checks, "ctx": ctx_extra or {}}


# ------------------------------------------------------------------------------------------ evidence_pack
class EvidencePackIn(_In):
    parcel_uid: str
    category: str | None = None


def evidence_pack(a: EvidencePackIn, ctx) -> ToolResult:
    """INTERIM EvidencePack assembly (inset, paper rows with bbox, timeline events, NDVI/BSI series, chip refs).
    TODO(P5): conform to schemas/evidence/EvidencePack.json and add critic challenge + ledger hash at present."""
    geo = query(ctx.kg, "SELECT ST_AsGeoJSON(geom, 6)::json AS geom, v.name AS village, p.area_ha_gis FROM parcel p "
                        "JOIN village v ON v.id = p.village_id WHERE parcel_uid = %s", (a.parcel_uid,))
    if not geo:
        raise ToolError("not_found", f"parcel {a.parcel_uid} not found")
    paper = query(ctx.kg, "SELECT f.fact_type, f.value_num, f.value_text, f.unit, x.document_id, x.page_no, x.bbox, x.id AS extraction_id "
                          "FROM parcel_fact f JOIN extraction x ON x.id = f.extraction_id WHERE f.parcel_uid = %s "
                          "AND f.fact_type NOT IN ('owner','patta_no') ORDER BY f.fact_type", (a.parcel_uid,))
    events = query(ctx.kg, "SELECT stage, event_date, document_id FROM acquisition_event WHERE parcel_uid = %s "
                           "ORDER BY event_date", (a.parcel_uid,))
    series = query(ctx.kg, "SELECT date, round(ndvi::numeric,4) AS ndvi, round(bsi::numeric,4) AS bsi, valid_frac >= 0.5 AS valid "
                           "FROM parcel_obs WHERE parcel_uid = %s ORDER BY date", (a.parcel_uid,))
    ev = [Evidence(kind="page_bbox", ref=f"extraction:{p['extraction_id']}", page=p["page_no"], document_id=p["document_id"],
                   bbox=p["bbox"], excerpt=f"{p['fact_type']}={p['value_num'] if p['value_num'] is not None else p['value_text']}")
          for p in paper]
    pack = {"parcel_uid": a.parcel_uid, "category": a.category, "inset": {"type": "Feature", "geometry": geo[0]["geom"],
            "properties": {"parcel_uid": a.parcel_uid, "village": geo[0]["village"]}},
            "paper_claims": paper, "timeline": {"events": events, "series": series},
            "chips": [], "checks": [], "verdict": None, "caveats": [FIELD]}
    return ToolResult(data=pack, evidence=ev + [Evidence(kind="timeseries", ref=f"parcel_obs:{a.parcel_uid}",
                                                         excerpt=f"{len(series)} obs")],
                      confidence=0.8, summary=f"evidence pack {a.parcel_uid}: {len(paper)} paper facts, "
                      f"{len(events)} events, {len(series)} obs", caveats=["interim evidence pack"])


def land_tools() -> list[Tool]:
    return [
        Tool("lifecycle_status", "Per-parcel acquisition lifecycle: ordered events, current stage, days in stage, next "
             "expected stage, blockers, STALLED / STAGE_ORDER_VIOLATION / EXEMPTION_CONFLICT flags, village x stage "
             "matrix and funnel.", LifecycleIn, lifecycle_status, timeout_s=30),
        Tool("match_parcels", "Match document rows (village, survey_no, sub_div, extent) to FMB parcels with top-3 "
             "candidates; never across villages.", MatchIn, match_parcels, timeout_s=30),
        Tool("paper_vs_planet", "SLOW interim rules recomputed live; prefer findings_query (precomputed findings). "
             "Use only when findings_query has no category for the question. Findings for a scope: PV1 classification conflict, PV3 post-possession activity (DiD vs "
             "controls), PV4 idle land, PV5 pre-notification change, and document rules EXTENT_MISMATCH, "
             "COMPENSATION_MISMATCH, STALLED, STAGE_ORDER_VIOLATION, EXEMPTION_CONFLICT.", PvpIn, paper_vs_planet,
             timeout_s=60),
    ]
