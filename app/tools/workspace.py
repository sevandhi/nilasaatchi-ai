"""compose_workspace: deterministic WorkspaceSpec builder from a finished RunState (no model calls).

Heuristics are generic: tables = the main list-of-records in each step output; map = geometries for any
``parcel_uid`` values (and uploaded polygons); charts = counts by the first categorical column; KPIs = verified
kpi claims; every table/KPI links its claim ids; verification, SQL and route trace are copied from the state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import (
    ChartSpec,
    Column,
    EvidenceGroup,
    Kpi,
    LegendItem,
    MapLayer,
    MapSpec,
    RunState,
    SqlEntry,
    TableSpec,
    TimelineEvent,
    TimelineSpec,
    TimeSlider,
    VerificationClaim,
    VerificationSpec,
    VerificationSummary,
    WorkspaceSpec,
    now_iso,
)
from app.tools.registry import Tool, ToolResult

LIST_KEYS = ("findings", "parcels", "rows", "matches", "streaks", "hits", "seasons", "village_stage_matrix")
CAT_KEYS = ("category", "current_stage", "status", "state", "dominant_state", "stage", "doc_type", "village")
COLOR_KEYS = ("category", "current_stage", "state", "status", "dominant_state")
PALETTE = ["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
STATUS = {"ACCEPT": "verified", "DOWNGRADE": "downgraded", "REVIEW": "review", "REROUTE": "review", None: "review"}


def _main_list(data: Any) -> tuple[str, list[dict]] | None:
    if not isinstance(data, dict):
        return None
    for k in LIST_KEYS:
        v = data.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return k, v
    return None


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _coltype(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-":
        return "date"
    return "string"


def _flatten(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if _scalar(v):
            out[k] = v
        elif isinstance(v, dict) and k in ("observed",):
            for kk, vv in v.items():
                if _scalar(vv):
                    out[kk] = vv
        elif isinstance(v, list) and all(_scalar(x) for x in v) and k in ("flags", "blockers"):
            out[k] = ", ".join(str(x) for x in v)
    return out


def compose(state: RunState, outputs: dict[str, dict], kg_dsn: str | None, pseudo=None) -> WorkspaceSpec:
    verdicts = state.verdicts
    claims_by_step: dict[str, list[str]] = {}
    for c in state.claims:
        claims_by_step.setdefault(c.step_id, []).append(c.id)
    kpis = []
    for c in state.claims:
        if c.kind != "kpi" or isinstance(c.value, (dict, list)):
            continue
        v = verdicts.get(c.id)
        kpis.append(Kpi(id=f"k_{c.id}", label=f"{c.field.replace('_', ' ')} ({c.subject})", value=c.value, unit=c.unit,
                        claim_id=c.id, status=STATUS[v.verdict if v else None],
                        confidence=round(v.confidence if v else min(c.confidence, 0.5), 3)))
    tables, charts, uids, color_vals = [], [], {}, {}
    for sid, rec in state.steps.items():
        if rec.status != "ok" or sid not in outputs:
            continue
        out = outputs[sid]
        ml = _main_list(out.get("data"))
        if not ml:
            continue
        key, rows = ml
        flat = [_flatten(r) for r in rows[:5000]]
        cols = []
        for k in flat[0]:
            sample = next((r[k] for r in flat if r.get(k) is not None), None)
            cols.append(Column(key=k, label=k.replace("_", " "), type=_coltype(sample)))
        tables.append(TableSpec(id=f"t_{sid}", title=f"{rec.tool or rec.llm_task}: {key}", columns=cols, rows=flat,
                                claim_ids=claims_by_step.get(sid, [])[:200], sql=out.get("sql")))
        cat = next((k for k in CAT_KEYS if k in flat[0]), None)
        if cat and len(flat) > 1:
            counts: dict[str, int] = {}
            for r in flat:
                counts[str(r.get(cat))] = counts.get(str(r.get(cat)), 0) + 1
            charts.append(ChartSpec(id=f"ch_{sid}", title=f"{key} by {cat}", type="bar", x=cat, y=["n"],
                                    data=[{cat: k, "n": v} for k, v in sorted(counts.items())],
                                    claim_ids=claims_by_step.get(sid, [])[:50]))
        elif "grp" in flat[0]:
            num = [c.key for c in cols if c.type == "number"][:3]
            charts.append(ChartSpec(id=f"ch_{sid}", title=f"{key} by group", type="bar", x="grp", y=num, data=flat,
                                    claim_ids=claims_by_step.get(sid, [])[:50]))
        ck = next((k for k in COLOR_KEYS if k in flat[0]), None)
        for r in flat:
            u = r.get("parcel_uid")
            if u:
                uids.setdefault(u, {})
                if ck and r.get(ck) is not None:
                    uids[u].setdefault("value", str(r.get(ck)))
                    color_vals[str(r.get(ck))] = True
                    uids[u]["color_by"] = ck
    layers = []
    bbox = None
    if uids and kg_dsn:
        from app.tools.db import query
        feats = query(kg_dsn, "SELECT p.parcel_uid, v.name AS village, p.block_id, p.area_ha_gis, "
                              "ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.00001), 6)::json AS g FROM parcel p "
                              "JOIN village v ON v.id = p.village_id WHERE p.parcel_uid = ANY(%s) ORDER BY 1",
                      (sorted(uids)[:5000],))
        ext = query(kg_dsn, "SELECT ST_XMin(e) x0, ST_YMin(e) y0, ST_XMax(e) x1, ST_YMax(e) y1 FROM "
                            "(SELECT ST_Extent(geom) e FROM parcel WHERE parcel_uid = ANY(%s)) q", (sorted(uids)[:5000],))
        if ext and ext[0]["x0"] is not None:
            bbox = [round(ext[0][k], 6) for k in ("x0", "y0", "x1", "y1")]
        ck = next((u.get("color_by") for u in uids.values() if u.get("color_by")), None)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": f["g"], "properties": {
                "parcel_uid": f["parcel_uid"], "village": f["village"], "block": f["block_id"],
                "area_ha": f["area_ha_gis"], **({ck: uids[f["parcel_uid"]].get("value")} if ck else {})}} for f in feats]}
        legend = [LegendItem(value=v, label=v, color=PALETTE[i % len(PALETTE)]) for i, v in enumerate(sorted(color_vals))]
        layers.append(MapLayer(id="parcels", title="Parcels in the answer", kind="parcels", geojson=fc, color_by=ck,
                               legend=legend))
    if state.slots.polygons_ref and Path(state.slots.polygons_ref).exists():
        try:
            fc = json.loads(Path(state.slots.polygons_ref).read_text(encoding="utf-8"))
            layers.append(MapLayer(id="uploads", title="Uploaded polygons", kind="polygons", geojson=fc))
        except ValueError:
            pass
    season_vals = sorted({f"{r.get('ag_year')} {r.get('season')}" for t in tables for r in t.rows
                          if r.get("season") and r.get("ag_year")})
    timeline = None
    for sid, rec in state.steps.items():
        d = (outputs.get(sid) or {}).get("data")
        if rec.status == "ok" and isinstance(d, dict) and isinstance(d.get("timeline"), dict):
            timeline = TimelineSpec(subject=d.get("parcel_uid", ""), events=[
                TimelineEvent(date=str(e.get("event_date") or e.get("date")), stage=e.get("stage", ""),
                              evidence_ref=f"document:{e['document_id']}" if e.get("document_id") else None)
                for e in d["timeline"].get("events", []) if (e.get("event_date") or e.get("date"))],
                series=d["timeline"].get("series", [])[:2000])
            break
    vclaims, summ = [], VerificationSummary()
    for c in state.claims:
        v = verdicts.get(c.id)
        crit = next((ch.model_dump() for ch in reversed(state.critic) if ch.claim_id == c.id), None)
        vclaims.append(VerificationClaim(claim_id=c.id, verdict=v.verdict if v else None,
                                         confidence=round(v.confidence if v else 0.0, 3), checks=c.checks, critic=crit))
        if v:
            if v.verdict == "ACCEPT":
                summ.accepted += 1
            elif v.verdict == "DOWNGRADE":
                summ.downgraded += 1
            else:
                summ.review += 1
    summ.rerouted = state.reroutes
    caveats = list(dict.fromkeys([*state.assumptions, *[c for o in outputs.values() for c in o.get("caveats", [])]]))
    spec = WorkspaceSpec(
        workspace_id=state.workspace_id, title=(state.plan.goal if state.plan else state.request)[:120],
        domain=state.domain, run_id=state.run_id, created_at=now_iso(), ledger_head=state.ledger_head, lang=state.lang,
        kpis=kpis[:24], map=MapSpec(bbox=bbox, layers=layers,
                                     time_slider=TimeSlider(values=season_vals) if season_vals else None),
        tables=tables, charts=charts[:8], timeline=timeline, narrative=state.narrative,
        evidence=[EvidenceGroup(claim_id=c.id, items=c.evidence) for c in state.claims if c.evidence][:500],
        verification=VerificationSpec(summary=summ, claims=vclaims),
        sql=[SqlEntry(step_id=s, sql=o["sql"]) for s, o in outputs.items() if o.get("sql")],
        route_trace=state.route_trace, caveats=caveats[:30])
    return spec


class ComposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None)


def compose_workspace_tool(a: ComposeIn, ctx) -> ToolResult:
    st = getattr(ctx, "state", None)
    if st is None:
        return ToolResult(data=None, summary="compose_workspace runs in the present node", confidence=1.0)
    spec = compose(st, getattr(ctx, "outputs", {}), ctx.kg)
    if a.title:
        spec.title = a.title
    return ToolResult(data=spec.model_dump(mode="json"), confidence=1.0, summary="workspace spec")


def workspace_tools() -> list[Tool]:
    return [Tool("compose_workspace", "Build or patch the WorkspaceSpec (map, KPIs, tables, charts, timeline) from the "
                 "verified run. Called by the presenter.", ComposeIn, compose_workspace_tool, planner_visible=False)]
