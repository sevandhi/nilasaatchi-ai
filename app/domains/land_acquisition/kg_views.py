"""Tools over the P5 knowledge-graph views: findings_query, parcel_timeline, satellite_summary, evidence_pack.

All reads run as agent_ro (read only, 5 s statement timeout). Owner names never leave these tools (the finding
table stores none; evidence_pack masks owners via pipeline.findings.evidence_pack.mask_owner).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.agent.state import Evidence
from app.domains.land_acquisition.tools import FIELD, Scope, _In
from app.tools.db import query
from app.tools.registry import ClaimSeed, Tool, ToolError, ToolResult

CATEGORIES = ["COMPENSATION_MISMATCH", "EXTENT_MISMATCH", "PV1_CLASSIFICATION_CONFLICT", "PV3_POST_POSSESSION_ACTIVITY",
              "PV4_IDLE_LAND_BANK", "DOC_VERSION_CONFLICT", "FMB_QUALITY", "EXTRACTION_ERROR", "STALLED"]
SEV_RANK = "CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
ORDER = {"severity": f"{SEV_RANK}, f.confidence DESC, f.id",
         "confidence": "f.confidence DESC, f.id",
         "magnitude": "abs(coalesce((f.metrics->>'diff_rs')::float, (f.metrics->>'diff_ha')::float, "
                      "(f.metrics->>'did')::float, (f.metrics->>'idle_ha_sum')::float, 0)) DESC, f.id"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------ findings_query
class FindingsIn(Scope):
    categories: list[str] = Field(default_factory=list, description="finding categories: " + ", ".join(CATEGORIES))
    severities: list[Literal["high", "medium", "low"]] = Field(default_factory=list)
    signal: str | None = Field(None, description="PV3 metrics.signal: vigour_drop | still_farmed_lead")
    min_confidence: float = Field(0.0, ge=0, le=1)
    metric_max: dict[str, float] = Field(default_factory=dict, description="upper bounds on numeric metrics, e.g. "
                                         "{\"min_substation_km\": 4} or {\"min_major_road_km\": 2} (PV4)")
    metric_min: dict[str, float] = Field(default_factory=dict, description="lower bounds, e.g. {\"diff_ha\": 1}")
    order_by: Literal["severity", "confidence", "magnitude"] = "severity"
    limit: int = Field(25, ge=1, le=500)


def _finding_checks(cat: str, m: dict) -> tuple[list[str], dict]:
    if cat == "COMPENSATION_MISMATCH":
        return ["compensation_arith", "finding_reread"], {"amount_rs": m.get("amount_rs"), "extent_ac": m.get("extent_ac"),
                                                          "rate_per_acre": m.get("rate_per_ac")}
    if cat == "EXTENT_MISMATCH":
        return ["extent_vs_gis", "finding_reread"], {"doc_ha": m.get("doc_ha"), "gis_ha": m.get("gis_ha")}
    if cat == "PV3_POST_POSSESSION_ACTIVITY":
        return ["did_ci_contains", "finding_reread"], {"did": m.get("did"), "ci": m.get("ci"), "signal": m.get("signal")}
    return ["finding_reread"], {}


def findings_query(a: FindingsIn, ctx) -> ToolResult:
    w, p = ["f.status <> 'dismissed'"], []
    for col, val in (("f.category", a.categories), ("f.severity", a.severities), ("f.village", a.villages),
                     ("f.block_id", a.blocks)):
        if val:
            w.append(f"{col} = ANY(%s)")
            p.append(val)
    if a.parcel_uids:
        w.append("(f.parcel_uid = ANY(%s) OR f.metrics->'parcels' ?| %s)")
        p += [a.parcel_uids, a.parcel_uids]
    if a.signal:
        w.append("f.metrics->>'signal' = %s")
        p.append(a.signal)
    if a.min_confidence:
        w.append("f.confidence >= %s")
        p.append(a.min_confidence)
    for bounds, op in ((a.metric_max, "<="), (a.metric_min, ">=")):
        for k, v in bounds.items():
            w.append(f"(f.metrics->>%s)::float {op} %s")     # key is a bound parameter, never interpolated
            p += [k, v]
    where = " AND ".join(w)
    rows = query(ctx.kg, f"SELECT f.id AS finding_id, f.category, f.severity, round(f.confidence::numeric,3) AS confidence, "
                         f"f.parcel_uid, f.village, f.block_id, f.title, f.evidence_level, f.metrics, f.caveats "
                         f"FROM finding f WHERE {where} ORDER BY {ORDER[a.order_by]} LIMIT %s", p + [a.limit])
    total = query(ctx.kg, f"SELECT count(*) AS n FROM finding f WHERE {where}", p)[0]["n"]
    claims = []
    for r in rows:
        m = r["metrics"] or {}
        checks, cctx = _finding_checks(r["category"], m)
        claims.append(ClaimSeed(kind="finding", subject=r["parcel_uid"] or f"{r['village']}|block {r['block_id']}",
                                field=r["category"], value={k: v for k, v in m.items() if not isinstance(v, (list, dict))
                                                            or k == "ci"},
                                evidence=[Evidence(kind="sql", ref=f"finding:{r['finding_id']}", excerpt=r["title"][:160])],
                                checks=checks, confidence=float(r["confidence"] or 0.5),
                                context={**cctx, "finding_id": r["finding_id"], "parcel_uid": r["parcel_uid"]}))
    claims.append(ClaimSeed(kind="kpi", subject="scope", field="n_findings_total", value=total, checks=["rerun_sql"],
                            context={"sql": f"SELECT count(*) AS n FROM finding f WHERE {where}", "params": p, "row": 0,
                                     "column": "n"}))
    for r in rows:   # scalar metrics become table columns (the workspace shows scalar cells only)
        m = r.pop("metrics") or {}
        r.update({k: v for k, v in m.items() if not isinstance(v, (list, dict)) and k not in r})
    return ToolResult(data={"findings": rows, "total_matching": total}, n_rows=len(rows), confidence=0.85,
                      evidence=[Evidence(kind="sql", ref="finding", excerpt=f"{total} findings match")],
                      caveats=[FIELD], summary=f"{len(rows)} of {total} findings ({', '.join(a.categories) or 'all'})",
                      claims=claims)


# ------------------------------------------------------------------------------------------ parcel_timeline
class TimelineIn(_In):
    parcel_uid: str = Field(description="'<Village>|<KIDE>', e.g. 'Melathattaparai|233'")


def parcel_timeline(a: TimelineIn, ctx) -> ToolResult:
    lc = query(ctx.kg, "SELECT parcel_uid, village, kide, block_id, area_ha_gis, n_events, current_stage, stage_entered, "
                       "days_in_stage, next_expected_stage, missing_mandatory, has_exemption, exemption_conflict, "
                       "evidence_level, stalled_flag FROM v_parcel_lifecycle WHERE parcel_uid = %s", (a.parcel_uid,),
               readonly_role=False)   # trusted template; the view reads *_link tables agent_ro cannot
    if not lc:
        raise ToolError("not_found", f"parcel {a.parcel_uid} not in v_parcel_lifecycle")
    ev = query(ctx.kg, "SELECT stage, sub_stage, event_date, date_precision, document_id, amount, link_kind, "
                       "round(match_score::numeric,3) AS match_score FROM v_parcel_events WHERE parcel_uid = %s "
                       "ORDER BY event_date NULLS LAST, stage_ord", (a.parcel_uid,), readonly_role=False)
    sql = "SELECT count(*) AS n FROM v_parcel_events WHERE parcel_uid = %s"
    claims = [ClaimSeed(kind="kpi", subject=a.parcel_uid, field="n_events", value=len(ev), checks=["rerun_sql"],
                        context={"sql": sql, "params": [a.parcel_uid], "row": 0, "column": "n", "trusted": True}),
              ClaimSeed(kind="entity", subject=a.parcel_uid, field="current_stage", value=lc[0]["current_stage"],
                        checks=["rerun_sql"], context={"sql": "SELECT current_stage FROM v_parcel_lifecycle WHERE "
                                                              "parcel_uid = %s", "params": [a.parcel_uid], "row": 0,
                                                       "column": "current_stage", "trusted": True})]
    return ToolResult(data={"parcel": lc[0], "events": ev}, n_rows=len(ev), confidence=0.85,
                      evidence=[Evidence(kind="document", ref=f"document:{e['document_id']}", document_id=e["document_id"],
                                         excerpt=f"{e['stage']} {e['event_date']}") for e in ev if e["document_id"]][:10],
                      summary=f"{a.parcel_uid}: stage {lc[0]['current_stage']}, {len(ev)} events", claims=claims)


# ------------------------------------------------------------------------------------------ satellite_summary
class SatSummaryIn(_In):
    parcel_uid: str
    from_year: int = Field(2019, ge=2015, le=2030)


def satellite_summary(a: SatSummaryIn, ctx) -> ToolResult:
    did = query(ctx.kg, "SELECT event, metric, season_scope, event_date, n_pre, n_post, round(did::numeric,3) AS did, "
                        "round(ci_lo::numeric,3) AS ci_lo, round(ci_hi::numeric,3) AS ci_hi, "
                        "round(farmland_like_p::numeric,3) AS farmland_like_p FROM parcel_did WHERE parcel_uid = %s "
                        "ORDER BY event, metric, season_scope", (a.parcel_uid,))
    seasons = query(ctx.kg, "SELECT ag_year, season, state, round(p_state::numeric,3) AS p_state FROM parcel_season "
                            "WHERE parcel_uid = %s AND ag_year >= %s AND season <> 'annual' ORDER BY ag_year, season",
                    (a.parcel_uid, a.from_year))
    if not seasons and not did:
        raise ToolError("not_found", f"no satellite record for {a.parcel_uid}")
    counts: dict[str, int] = {}
    for s in seasons:
        counts[s["state"]] = counts.get(s["state"], 0) + 1
    from pipeline.findings.evidence_pack import chip_refs
    chips = chip_refs(a.parcel_uid, [], False)
    sql = ("SELECT count(*) FILTER (WHERE state = 'cropped' OR state = 'irrigated_multi') AS n_crop FROM parcel_season "
           "WHERE parcel_uid = %s AND ag_year >= %s AND season <> 'annual'")
    n_crop = counts.get("cropped", 0) + counts.get("irrigated_multi", 0)
    claims = [ClaimSeed(kind="kpi", subject=a.parcel_uid, field="n_crop", value=n_crop, unit="seasons",
                        checks=["rerun_sql"], context={"sql": sql, "params": [a.parcel_uid, a.from_year], "row": 0,
                                                       "column": "n_crop"},
                        evidence=[Evidence(kind="timeseries", ref=f"parcel_season:{a.parcel_uid}",
                                           excerpt=f"{len(seasons)} seasons")])]
    for d in did[:2]:
        claims.append(ClaimSeed(kind="kpi", subject=a.parcel_uid, field=f"did_{d['metric']}_{d['season_scope']}",
                                value=d["did"], checks=["did_ci_contains"],
                                context={"did": d["did"], "ci": [d["ci_lo"], d["ci_hi"]]}))
    return ToolResult(data={"parcel_uid": a.parcel_uid, "state_counts": counts, "seasons": seasons, "did": did,
                            "chips": {"cached": chips.get("cached", [])[:6]}},
                      n_rows=len(seasons), confidence=0.75,
                      evidence=[Evidence(kind="satellite_chip", ref=c) for c in chips.get("cached", [])[:3]] +
                               [Evidence(kind="timeseries", ref=f"parcel_did:{a.parcel_uid}", excerpt=f"{len(did)} DiD rows")],
                      caveats=[FIELD, "Sentinel-2 10 m; small parcels are mixed pixels"],
                      summary=f"{a.parcel_uid}: {counts}; {len(did)} DiD rows", claims=claims)


# ------------------------------------------------------------------------------------------ evidence_pack
class EvidencePackIn(_In):
    parcel_uid: str | None = Field(description="parcel of the finding (its top finding unless finding_id is given); "
                                   "null for block/village-level findings when finding_id is given")
    finding_id: int | None = Field(None, description="finding id from findings_query (overrides parcel_uid)")
    category: str | None = None


def evidence_pack(a: EvidencePackIn, ctx) -> ToolResult:
    fid = a.finding_id
    if fid is None and not a.parcel_uid:
        raise ToolError("bad_args", "evidence_pack needs parcel_uid or finding_id")
    if fid is None:
        try:
            r = query(ctx.kg, f"SELECT f.id FROM finding f WHERE f.parcel_uid = %s AND (%s::text IS NULL OR "
                              f"f.category = %s) ORDER BY f.category IN ('FMB_QUALITY','EXTRACTION_ERROR'), {SEV_RANK}, "
                              f"f.confidence DESC LIMIT 1",
                      (a.parcel_uid, a.category, a.category))
        except Exception:  # noqa: BLE001 - KG without the P5 finding table (fixture KG)
            r = []
        if not r:   # no finding: parcel-level pack (documents, events, NDVI/BSI series)
            from app.domains.land_acquisition.tools import EvidencePackIn as _PIn
            from app.domains.land_acquisition.tools import evidence_pack as _parcel_pack
            return _parcel_pack(_PIn(parcel_uid=str(a.parcel_uid), category=a.category), ctx)
        fid = r[0]["id"]
    import psycopg

    from pipeline.findings.evidence_pack import evidence_pack as _pack
    with psycopg.connect(ctx.kg, connect_timeout=5) as conn:
        conn.execute("SET statement_timeout = 5000")
        try:
            pack = _pack(int(fid), conn=conn)
        except KeyError as e:
            raise ToolError("not_found", str(e)) from e
    import json
    pack = json.loads(json.dumps(pack, default=str))
    ev = [Evidence(kind="page_bbox", ref=f"extraction:{p['extraction_id']}", page=p.get("page_no"),
                   document_id=p.get("document_id"), bbox=p.get("bbox"),
                   excerpt=str({k: v for k, v in (p.get("values") or {}).items() if k != "owner"})[:160])
          for p in pack.get("paper", []) if p.get("extraction_id")]
    m = pack.get("metrics") or {}
    checks, cctx = _finding_checks(pack["category"], m)
    claim = ClaimSeed(kind="finding", subject=pack["parcel_uid"] or f"{pack['village']}|block {pack['block_id']}",
                      field=pack["category"], value={k: v for k, v in m.items() if not isinstance(v, (list, dict)) or k == "ci"},
                      evidence=ev[:4], checks=checks, confidence=float(pack.get("confidence") or 0.5),
                      context={**cctx, "finding_id": pack["finding_id"], "parcel_uid": pack["parcel_uid"]})
    planet = pack.get("planet") or {}
    return ToolResult(data=pack, evidence=ev + [Evidence(kind="timeseries", ref=f"parcel_did:{pack['parcel_uid']}")],
                      confidence=0.8, caveats=list(pack.get("caveats") or []) + [FIELD],
                      summary=f"evidence pack finding {fid} ({pack['category']}): {len(ev)} paper rows, "
                              f"{len(planet.get('season_states') or [])} seasons", claims=[claim])


def kg_view_tools() -> list[Tool]:
    return [
        Tool("findings_query", "Filter precomputed paper-vs-planet findings (compensation / extent mismatches, "
             "post-possession activity PV3, idle land PV4, classification conflict PV1, document drift "
             "DOC_VERSION_CONFLICT) by category, village, block, severity, parcel; ranked. Output: data.findings[] "
             "(finding_id, category, severity, parcel_uid, village, block_id, metrics) and data.total_matching.",
             FindingsIn,
             findings_query, timeout_s=15),
        Tool("parcel_timeline", "One parcel's acquisition lifecycle: ordered events with documents, current stage, "
             "days in stage, missing mandatory stages, stall flag.", TimelineIn, parcel_timeline, timeout_s=15),
        Tool("satellite_summary", "One parcel's satellite record: per-season land-use states, post-possession DiD vs "
             "control farms, cached chip paths.", SatSummaryIn, satellite_summary, timeout_s=15),
        Tool("evidence_pack", "Evidence pack for one finding (paper rows with page bbox, satellite DiD/seasons, chips, "
             "metrics). Give finding_id, or parcel_uid for its top finding.", EvidencePackIn, evidence_pack,
             timeout_s=20),
    ]
