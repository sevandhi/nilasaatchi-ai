"""Background-thread sequencing for ingest jobs, called from app/api/routers/ingest.py.

Document jobs: each remaining stage (catalog..findings) runs as its own `uv run python -m
app.ingest.stage_cli` subprocess, sequentially, in a background `threading.Thread`. Satellite jobs:
one background thread runs `app.ingest.satellite_stages.run_satellite_job`, which itself manages a
single long-lived subprocess (planet.refresh) and parses its progress.

Threads (not asyncio tasks) because `subprocess.run`/`Popen.wait()` here are blocking calls; a
background thread keeps the FastAPI event loop free to serve other requests while a job runs.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from app.ingest import store
from app.ingest.db import get_conn

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generous per-stage timeouts: `extract` can involve several VLM calls; `classify`/`catalog` are
# normally seconds. A stage that hangs past this is killed and recorded as failed, not left to
# block the worker thread (and therefore this job) forever.
STAGE_TIMEOUT_S = {"catalog": 300, "classify": 180, "extract": 900, "load": 180, "match": 300,
                   "findings": 600}
DOCUMENT_REMAINING_STAGES = ["catalog", "classify", "extract", "load", "match", "findings"]


def _run_stage_subprocess(job_id: int, stage: str) -> None:
    timeout = STAGE_TIMEOUT_S.get(stage, 600)
    try:
        proc = subprocess.run(
            ["uv", "run", "python", "-m", "app.ingest.stage_cli", "document", stage, "--job-id", str(job_id)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        with get_conn() as conn:
            store.set_stage(conn, job_id, stage, "failed", detail=f"stage timed out after {timeout}s")
        return
    with get_conn() as conn:
        job = store.get_job(conn, job_id)
    stage_row = next((s for s in job["stages"] if s["name"] == stage), None)
    if stage_row is not None and stage_row["status"] == "running":
        # stage_cli always records its own outcome before exiting; still "running" means the
        # subprocess itself crashed (e.g. segfault) before it could write anything back.
        tail = (proc.stderr or "")[-2000:]
        with get_conn() as conn:
            store.set_stage(conn, job_id, stage, "failed",
                            detail=f"stage subprocess crashed (exit {proc.returncode}): {tail or '(no stderr)'}")


def run_document_job(job_id: int) -> None:
    with get_conn() as conn:
        store.set_job_status(conn, job_id, "running")
    for stage in DOCUMENT_REMAINING_STAGES:
        _run_stage_subprocess(job_id, stage)
        with get_conn() as conn:
            job = store.get_job(conn, job_id)
        stage_row = next(s for s in job["stages"] if s["name"] == stage)
        if stage_row["status"] == "failed":
            with get_conn() as conn:
                store.set_job_status(conn, job_id, "failed", error=f"{stage}: {stage_row.get('detail')}")
                store.skip_remaining(conn, job_id, stage, detail="skipped: an earlier stage failed")
            return
    with get_conn() as conn:
        from app.ingest import document_stages as ds

        document_id = store.get_job(conn, job_id)["document_id"]
        summary = ds.document_summary(conn, document_id) if document_id else {}
        job = store.get_job(conn, job_id)
        result = job.get("result") or {}
        result.update(summary)
        store.merge_result(conn, job_id, summary)
        store.set_job_status(conn, job_id, "done")


def start_document_job(job_id: int) -> None:
    threading.Thread(target=run_document_job, args=(job_id,), daemon=True, name=f"ingest-doc-{job_id}").start()


def start_satellite_job(job_id: int) -> None:
    from app.ingest.satellite_stages import run_satellite_job

    threading.Thread(target=run_satellite_job, args=(job_id,), daemon=True,
                     name=f"ingest-sat-{job_id}").start()


def resume_stale_jobs() -> int:
    """Call once at API startup: a `queued`/`running` job left over from a killed process can
    never finish (no thread is driving it any more) — mark it failed honestly instead of leaving
    it stuck forever."""
    with get_conn() as conn:
        return store.mark_stale_running_as_failed(conn)
