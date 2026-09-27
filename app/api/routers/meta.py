"""GET /stats/overview, /eval/summary, /decisions, /progress (docs/ui-spec.md "Backend additions
needed"). `/stats/overview` numbers each carry a `source` string (the SQL or file they came from,
per the backend-engineer brief); the docs/*.md endpoints are parsed, never hand-copied, so they can
never drift from the honest, measured record CLAUDE.md requires."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.config import REPO_ROOT
from app.api.db import get_conn
from app.api.mdparse import first_table
from app.api.schemas import (
    Decision,
    DecisionsResponse,
    EvalSummaryResponse,
    KpiValue,
    ProgressResponse,
    StatsOverviewResponse,
)

router = APIRouter(tags=["meta"])


# -------------------------------------------------------------------------------------- stats

_OVERVIEW_QUERIES: list[tuple[str, str, str, str]] = [
    ("documents", "Documents", "SELECT count(*) AS n FROM document", "document"),
    ("pages", "Pages", "SELECT count(*) AS n FROM page", "page"),
    ("extraction_rows", "Extraction rows", "SELECT count(*) AS n FROM extraction", "extraction"),
    ("facts", "Parcel facts", "SELECT count(*) AS n FROM parcel_fact", "parcel_fact"),
    ("events", "Acquisition events", "SELECT count(*) AS n FROM acquisition_event", "acquisition_event"),
    ("parcels", "Parcels", "SELECT count(*) AS n FROM parcel", "parcel"),
    ("parcels_with_facts", "Parcels with facts",
     "SELECT count(DISTINCT parcel_uid) AS n FROM parcel_fact WHERE parcel_uid IS NOT NULL", "parcel_fact"),
    ("satellite_observations", "Satellite observations", "SELECT count(*) AS n FROM parcel_obs", "parcel_obs"),
    ("parcel_seasons", "Parcel-seasons", "SELECT count(*) AS n FROM parcel_season", "parcel_season"),
    ("findings", "Findings", "SELECT count(*) AS n FROM finding WHERE status = 'open'", "finding"),
    ("review_queue", "Review queue",
     "SELECT count(*) AS n FROM review_queue WHERE status = 'open'", "review_queue"),
]


def _findings_by_category(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT category, count(*) AS n FROM finding WHERE status = 'open' GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return {r["category"]: r["n"] for r in rows}


def _classified_types(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT classified_type, count(*) AS n FROM document GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return {(r["classified_type"] or "NULL"): r["n"] for r in rows}


def _aws_spend() -> dict | None:
    path = REPO_ROOT / "data" / "aws_spend.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _aws_spend_router_estimate() -> float:
    """Sum of `actual_cost_usd` for Bedrock-billed model ids in data/router_log.sqlite (every
    `mode`, including "batch"). Cost Explorer (`data/aws_spend.json`, `make aws-cost`) lags ~1 day,
    so this is the more current figure during/just after a bulk Bedrock job."""
    import sqlite3

    path = REPO_ROOT / "data" / "router_log.sqlite"
    if not path.exists():
        return 0.0
    try:
        from app.router.registry import load_registry

        bedrock_ids = load_registry().aws_billed_ids()
    except Exception:  # noqa: BLE001 - registry unavailable/misconfigured: report 0, don't fail the KPI
        bedrock_ids = set()
    if not bedrock_ids:
        return 0.0
    conn = sqlite3.connect(path)
    try:
        placeholders = ",".join("?" for _ in bedrock_ids)
        row = conn.execute(
            f"SELECT COALESCE(sum(actual_cost_usd), 0) AS n FROM router_log "
            f"WHERE model_id IN ({placeholders})", list(bedrock_ids),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


@router.get("/stats/overview", response_model=StatsOverviewResponse)
async def stats_overview() -> StatsOverviewResponse:
    def _run():
        with get_conn() as conn:
            values = {}
            for kid, _label, sql, _table in _OVERVIEW_QUERIES:
                values[kid] = conn.execute(sql).fetchone()["n"]
            return values, _findings_by_category(conn), _classified_types(conn)

    values, findings_by_cat, classified_types = await asyncio.to_thread(_run)
    kpis = [
        KpiValue(id=kid, label=label, value=values[kid], source=f"`{sql}`")
        for kid, label, sql, _table in _OVERVIEW_QUERIES
    ]
    kpis.append(KpiValue(id="findings_by_category", label="Findings by category",
                         value=sum(findings_by_cat.values()), breakdown=findings_by_cat,
                         source="SELECT category, count(*) FROM finding WHERE status='open' GROUP BY 1"))
    kpis.append(KpiValue(id="classified_types", label="Classified document types",
                         value=len(classified_types), breakdown=classified_types,
                         source="SELECT classified_type, count(*) FROM document GROUP BY 1"))
    spend = _aws_spend()
    kpis.append(KpiValue(id="aws_spend_usd", label="AWS spend vs US$100 event budget (Cost Explorer)",
                         value=(spend or {}).get("total_usd"), unit="USD",
                         breakdown={"budget_usd": 100, "fetched_at": (spend or {}).get("fetched_at"),
                                   "note": "Cost Explorer lags ~1 day; see aws_spend_router_estimate_usd"},
                         source="data/aws_spend.json (`make aws-cost`)"))
    router_estimate = await asyncio.to_thread(_aws_spend_router_estimate)
    kpis.append(KpiValue(id="aws_spend_router_estimate_usd",
                         label="AWS spend vs US$100 event budget (router estimate, live)",
                         value=router_estimate, unit="USD", breakdown={"budget_usd": 100},
                         source="data/router_log.sqlite (sum actual_cost_usd WHERE model_id IN "
                                "config/models.yaml bedrock ids, all modes incl. batch)"))

    progress_md = (REPO_ROOT / "docs" / "progress.md").read_text(encoding="utf-8")
    m = re.search(r"Overall completion:\s*\*\*([^*]+)\*\*", progress_md)
    phase_status = {"overall": m.group(1).strip() if m else None,
                    "phases": first_table(progress_md), "source": "docs/progress.md"}
    return StatsOverviewResponse(kpis=kpis, phase_status=phase_status, generated_at=datetime.now(UTC))


# -------------------------------------------------------------------------------------- eval / decisions / progress

@router.get("/eval/summary", response_model=EvalSummaryResponse)
async def eval_summary() -> EvalSummaryResponse:
    metrics_md = (REPO_ROOT / "docs" / "metrics.md").read_text(encoding="utf-8")
    rows = first_table(metrics_md)
    files = {}
    for name, rel in [("planet_did", "eval/planet/did_report.json"),
                      ("student_metrics", "eval/planet/student_metrics.json"),
                      ("agent_run_final", "data/eval/agent_run_final.json"),
                      ("match_eval", "data/eval/match_eval.json")]:
        p = REPO_ROOT / rel
        if p.exists():
            try:
                files[name] = json.loads(p.read_text())
            except Exception as e:  # noqa: BLE001 - report, never fail the endpoint
                files[name] = {"error": str(e)}
    return EvalSummaryResponse(rows=rows, files=files)


@router.get("/decisions", response_model=DecisionsResponse)
async def decisions() -> DecisionsResponse:
    md = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    rows = first_table(md)
    items = [Decision(id=r.get("ID", ""), date=r.get("Date"), decision=r.get("Decision", ""),
                      by=r.get("By"), context=r.get("Context / alternatives"))
            for r in rows if r.get("ID", "").startswith("D-")]
    return DecisionsResponse(items=items)


@router.get("/progress", response_model=ProgressResponse)
async def progress() -> ProgressResponse:
    md = (REPO_ROOT / "docs" / "progress.md").read_text(encoding="utf-8")
    m = re.search(r"Overall completion:\s*\*\*([^*]+)\*\*", md)
    return ProgressResponse(overall_pct=m.group(1).strip() if m else None,
                            phases=first_table(md), raw_markdown=md)
