"""`uv run python -m app.ingest.stage_cli document <stage> --job-id <id>`

Runs exactly one document-ingest stage in its own subprocess: pipeline.extract's OCR/VLM path (and
Tesseract's docker exec) has segfaulted in-process before (D-041-adjacent incidents), so isolating
each stage this way means a crash there fails only that one stage — never the long-running API
process. The orchestrator (app/ingest/orchestrator.py, running in the API process) only launches
this subprocess per stage and re-reads the job row afterwards; every DB write (stage status,
document_id, result) happens in here so the orchestrator never needs to parse stdout for anything
but a crash it didn't catch itself.

Exit code 0 always (even on a stage-level "failed" status, which is recorded in the DB, not via
exit code) unless something raises before the `try` block can record it (e.g. cannot open the DB
at all) — the orchestrator treats a non-zero exit whose stage row is still `running` as a bare
crash and records that itself.
"""
from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from app.ingest import document_stages as ds
from app.ingest import store
from app.ingest.db import get_conn


def _run_one(conn, job: dict, stage: str) -> dict:
    params = job.get("params") or {}
    document_id = job.get("document_id")
    if stage == "catalog":
        path = Path(params["path"])
        if not path.is_absolute():
            path = ds.REPO_ROOT / path
        folder_label = params.get("folder_label") or "ingest_upload"
        res = ds.run_catalog_stage(conn, path, folder_label)
        if res.get("document_id"):
            store.set_document_id(conn, job["id"], res["document_id"])
        return res
    if document_id is None:
        return {"status": "failed", "detail": f"no document_id on job {job['id']} before stage {stage!r} "
                                              "(catalog did not run or did not record one)"}
    if stage == "classify":
        return ds.run_classify_stage(conn, document_id, params.get("doc_type"))
    if stage == "extract":
        return ds.run_extract_stage(conn, document_id)
    if stage == "load":
        return ds.run_load_stage(conn, document_id)
    if stage == "match":
        return ds.run_match_stage(conn)
    if stage == "findings":
        return ds.run_findings_stage(conn, document_id)
    raise ValueError(f"unknown document stage {stage!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["document"])
    ap.add_argument("stage", choices=["catalog", "classify", "extract", "load", "match", "findings"])
    ap.add_argument("--job-id", type=int, required=True)
    args = ap.parse_args()

    with get_conn() as conn:
        job = store.get_job(conn, args.job_id)
        if job is None:
            raise SystemExit(f"ingest_job {args.job_id} not found")
        store.set_stage(conn, args.job_id, args.stage, "running")

    try:
        with get_conn() as conn:
            res = _run_one(conn, job, args.stage)
    except Exception as exc:  # noqa: BLE001 - always record, never let a bare traceback exit the process
        detail = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc()[-2000:]
        with get_conn() as conn:
            store.set_stage(conn, args.job_id, args.stage, "failed", detail=detail)
        return

    with get_conn() as conn:
        store.set_stage(conn, args.job_id, args.stage, res.get("status", "failed"), detail=res.get("detail"))
        patch = {k: v for k, v in res.items() if k not in ("status", "detail")}
        if patch:
            store.merge_result(conn, args.job_id, patch)


if __name__ == "__main__":
    main()
