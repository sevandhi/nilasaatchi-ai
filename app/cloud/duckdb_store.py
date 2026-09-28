"""DuckDB re-implementations of the list-with-filter routers (`findings`, `documents`,
`review-queue`, `runs`) over the Parquet tables `scripts/cloud_export.py` writes to
`tables/*.parquet`. Every filter/order/limit/offset here must match its live-API counterpart
exactly (see the docstring at the top of each function for the router it mirrors) — see
`app/cloud/ENDPOINTS.md` for the one documented deviation (documents' `q` full-text filter).

Values are pre-formatted to already be JSON-ready at export time (see `scripts/cloud_export.py`'s
`_iso_z`/`_num`/`_json_or_none`) so no reformatting happens here beyond `json.loads` on the columns
that were JSON-encoded as strings (heterogeneous jsonb columns Arrow can't type as a single struct).
"""
from __future__ import annotations

import json
from typing import Any

import duckdb

from app.cloud.snapshot import SnapshotStore


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _connect(store: SnapshotStore, table: str) -> tuple[duckdb.DuckDBPyConnection, str] | None:
    path = store.local_path(f"tables/{table}.parquet")
    if path is None:
        return None
    return duckdb.connect(), str(path)


# --------------------------------------------------------------------------------- findings

FINDING_COLS = ["id", "finding_key", "category", "severity", "confidence", "parcel_uid", "village",
                "unit_id", "block_id", "title", "evidence_level", "verdict", "status", "caveats",
                "created_at"]


def list_findings(store: SnapshotStore, *, category: str | None = None, severity: str | None = None,
                  village: str | None = None, block: int | None = None, level: str | None = None,
                  limit: int = 100, offset: int = 0) -> dict | None:
    """Mirrors `GET /findings` (app/api/routers/findings.py `list_findings`): always
    `status = 'open'`, then optional exact-match filters, `ORDER BY severity, id`."""
    conn = _connect(store, "findings")
    if conn is None:
        return None
    con, path = conn
    where = ["status = 'open'"]
    params: list[Any] = []
    if category:
        where.append("category = ?")
        params.append(category)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if village:
        where.append("village = ?")
        params.append(village)
    if block is not None:
        where.append("block_id = ?")
        params.append(block)
    if level:
        where.append("evidence_level = ?")
        params.append(level)
    clause = " AND ".join(where)

    total = con.execute(f"SELECT count(*) FROM read_parquet(?) WHERE {clause}", [path, *params]).fetchone()[0]
    cols = ", ".join(FINDING_COLS)
    rows = _rows(
        con,
        f"SELECT {cols} FROM read_parquet(?) WHERE {clause} ORDER BY severity, id LIMIT ? OFFSET ?",
        [path, *params, limit, offset],
    )
    for r in rows:
        r["caveats"] = list(r["caveats"] or [])
    return {"items": rows, "total": total, "limit": limit, "offset": offset,
           "source": "finding table (db/migrations/0014_findings.sql)"}


# --------------------------------------------------------------------------------- documents

DOCUMENT_COLS = ["id", "sha256", "path", "classified_type", "scheme_relevance", "village", "unit_no",
                 "block_no", "doc_no", "doc_date", "pages", "status", "stage", "folder_label",
                 "folder_type_mismatch"]


