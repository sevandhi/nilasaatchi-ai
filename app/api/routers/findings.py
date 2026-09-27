"""GET /findings, /findings/summary, /findings/{id}/evidence-pack, /idle-land
(docs/ui-spec.md "Backend additions needed"). Evidence packs mask owner fields via
pipeline.findings.evidence_pack.mask_owner (P5, gis-engineer) — never PII, never bank data."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.api.db import get_conn
from app.api.schemas import (
    EvidencePackResponse,
    FindingItem,
    FindingListResponse,
    FindingSummaryResponse,
    FindingSummaryRow,
    IdleLandResponse,
    IdleLandRow,
)

router = APIRouter(tags=["findings"])


@router.get("/findings", response_model=FindingListResponse)
async def list_findings(
    category: str | None = None, severity: str | None = None, village: str | None = None,
    block: int | None = Query(None, alias="block"), level: str | None = Query(None, alias="level"),
    limit: int = Query(100, le=1000), offset: int = 0,
) -> FindingListResponse:
    where, params = ["status = 'open'"], []
    if category:
        where.append("category = %s")
        params.append(category)
    if severity:
        where.append("severity = %s")
        params.append(severity)
    if village:
        where.append("village = %s")
        params.append(village)
    if block is not None:
        where.append("block_id = %s")
        params.append(block)
    if level:
        where.append("evidence_level = %s")
        params.append(level)
    clause = " AND ".join(where)

    def _run():
        with get_conn() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM finding WHERE {clause}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT id, finding_key, category, severity, confidence, parcel_uid, village, "
                f"unit_id, block_id, title, evidence_level, verdict, status, caveats, created_at "
                f"FROM finding WHERE {clause} ORDER BY severity, id LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
            return total, rows

    total, rows = await asyncio.to_thread(_run)
    return FindingListResponse(items=[FindingItem(**r) for r in rows], total=total,
                               limit=limit, offset=offset)


@router.get("/findings/summary", response_model=FindingSummaryResponse)
async def findings_summary() -> FindingSummaryResponse:
    def _run():
        with get_conn() as conn:
            return conn.execute(
                "SELECT category, severity, evidence_level, n, avg_conf FROM v_finding_summary "
                "ORDER BY category, severity"
            ).fetchall()

    rows = await asyncio.to_thread(_run)
    return FindingSummaryResponse(items=[FindingSummaryRow(**r) for r in rows])


@router.get("/idle-land", response_model=IdleLandResponse)
async def idle_land() -> IdleLandResponse:
    def _run():
        with get_conn() as conn:
            return conn.execute(
                "SELECT finding_id, village, unit_id, block_id, idle_ha, n_parcels, "
                "oldest_possession, min_major_road_km, min_substation_km, evidence_level, confidence "
                "FROM v_idle_land_bank ORDER BY idle_ha DESC NULLS LAST"
            ).fetchall()

    rows = await asyncio.to_thread(_run)
    return IdleLandResponse(items=[IdleLandRow(**r) for r in rows])


@router.get("/findings/{finding_id}/evidence-pack", response_model=EvidencePackResponse)
async def get_evidence_pack(finding_id: int, render_chips: bool = False) -> EvidencePackResponse:
    def _run():
        # evidence_pack() opens its own tuple-row connection (os.environ["DATABASE_URL"]) when no
        # `conn` is passed; app.api.db's pool uses dict_row (routers/*), which is incompatible with
        # evidence_pack's `zip(column_names, row)` unpacking — so we deliberately do not share the pool here.
        from pipeline.findings.evidence_pack import evidence_pack

        return evidence_pack(finding_id, render_chips=render_chips)

    try:
        pack = await asyncio.to_thread(_run)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return EvidencePackResponse(finding_id=finding_id, pack=pack)


@router.get("/findings/facets")
async def finding_facets() -> dict:
    """Values present in the data for each Findings filter (with counts), for the filter dropdowns."""
    def _run():
        with get_conn() as conn:
            out = {}
            for key, col in (("category", "category"), ("severity", "severity"), ("village", "village"),
                             ("block", "block_id"), ("level", "evidence_level")):
                rows = conn.execute(f"SELECT {col} AS v, count(*) AS n FROM finding WHERE status = 'open' "
                                    f"AND {col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
                out[key] = [{"value": r["v"], "count": r["n"]} for r in rows]
            return out
    return await asyncio.to_thread(_run)


@router.get("/findings/{finding_id}/plain-summary")
async def finding_plain_summary(finding_id: int, lang: str = Query("en", pattern="^(en|ta)$")) -> dict:
    """The finding explained in everyday words (AI via the router, cached; template fallback)."""
    def _run():
        from app.api.plain_summary import plain_summary
        from pipeline.findings.evidence_pack import evidence_pack

        return plain_summary(evidence_pack(finding_id, render_chips=False), lang)

    try:
        return await asyncio.to_thread(_run)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
