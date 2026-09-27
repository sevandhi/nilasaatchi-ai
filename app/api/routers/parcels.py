"""GET /parcels/{uid}, /parcels/{uid}/timeline, /parcels/{uid}/extractions,
/parcels/{uid}/satellite, /parcels/{uid}/findings (docs/ui-spec.md "Backend additions needed").

Owner names are masked by default everywhere (safe_row_json, D-032/D-033); bank account numbers are
stripped unconditionally.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.api.config import get_settings
from app.api.db import get_conn
from app.api.mask import safe_row_json
from app.api.schemas import (
    DidRow,
    FindingItem,
    ParcelDetail,
    ParcelExtractionRow,
    ParcelExtractionsResponse,
    ParcelSatelliteResponse,
    ParcelTimelineResponse,
    SeasonState,
    SeriesPoint,
    TimelineEvent,
)

router = APIRouter(tags=["parcels"])


def _get_parcel_lifecycle(conn, parcel_uid: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM v_parcel_lifecycle WHERE parcel_uid = %s", (parcel_uid,)
    ).fetchone()


def _get_parcel_base(conn, parcel_uid: str) -> dict | None:
    return conn.execute(
        "SELECT p.parcel_uid, v.name AS village, p.kide, p.survey_no, p.sub_div, p.unit_id, "
        "p.block_id, p.area_ha_gis, round((ST_Area(p.geom_utm)/1e4)::numeric,4) AS area_ha_utm, "
        "p.inside_park_boundary FROM parcel p JOIN village v ON v.id = p.village_id "
        "WHERE p.parcel_uid = %s", (parcel_uid,),
    ).fetchone()


@lru_cache
def _smoothed_dataset():
    import pyarrow.dataset as ds

    from app.api.config import REPO_ROOT

    path = REPO_ROOT / "data" / "s2" / "smoothed.parquet"
    if not path.exists():
        return None
    return ds.dataset(path)


def _smoothed_series(parcel_uid: str) -> list[SeriesPoint]:
    dataset = _smoothed_dataset()
    if dataset is None:
        return []
    import pyarrow.compute as pc

    table = dataset.to_table(filter=(pc.field("parcel_uid") == parcel_uid))
    df = table.to_pylist()
    df.sort(key=lambda r: r["date"])
    return [SeriesPoint(date=r["date"], ndvi=r.get("ndvi_s"), bsi=r.get("bsi_s"),
                        ndwi=r.get("ndwi_s"), supported=r.get("supported")) for r in df]


@router.get("/parcels/{parcel_uid:path}/timeline", response_model=ParcelTimelineResponse)
async def get_parcel_timeline(parcel_uid: str) -> ParcelTimelineResponse:
    def _run():
        with get_conn() as conn:
            lc = _get_parcel_lifecycle(conn, parcel_uid)
            if lc is None:
                return None
            seasons = conn.execute(
                "SELECT ag_year, season, state, p_state, route, needs_field FROM parcel_season "
                "WHERE parcel_uid = %s ORDER BY ag_year, season", (parcel_uid,),
            ).fetchall()
            return lc, seasons

    result = await asyncio.to_thread(_run)
    if result is None:
        raise HTTPException(status_code=404, detail="parcel not found")
    lc, seasons = result
    events = [TimelineEvent(event_id=e.get("event_id"), stage=e["stage"], event_date=e.get("date"),
                            date_precision=e.get("precision"), document_id=e.get("document_id"),
                            link_kind=e.get("link"))
             for e in (lc.get("events") or [])]
    series = await asyncio.to_thread(_smoothed_series, parcel_uid)
    return ParcelTimelineResponse(
        parcel_uid=parcel_uid, events=events, series=series,
        season_states=[SeasonState(**s) for s in seasons],
    )


@router.get("/parcels/{parcel_uid:path}/extractions", response_model=ParcelExtractionsResponse)
async def get_parcel_extractions(parcel_uid: str, limit: int = Query(200, le=2000),
                                 offset: int = 0, demo_mask: bool = True) -> ParcelExtractionsResponse:
    settings = get_settings()
    masked = demo_mask or not settings.allow_demo_unmask

    def _run():
        with get_conn() as conn:
            rows = conn.execute(
                """
                WITH ext_ids AS (
                    SELECT extraction_id, fact_type FROM parcel_fact WHERE parcel_uid = %(uid)s
                    UNION
                    SELECT extraction_id, stage AS fact_type FROM acquisition_event
                    WHERE parcel_uid = %(uid)s AND extraction_id IS NOT NULL
                )
                SELECT DISTINCT ON (e.id) e.id AS extraction_id, x.fact_type, e.document_id, e.page_no,
                       e.bbox, e.bbox_method, e.confidence, e.self_consistency, e.owner_read_status,
                       e.review_status, e.row_json
                FROM ext_ids x JOIN extraction e ON e.id = x.extraction_id
                ORDER BY e.id LIMIT %(limit)s OFFSET %(offset)s
                """, {"uid": parcel_uid, "limit": limit, "offset": offset},
            ).fetchall()
            total = conn.execute(
                "SELECT count(DISTINCT extraction_id) AS n FROM ("
                "SELECT extraction_id FROM parcel_fact WHERE parcel_uid = %(uid)s "
                "UNION SELECT extraction_id FROM acquisition_event "
                "WHERE parcel_uid = %(uid)s AND extraction_id IS NOT NULL) t",
                {"uid": parcel_uid},
            ).fetchone()["n"]
            return rows, total

    rows, total = await asyncio.to_thread(_run)
    items = [ParcelExtractionRow(
        extraction_id=r["extraction_id"], fact_type=r["fact_type"], document_id=r["document_id"],
        page_no=r["page_no"], bbox=r["bbox"], bbox_method=r["bbox_method"], confidence=r["confidence"],
        self_consistency=r["self_consistency"], owner_read_status=r["owner_read_status"],
        review_status=r["review_status"], row=safe_row_json(r["row_json"], masked=masked),
    ) for r in rows]
    return ParcelExtractionsResponse(parcel_uid=parcel_uid, items=items, total=total)


def _chip_list(parcel_uid: str) -> list[str]:
    try:
        from planet.chips.render import CHIP_DIR, safe_name
    except Exception:  # noqa: BLE001 - optional heavy deps (mirrors pipeline.findings.evidence_pack)
        return []
    d = CHIP_DIR / safe_name(parcel_uid)
    if not d.exists():
        return []
    return sorted(str(Path(p)) for p in d.glob("*.png"))


@router.get("/parcels/{parcel_uid:path}/satellite", response_model=ParcelSatelliteResponse)
async def get_parcel_satellite(parcel_uid: str) -> ParcelSatelliteResponse:
    def _run():
        with get_conn() as conn:
            did = conn.execute(
                "SELECT event, metric, season_scope, event_date, n_pre, n_post, pre_diff, post_diff, "
                "did, ci_lo, ci_hi, farmland_like_p FROM parcel_did WHERE parcel_uid = %s "
                "ORDER BY event, metric, season_scope", (parcel_uid,),
            ).fetchall()
            seasons = conn.execute(
                "SELECT ag_year, season, state, p_state, route, needs_field FROM parcel_season "
                "WHERE parcel_uid = %s ORDER BY ag_year, season", (parcel_uid,),
            ).fetchall()
            exists = conn.execute("SELECT 1 FROM parcel WHERE parcel_uid = %s", (parcel_uid,)).fetchone()
            return did, seasons, exists

    did, seasons, exists = await asyncio.to_thread(_run)
    if exists is None:
        raise HTTPException(status_code=404, detail="parcel not found")
    chips = await asyncio.to_thread(_chip_list, parcel_uid)
    return ParcelSatelliteResponse(
        parcel_uid=parcel_uid, did=[DidRow(**d) for d in did],
        season_states=[SeasonState(**s) for s in seasons], chips=chips,
    )


@router.get("/parcels/{parcel_uid:path}/findings", response_model=list[FindingItem])
async def get_parcel_findings(parcel_uid: str) -> list[FindingItem]:
    def _run():
        with get_conn() as conn:
            return conn.execute(
                "SELECT id, finding_key, category, severity, confidence, parcel_uid, village, "
                "unit_id, block_id, title, evidence_level, verdict, status, caveats, created_at "
                "FROM finding WHERE parcel_uid = %s ORDER BY severity, id", (parcel_uid,),
            ).fetchall()

    rows = await asyncio.to_thread(_run)
    return [FindingItem(**r) for r in rows]


# Registered *last*: the `:path` converter is greedy and would otherwise shadow the routes above
# (Starlette matches routes in declaration order) for any parcel_uid containing a literal "/"
# (D-020 parcel_uid = 'village|kide', and kide itself may contain '/', e.g. 'Allikulam|10/1').
@router.get("/parcels/{parcel_uid:path}", response_model=ParcelDetail)
async def get_parcel(parcel_uid: str) -> ParcelDetail:
    def _run():
        with get_conn() as conn:
            base = _get_parcel_base(conn, parcel_uid)
            if base is None:
                return None
            lc = _get_parcel_lifecycle(conn, parcel_uid) or {}
            n_findings = conn.execute(
                "SELECT count(*) AS n FROM finding WHERE parcel_uid = %s AND status = 'open'",
                (parcel_uid,),
            ).fetchone()["n"]
            return base, lc, n_findings

    result = await asyncio.to_thread(_run)
    if result is None:
        raise HTTPException(status_code=404, detail="parcel not found")
    base, lc, n_findings = result
    return ParcelDetail(
        **base,
        current_stage=lc.get("current_stage"), stage_entered=lc.get("stage_entered"),
        days_in_stage=lc.get("days_in_stage"), evidence_level=lc.get("evidence_level"),
        stalled_flag=lc.get("stalled_flag"), n_findings=n_findings,
    )