def list_documents(store: SnapshotStore, *, village: str | None = None, classified_type: str | None = None,
                   stage: str | None = None, scheme_relevance: str | None = None, q: str | None = None,
                   limit: int = 50, offset: int = 0) -> dict | None:
    """Mirrors `GET /documents` (app/api/routers/documents.py `list_documents`).

    Deviation (documented, see ENDPOINTS.md): the live API's `q` also full-text-searches each
    document's OCR page text (`page.tsv @@ plainto_tsquery(...)`) — the cloud snapshot does not
    carry per-page OCR text, so `q` here only matches `path`/`doc_no` (ILIKE substring). A `q` that
    only matched via page text in the live API will return fewer/no rows here.
    """
    conn = _connect(store, "documents")
    if conn is None:
        return None
    con, path = conn
    where: list[str] = []
    params: list[Any] = []
    if village:
        where.append("village = ?")
        params.append(village)
    if classified_type:
        where.append("classified_type = ?")
        params.append(classified_type)
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if scheme_relevance:
        where.append("scheme_relevance = ?")
        params.append(scheme_relevance)
    if q:
        where.append("(path ILIKE ? OR doc_no ILIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = con.execute(f"SELECT count(*) FROM read_parquet(?) {clause}", [path, *params]).fetchone()[0]
    cols = ", ".join(DOCUMENT_COLS)
    rows = _rows(
        con, f"SELECT {cols} FROM read_parquet(?) {clause} ORDER BY id LIMIT ? OFFSET ?",
        [path, *params, limit, offset],
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------------- review queue

REVIEW_COLS = ["id", "content_hash", "document_id", "page_id", "page_ref", "extraction_id", "reason",
              "detail", "status", "decided_value", "decided_by", "decided_at", "created_at"]


def list_review_queue(store: SnapshotStore, *, status: str = "open", reason: str | None = None,
                      limit: int = 100, offset: int = 0) -> dict | None:
    """Mirrors `GET /review-queue` (app/api/routers/review.py `review_queue`) — note the live
    response has no `limit`/`offset` keys (see `ReviewListResponse`), only `items`/`total`."""
    conn = _connect(store, "review_queue")
    if conn is None:
        return None
    con, path = conn
    where = ["status = ?"]
    params: list[Any] = [status]
    if reason:
        where.append("reason = ?")
        params.append(reason)
    clause = " AND ".join(where)

    total = con.execute(f"SELECT count(*) FROM read_parquet(?) WHERE {clause}", [path, *params]).fetchone()[0]
    cols = ", ".join(REVIEW_COLS)
    rows = _rows(
        con,
        f"SELECT {cols} FROM read_parquet(?) WHERE {clause} ORDER BY created_at LIMIT ? OFFSET ?",
        [path, *params, limit, offset],
    )
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
        r["decided_value"] = json.loads(r["decided_value"]) if r["decided_value"] else None
    return {"items": rows, "total": total}


# --------------------------------------------------------------------------------- runs

RUN_COLS = ["id", "workspace_id", "status", "request", "created_at", "updated_at"]


def list_runs(store: SnapshotStore, *, status: str | None = None, limit: int = 50,
             offset: int = 0) -> dict | None:
    """Mirrors `GET /runs` (app/api/routers/runs.py `list_runs`) — read-only run *history* only;
    creating/streaming/resuming a run (POST /runs, /runs/{id}/events, /runs/{id}/clarify) stays in
    the local app (D-067)."""
    conn = _connect(store, "runs")
    if conn is None:
        return None
    con, path = conn
    where = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = con.execute(f"SELECT count(*) FROM read_parquet(?) {clause}", [path, *params]).fetchone()[0]
    cols = ", ".join(RUN_COLS)
    rows = _rows(
        con, f"SELECT {cols} FROM read_parquet(?) {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [path, *params, limit, offset],
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


def document_meta(store: SnapshotStore, document_id: int) -> dict | None:
    """Page count and plain details of one document (the document viewer's prev / next), from the catalogue table."""
    conn = _connect(store, "documents")
    if conn is None:
        return None
    con, path = conn
    row = con.execute("SELECT id, pages, classified_type, stage, village, folder_label, "
                      "regexp_replace(path, '^.*/', '') AS file_name FROM read_parquet(?) WHERE id = ?",
                      [path, document_id]).fetchone()
    if row is None:
        return None
    return dict(zip(["id", "pages", "classified_type", "stage", "village", "folder_label", "file_name"], row, strict=True))
