"""GET /documents, GET /documents/{id}/pages/{n}.webp, GET /evidence/{extraction_id}.

Owner names are masked by default everywhere (D-032/D-033); `?demo_mask=false` only unmasks when
the server is explicitly configured with `ALLOW_DEMO_UNMASK=true` (local only, never in a deployed
or demo-visible environment). Bank account numbers are stripped unconditionally.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.config import REPO_ROOT, get_settings
from app.api.db import get_conn
from app.api.mask import safe_row_json
from app.api.mdparse import first_table
from app.api.schemas import (
    ClassifierSummaryResponse,
    DocumentListResponse,
    DocumentSummary,
    EvidenceResponse,
    OwnerRef,
)

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    village: str | None = None, classified_type: str | None = None, stage: str | None = None,
    scheme_relevance: str | None = None, q: str | None = None,
    limit: int = Query(50, le=500), offset: int = 0,
) -> DocumentListResponse:
    where, params = [], []
    if village:
        where.append("village = %s")
        params.append(village)
    if classified_type:
        where.append("classified_type = %s")
        params.append(classified_type)
    if stage:
        where.append("stage = %s")
        params.append(stage)
    if scheme_relevance:
        where.append("scheme_relevance = %s")
        params.append(scheme_relevance)
    if q:
        # `path`/`doc_no` full-text-ish match; page.tsv already indexes OCR text (see page_tsv_idx),
        # so a document also matches when any of its pages' text matches the query.
        where.append("(path ILIKE %s OR doc_no ILIKE %s OR id IN "
                     "(SELECT document_id FROM page WHERE tsv @@ plainto_tsquery('simple', %s)))")
        params.extend([f"%{q}%", f"%{q}%", q])
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    def _run():
        with get_conn() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM document {clause}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT id, sha256, path, classified_type, scheme_relevance, village, unit_no, "
                f"block_no, doc_no, doc_date, pages, status, stage, folder_label, "
                f"(folder_label IS NOT NULL AND classified_type IS NOT NULL "
                f"AND folder_label <> classified_type) AS folder_type_mismatch "
                f"FROM document {clause} ORDER BY id LIMIT %s OFFSET %s", [*params, limit, offset],
            ).fetchall()
            return total, rows

    total, rows = await asyncio.to_thread(_run)
    return DocumentListResponse(items=[DocumentSummary(**r) for r in rows], total=total,
                                limit=limit, offset=offset)


@router.get("/classifier/summary", response_model=ClassifierSummaryResponse)
async def classifier_summary() -> ClassifierSummaryResponse:
    metrics_md = (REPO_ROOT / "docs" / "metrics.md").read_text(encoding="utf-8")
    rows = [r for r in first_table(metrics_md) if "classifier" in r.get("Metric", "").lower()]

    def _run():
        with get_conn() as conn:
            dist = conn.execute(
                "SELECT classified_type, stage, count(*) AS n FROM document GROUP BY 1, 2 "
                "ORDER BY n DESC LIMIT 100"
            ).fetchall()
            mismatch = conn.execute(
                "SELECT count(*) AS n FROM document WHERE folder_label IS NOT NULL "
                "AND classified_type IS NOT NULL AND folder_label <> classified_type"
            ).fetchone()["n"]
            return dist, mismatch

    dist, mismatch = await asyncio.to_thread(_run)
    return ClassifierSummaryResponse(metrics=rows, live_distribution=dist,
                                     folder_vs_classified_mismatch=mismatch)


@router.get("/documents/{document_id}/pages/{page_no}.webp")
async def get_page_preview(document_id: int, page_no: int) -> FileResponse:
    def _run():
        with get_conn() as conn:
            return conn.execute(
                "SELECT preview_path FROM page WHERE document_id = %s AND page_no = %s",
                (document_id, page_no),
            ).fetchone()

    row = await asyncio.to_thread(_run)
    if row is None or not row["preview_path"]:
        raise HTTPException(status_code=404, detail="page preview not found")
    # preview_path is stored repo-relative (e.g. "data/pages/..") — resolve against the repo root.
    raw = Path(row["preview_path"])
    path = raw if raw.is_absolute() else (REPO_ROOT / raw).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="page preview file missing on disk")
    return FileResponse(path, media_type="image/webp")


@router.get("/evidence/{extraction_id}", response_model=EvidenceResponse)
async def get_evidence(extraction_id: int, demo_mask: bool = True) -> EvidenceResponse:
    settings = get_settings()
    masked = demo_mask or not settings.allow_demo_unmask

    def _run():
        with get_conn() as conn:
            ext = conn.execute(
                "SELECT id, document_id, page_id, page_no, page_ref, schema_name, doc_type, row_json, "
                "bbox, bbox_method, extractor, privacy_tier, confidence, review_status "
                "FROM extraction WHERE id = %s", (extraction_id,),
            ).fetchone()
            if ext is None:
                return None, []
            owners = conn.execute(
                "SELECT position, owner_token, relation, legal_heirs FROM v_owner_pseudo "
                "WHERE extraction_id = %s ORDER BY position", (extraction_id,),
            ).fetchall()
            if not masked:
                names = conn.execute(
                    "SELECT eo.position, o.canonical_name FROM extraction_owner eo "
                    "JOIN owner o ON o.id = eo.owner_id WHERE eo.extraction_id = %s", (extraction_id,),
                ).fetchall()
                name_by_pos = {r["position"]: r["canonical_name"] for r in names}
                for o in owners:
                    o["canonical_name"] = name_by_pos.get(o["position"])
            return ext, owners

    ext, owners = await asyncio.to_thread(_run)
    if ext is None:
        raise HTTPException(status_code=404, detail="extraction not found")
    row_json = safe_row_json(ext["row_json"], masked=masked)
    owner_refs = [OwnerRef(position=o["position"], token=o["owner_token"], relation=o.get("relation"),
                           legal_heirs=o.get("legal_heirs", False),
                           name=(None if masked else o.get("canonical_name")))
                 for o in owners]
    return EvidenceResponse(
        extraction_id=ext["id"], document_id=ext["document_id"], page_id=ext["page_id"],
        page_no=ext["page_no"], page_ref=ext["page_ref"], schema_name=ext["schema_name"],
        doc_type=ext["doc_type"], bbox=ext["bbox"], bbox_method=ext["bbox_method"],
        extractor=ext["extractor"], privacy_tier=ext["privacy_tier"], confidence=ext["confidence"],
        review_status=ext["review_status"], row=row_json, owners=owner_refs, masked=masked,
    )


@router.get("/documents/facets")
async def document_facets() -> dict:
    """Values present in the catalogue for each Documents filter (with counts), live from the database, so
    new documents (uploads) and new types/stages appear in the dropdowns automatically."""
    def _run():
        with get_conn() as conn:
            out = {}
            for key in ("village", "classified_type", "stage"):
                rows = conn.execute(f"SELECT {key} AS v, count(*) AS n FROM document WHERE {key} IS NOT NULL "
                                    f"AND {key} <> '' GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
                out[key] = [{"value": r["v"], "count": r["n"]} for r in rows]
            return out
    return await asyncio.to_thread(_run)
