"""`ingest_job` CRUD (db/migrations/0015_ingest_job.sql). Fetch-modify-write on the `stages` jsonb
array: every job is driven by exactly one orchestrator thread (app.ingest.orchestrator) at a time,
so there is never a concurrent writer to worry about.

Every query here opens its own `dict_row` cursor, independent of the caller's connection default —
`app.api.db`'s pool already defaults to dict_row, but `app.ingest.db` (used by `stage_cli`
subprocesses, so it stays compatible with the pipeline.* modules that assume plain tuple rows)
does not.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

DOCUMENT_STAGE_NAMES = ["store", "catalog", "classify", "extract", "load", "match", "findings"]
SATELLITE_STAGE_NAMES = ["inventory", "extract", "features", "classify", "findings"]


def _q(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _q1(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _x(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_stages(names: list[str]) -> list[dict[str, Any]]:
    return [{"name": n, "status": "pending", "started_at": None, "finished_at": None, "detail": None}
            for n in names]


def create_job(conn: psycopg.Connection, kind: str, filename: str | None, params: dict,
               stage_names: list[str], status: str = "queued") -> int:
    row = _q1(
        conn,
        "INSERT INTO ingest_job (kind, status, filename, stages, params) "
        "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb) RETURNING id",
        (kind, status, filename, json.dumps(init_stages(stage_names)), json.dumps(params, default=str)),
    )
    conn.commit()
    return row["id"]


def _row_to_dict(row: dict | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("stages", "params", "result"):
        v = d.get(k)
        if isinstance(v, str):
            d[k] = json.loads(v)
    return d


def get_job(conn: psycopg.Connection, job_id: int) -> dict | None:
    return _row_to_dict(_q1(conn, "SELECT * FROM ingest_job WHERE id = %s", (job_id,)))


def list_jobs(conn: psycopg.Connection, kind: str | None = None, limit: int = 20) -> list[dict]:
    if kind:
        rows = _q(conn, "SELECT * FROM ingest_job WHERE kind = %s ORDER BY id DESC LIMIT %s", (kind, limit))
    else:
        rows = _q(conn, "SELECT * FROM ingest_job ORDER BY id DESC LIMIT %s", (limit,))
    return [_row_to_dict(r) for r in rows]


def running_satellite_job(conn: psycopg.Connection) -> int | None:
    row = _q1(conn, "SELECT id FROM ingest_job WHERE kind = 'satellite' AND status IN ('queued', 'running') "
                    "ORDER BY id DESC LIMIT 1")
    return row["id"] if row else None


def set_stage(conn: psycopg.Connection, job_id: int, name: str, status: str, detail: str | None = None) -> None:
    job = get_job(conn, job_id)
    if job is None:
        return
    stages = job["stages"]
    now = _now()
    found = False
    for s in stages:
        if s["name"] == name:
            found = True
            if status == "running" and not s.get("started_at"):
                s["started_at"] = now
            if status in ("done", "failed", "skipped"):
                s["finished_at"] = now
            s["status"] = status
            if detail is not None:
                s["detail"] = detail
            break
    if not found:
        stages.append({"name": name, "status": status, "started_at": now if status == "running" else None,
                       "finished_at": now if status in ("done", "failed", "skipped") else None,
                       "detail": detail})
    _x(conn, "UPDATE ingest_job SET stages = %s::jsonb, updated_at = now() WHERE id = %s",
      (json.dumps(stages, default=str), job_id))
    conn.commit()


def skip_remaining(conn: psycopg.Connection, job_id: int, from_stage: str, detail: str = "skipped") -> None:
    job = get_job(conn, job_id)
    if job is None:
        return
    stages = job["stages"]
    hit = False
    for s in stages:
        if s["name"] == from_stage:
            hit = True
            continue
        if hit and s["status"] == "pending":
            s["status"] = "skipped"
            s["detail"] = detail
    _x(conn, "UPDATE ingest_job SET stages = %s::jsonb, updated_at = now() WHERE id = %s",
      (json.dumps(stages, default=str), job_id))
    conn.commit()


def merge_result(conn: psycopg.Connection, job_id: int, patch: dict) -> None:
    _x(conn, "UPDATE ingest_job SET result = COALESCE(result, '{}'::jsonb) || %s::jsonb, updated_at = now() "
            "WHERE id = %s", (json.dumps(patch, default=str), job_id))
    conn.commit()


def set_document_id(conn: psycopg.Connection, job_id: int, document_id: int) -> None:
    _x(conn, "UPDATE ingest_job SET document_id = %s, updated_at = now() WHERE id = %s", (document_id, job_id))
    conn.commit()


def set_job_status(conn: psycopg.Connection, job_id: int, status: str, *, error: str | None = None,
                   document_id: int | None = None) -> None:
    job = get_job(conn, job_id)
    if job is None:
        return
    started_at = job["started_at"]
    finished_at = job["finished_at"]
    if status == "running" and started_at is None:
        started_at = _now()
    if status in ("done", "failed", "duplicate") and finished_at is None:
        finished_at = _now()
    _x(conn,
       "UPDATE ingest_job SET status = %s, error = COALESCE(%s, error), "
       "document_id = COALESCE(%s, document_id), started_at = COALESCE(started_at, %s), "
       "finished_at = COALESCE(finished_at, %s), updated_at = now() WHERE id = %s",
       (status, error, document_id, started_at, finished_at, job_id))
    conn.commit()


def mark_stale_running_as_failed(conn: psycopg.Connection) -> int:
    """Called once at API startup (a `running`/`queued` job left over from a killed API process
    can never finish; a stale-running stage entry is not evidence of a real, in-flight stage)."""
    rows = _q(conn, "SELECT id FROM ingest_job WHERE status IN ('queued', 'running')")
    n = 0
    for r in rows:
        job_id = r["id"]
        job = get_job(conn, job_id)
        stages = job["stages"]
        for s in stages:
            if s["status"] in ("pending", "running"):
                s["status"] = "failed"
                s["detail"] = "interrupted"
                s["finished_at"] = _now()
        _x(conn,
           "UPDATE ingest_job SET status = 'failed', error = 'interrupted', stages = %s::jsonb, "
           "finished_at = COALESCE(finished_at, now()), updated_at = now() WHERE id = %s",
           (json.dumps(stages, default=str), job_id))
        n += 1
    conn.commit()
    return n


def to_job_response(job: dict) -> dict:
    """Shape a DB row into the API `Job` model (app/api/schemas.py `IngestJob`)."""
    return {
        "id": job["id"], "kind": job["kind"], "status": job["status"], "filename": job.get("filename"),
        "created_at": job["created_at"], "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"), "document_id": job.get("document_id"),
        "stages": job["stages"], "result": job.get("result"), "error": job.get("error"),
    }
