"""POST /ingest/documents, GET /ingest/jobs, GET /ingest/jobs/{id}, POST /ingest/satellite-refresh,
GET /ingest/satellite/status.

New-document ingest path (plan.md "make the app handle NEW data, not only the given dataset"): an
uploaded PDF runs through the same batch pipeline stages (pipeline.catalog/classify/extract/load/
match/findings — see app/ingest/document_stages.py) instead of a reimplementation, driven by a
background job (app/ingest/orchestrator.py) so the request returns immediately (202).

Owner names are masked wherever a job result surfaces extracted values (reused from the
documents/evidence routers via the same loader/mask path — see app/api/mask.py); this router never
returns raw owner text itself.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app.api.config import REPO_ROOT
from app.api.db import get_conn
from app.api.schemas import (
    IngestDocumentAccepted,
    IngestJob,
    IngestJobListResponse,
    SatelliteRefreshAccepted,
    SatelliteRefreshRequest,
    SatelliteStatusResponse,
)
from app.export.pdf import PlaywrightNotAvailable, html_to_pdf
from app.ingest import document_stages as ds
from app.ingest import orchestrator, report, store

router = APIRouter(prefix="/ingest", tags=["ingest"])

MAX_BYTES = 50 * 1024 * 1024


async def _to_thread(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


def _job_to_api(job: dict) -> IngestJob:
    return IngestJob(**store.to_job_response(job))


def declarable_types() -> list[str]:
    """Types an uploader may declare: the ones the table reader extracts."""
    from pipeline.extract.batch import PRIORITY

    return list(PRIORITY)


@router.get("/doc-types")
def ingest_doc_types() -> dict:
    return {"doc_types": declarable_types()}


@router.post("/documents", response_model=IngestDocumentAccepted, status_code=202)
async def ingest_document(
    file: UploadFile = File(...), village: str | None = Form(None),  # noqa: B008 - FastAPI idiom,
    doc_type: str | None = Form(None),
    # evaluated once at route registration, not per-request; not a mutable-default bug.
) -> IngestDocumentAccepted:
    if not (file.filename or "").lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="only PDF uploads are accepted")
    doc_type = (doc_type or "").strip() or None
    if doc_type and doc_type not in declarable_types():
        raise HTTPException(status_code=400, detail=f"unknown document type {doc_type!r}")
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds the {MAX_BYTES // (1024 * 1024)} MB limit")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="file does not look like a PDF (missing %PDF header)")

    def _store_and_check() -> tuple[str, str, int | None]:
        md5, path = ds.store_pdf(content)
        rel = str(path.relative_to(REPO_ROOT))
        with get_conn() as conn:
            dup_id = ds.find_duplicate(conn, md5)
        return md5, rel, dup_id

    md5, rel_path, dup_id = await _to_thread(_store_and_check)

    if dup_id is not None:
        def _create_dup_job() -> int:
            stages = store.init_stages(store.DOCUMENT_STAGE_NAMES)
            for s in stages:
                if s["name"] == "store":
                    s["status"], s["detail"] = "done", f"duplicate of document {dup_id} (md5 {md5})"
                else:
                    s["status"], s["detail"] = "skipped", "skipped: duplicate upload"
            with get_conn() as conn:
                job_id = store.create_job(
                    conn, "document", file.filename,
                    {"md5": md5, "path": rel_path, "village": village}, store.DOCUMENT_STAGE_NAMES,
                    status="duplicate",
                )
                # create_job wrote the default all-pending stages; overwrite with our pre-filled ones.
                conn.execute("UPDATE ingest_job SET stages = %s::jsonb, document_id = %s, "
                            "started_at = now(), finished_at = now() WHERE id = %s",
                            (json.dumps(stages, default=str), dup_id, job_id))
                conn.commit()
            return job_id

        job_id = await _to_thread(_create_dup_job)
        return IngestDocumentAccepted(job_id=job_id, status="duplicate", duplicate_of=dup_id)

    def _create_job() -> int:
        with get_conn() as conn:
            job_id = store.create_job(
                conn, "document", file.filename,
                {"path": rel_path, "folder_label": village, "md5": md5, "doc_type": doc_type}, store.DOCUMENT_STAGE_NAMES,
                status="queued",
            )
            store.set_stage(conn, job_id, "store", "done", detail=f"saved to {rel_path} (md5 {md5})")
            return job_id

    job_id = await _to_thread(_create_job)
    orchestrator.start_document_job(job_id)
    return IngestDocumentAccepted(job_id=job_id, status="queued", duplicate_of=None)


@router.get("/jobs", response_model=IngestJobListResponse)
async def list_ingest_jobs(kind: str | None = None, limit: int = 20) -> IngestJobListResponse:
    def _run():
        with get_conn() as conn:
            return store.list_jobs(conn, kind=kind, limit=limit)

    rows = await _to_thread(_run)
    return IngestJobListResponse(jobs=[_job_to_api(r) for r in rows])


@router.get("/jobs/{job_id}", response_model=IngestJob)
async def get_ingest_job(job_id: int) -> IngestJob:
    def _run():
        with get_conn() as conn:
            return store.get_job(conn, job_id)

    row = await _to_thread(_run)
    if row is None:
        raise HTTPException(status_code=404, detail="ingest job not found")
    return _job_to_api(row)


@router.get("/jobs/{job_id}/report")
async def ingest_job_report(job_id: int, fmt: str = "pdf") -> Response:
    """Downloadable verification report for one upload: pdf (readable report), html, or csv (rows)."""
    if fmt not in ("pdf", "html", "csv"):
        raise HTTPException(status_code=400, detail="fmt must be pdf, html or csv")

    def _run():
        with get_conn() as conn:
            job = store.get_job(conn, job_id)
            return None if job is None or job.get("kind") != "document" else report.build(conn, job)

    rep = await _to_thread(_run)
    if rep is None:
        raise HTTPException(status_code=404, detail="document upload job not found")
    name = f"nilasaatchi_report_job{job_id}"
    if fmt == "csv":
        return Response(report.to_csv(rep), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}_rows.csv"'})
    page = report.to_html(rep)
    if fmt == "html":
        return Response(page, media_type="text/html")
    try:
        pdf = await _to_thread(html_to_pdf, page)
    except PlaywrightNotAvailable as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.post("/satellite-refresh", response_model=SatelliteRefreshAccepted, status_code=202)
async def satellite_refresh(body: SatelliteRefreshRequest) -> SatelliteRefreshAccepted:
    def _create():
        with get_conn() as conn:
            if store.running_satellite_job(conn) is not None:
                return None
            return store.create_job(conn, "satellite", None,
                                    {"max_scenes": body.max_scenes}, store.SATELLITE_STAGE_NAMES,
                                    status="queued")

    job_id = await _to_thread(_create)
    if job_id is None:
        raise HTTPException(status_code=409, detail="a satellite-refresh job is already running")
    orchestrator.start_satellite_job(job_id)
    return SatelliteRefreshAccepted(job_id=job_id)


@router.get("/satellite/status", response_model=SatelliteStatusResponse)
async def satellite_status() -> SatelliteStatusResponse:
    def _run():
        with get_conn() as conn:
            scene = conn.execute(
                "SELECT max(datetime)::date AS latest, count(*) AS n FROM s2_scene"
            ).fetchone()
            running_id = store.running_satellite_job(conn)
            last_job = conn.execute(
                "SELECT finished_at FROM ingest_job WHERE kind = 'satellite' AND status = 'done' "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            return scene, running_id, last_job

    scene, running_id, last_job = await _to_thread(_run)
    return SatelliteStatusResponse(
        latest_scene_date=scene["latest"] if scene else None,
        scene_count=(scene["n"] if scene else 0) or 0,
        last_refresh_at=last_job["finished_at"] if last_job else None,
        running_job_id=running_id,
    )
