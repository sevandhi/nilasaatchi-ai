"""Satellite-refresh job: runs `uv run python -m planet.refresh --json` as a subprocess (owned by
the eo-engineer, built in parallel with this ingest path — see plan.md P3 follow-ups) and parses
its `STAGE <name> <status> <detail...>` progress lines + final `RESULT <json>` line into the
job's `stages`/`result`. Findings are re-run by this package afterwards (not by planet.refresh).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.ingest import document_stages as ds
from app.ingest import store

REPO_ROOT = Path(__file__).resolve().parents[2]
REFRESH_STAGES = ["inventory", "extract", "features", "classify"]  # planet.refresh's own 4 stages
SUBPROCESS_TIMEOUT_S = 3600


def run_satellite_job(job_id: int) -> None:
    from app.ingest.db import get_conn

    with get_conn() as conn:
        store.set_job_status(conn, job_id, "running")
        job = store.get_job(conn, job_id)
    max_scenes = (job.get("params") or {}).get("max_scenes")

    cmd = ["uv", "run", "python", "-m", "planet.refresh", "--json"]
    if max_scenes:
        cmd += ["--max-scenes", str(int(max_scenes))]

    for name in REFRESH_STAGES:
        with get_conn() as conn:
            store.set_stage(conn, job_id, name, "pending")

    result_payload: dict | None = None
    stderr_tail = ""
    try:
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except OSError as exc:
        with get_conn() as conn:
            for name in REFRESH_STAGES:
                store.set_stage(conn, job_id, name, "failed", detail=f"could not start planet.refresh: {exc}")
            store.set_job_status(conn, job_id, "failed", error=f"could not start planet.refresh: {exc}")
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        if line.startswith("STAGE "):
            parts = line.split(" ", 3)
            if len(parts) >= 3:
                name, status = parts[1], parts[2]
                detail = parts[3] if len(parts) > 3 else None
                with get_conn() as conn:
                    store.set_stage(conn, job_id, name, status, detail=detail)
        elif line.startswith("RESULT "):
            try:
                result_payload = json.loads(line[len("RESULT "):])
            except ValueError:
                result_payload = {"parse_error": line[:500]}
        else:
            print(line, file=sys.stderr)  # forward any other planet.refresh output to the API's own log

    proc.wait(timeout=SUBPROCESS_TIMEOUT_S)
    if proc.stderr is not None:
        stderr_tail = proc.stderr.read()[-2000:]

    if proc.returncode != 0:
        with get_conn() as conn:
            job = store.get_job(conn, job_id)
            for s in job["stages"]:
                if s["status"] in ("pending", "running"):
                    store.set_stage(conn, job_id, s["name"], "failed",
                                    detail=f"planet.refresh exited {proc.returncode}: {stderr_tail or '(no stderr)'}")
            store.set_job_status(conn, job_id, "failed",
                                 error=f"planet.refresh exited {proc.returncode}: {stderr_tail[:500]}")
        return

    with get_conn() as conn:
        if result_payload:
            store.merge_result(conn, job_id, result_payload)
        # planet.refresh may finish without emitting every STAGE line for a no-op run; mark any
        # stage it never touched as done rather than leave it stuck at "pending".
        job = store.get_job(conn, job_id)
        for s in job["stages"]:
            if s["status"] == "pending":
                store.set_stage(conn, job_id, s["name"], "done", detail="no change reported")

    # findings: this package's own responsibility (not planet.refresh's), same engine as the
    # document path and `make findings`.
    with get_conn() as conn:
        store.set_stage(conn, job_id, "findings", "running")
        try:
            res = ds.run_findings_stage(conn, document_id=None)
        except Exception as exc:  # noqa: BLE001
            store.set_stage(conn, job_id, "findings", "failed", detail=f"{type(exc).__name__}: {exc}")
            store.set_job_status(conn, job_id, "failed", error=f"findings stage crashed: {exc}")
            return
        store.set_stage(conn, job_id, "findings", res["status"], detail=res.get("detail"))
        store.merge_result(conn, job_id, {"new_findings": res.get("new_findings", 0)})
        store.set_job_status(conn, job_id, "done")
