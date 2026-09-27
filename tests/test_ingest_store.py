"""Unit tests for app.ingest.store (ingest_job CRUD + stage bookkeeping). Needs the real PostGIS
container (db-marked, same pattern as tests/api/conftest.py) but makes no model/router calls."""
from __future__ import annotations

import os
import pathlib

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.rows import dict_row

from app.ingest import store

ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.db


def _database_url() -> str | None:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return dotenv_values(ROOT / ".env").get("DATABASE_URL")


@pytest.fixture(scope="module")
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture()
def conn(database_url):
    try:
        c = psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row)
    except psycopg.OperationalError as e:
        pytest.skip(f"PostGIS not reachable: {e}")
    if c.execute("SELECT to_regclass('ingest_job')").fetchone()["to_regclass"] is None:
        pytest.skip("ingest_job not migrated (run: make migrate)")
    yield c
    c.rollback()
    c.close()


def _cleanup(conn, job_id: int) -> None:
    conn.execute("DELETE FROM ingest_job WHERE id = %s", (job_id,))
    conn.commit()


def test_init_stages_all_pending():
    stages = store.init_stages(store.DOCUMENT_STAGE_NAMES)
    assert [s["name"] for s in stages] == store.DOCUMENT_STAGE_NAMES
    assert all(s["status"] == "pending" for s in stages)
    assert all(s["started_at"] is None and s["finished_at"] is None for s in stages)


def test_create_and_get_job_roundtrip(conn):
    job_id = store.create_job(conn, "document", "test.pdf", {"path": "data/inbox/x.pdf"},
                              store.DOCUMENT_STAGE_NAMES)
    try:
        job = store.get_job(conn, job_id)
        assert job is not None
        assert job["kind"] == "document"
        assert job["status"] == "queued"
        assert job["filename"] == "test.pdf"
        assert len(job["stages"]) == len(store.DOCUMENT_STAGE_NAMES)
        assert job["params"]["path"] == "data/inbox/x.pdf"
        assert job["document_id"] is None
    finally:
        _cleanup(conn, job_id)


def test_get_job_missing_returns_none(conn):
    assert store.get_job(conn, 999_999_999) is None


def test_set_stage_running_then_done_sets_timestamps(conn):
    job_id = store.create_job(conn, "document", "a.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        store.set_stage(conn, job_id, "catalog", "running")
        job = store.get_job(conn, job_id)
        catalog = next(s for s in job["stages"] if s["name"] == "catalog")
        assert catalog["status"] == "running"
        assert catalog["started_at"] is not None
        assert catalog["finished_at"] is None

        store.set_stage(conn, job_id, "catalog", "done", detail="3 pages catalogued")
        job = store.get_job(conn, job_id)
        catalog = next(s for s in job["stages"] if s["name"] == "catalog")
        assert catalog["status"] == "done"
        assert catalog["finished_at"] is not None
        assert catalog["detail"] == "3 pages catalogued"
        # untouched stages stay pending
        classify = next(s for s in job["stages"] if s["name"] == "classify")
        assert classify["status"] == "pending"
    finally:
        _cleanup(conn, job_id)


def test_skip_remaining_after_failure(conn):
    job_id = store.create_job(conn, "document", "a.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        store.set_stage(conn, job_id, "catalog", "done")
        store.set_stage(conn, job_id, "classify", "failed", detail="boom")
        store.skip_remaining(conn, job_id, "classify", detail="skipped: an earlier stage failed")
        job = store.get_job(conn, job_id)
        by_name = {s["name"]: s for s in job["stages"]}
        assert by_name["catalog"]["status"] == "done"
        assert by_name["classify"]["status"] == "failed"
        for name in ("extract", "load", "match", "findings"):
            assert by_name[name]["status"] == "skipped"
    finally:
        _cleanup(conn, job_id)


def test_merge_result_accumulates(conn):
    job_id = store.create_job(conn, "document", "a.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        store.merge_result(conn, job_id, {"document_id": 42, "pages": 3})
        store.merge_result(conn, job_id, {"facts": 5})
        job = store.get_job(conn, job_id)
        assert job["result"] == {"document_id": 42, "pages": 3, "facts": 5}
    finally:
        _cleanup(conn, job_id)


def test_set_job_status_done_sets_finished_at_once(conn):
    job_id = store.create_job(conn, "document", "a.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        store.set_job_status(conn, job_id, "running")
        job = store.get_job(conn, job_id)
        assert job["status"] == "running"
        assert job["started_at"] is not None
        assert job["finished_at"] is None

        store.set_job_status(conn, job_id, "done", document_id=7)
        job = store.get_job(conn, job_id)
        assert job["status"] == "done"
        assert job["finished_at"] is not None
        assert job["document_id"] == 7
    finally:
        _cleanup(conn, job_id)


def test_mark_stale_running_as_failed(conn):
    job_id = store.create_job(conn, "document", "a.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        store.set_job_status(conn, job_id, "running")
        store.set_stage(conn, job_id, "catalog", "running")
        n = store.mark_stale_running_as_failed(conn)
        assert n >= 1
        job = store.get_job(conn, job_id)
        assert job["status"] == "failed"
        assert job["error"] == "interrupted"
        catalog = next(s for s in job["stages"] if s["name"] == "catalog")
        assert catalog["status"] == "failed"
        assert catalog["detail"] == "interrupted"
    finally:
        _cleanup(conn, job_id)


def test_running_satellite_job_detects_queued_and_running(conn):
    assert store.running_satellite_job(conn) is None  # sanity: nothing of ours pending yet
    job_id = store.create_job(conn, "satellite", None, {}, store.SATELLITE_STAGE_NAMES)
    try:
        assert store.running_satellite_job(conn) == job_id
        store.set_job_status(conn, job_id, "done")
        assert store.running_satellite_job(conn) is None
    finally:
        _cleanup(conn, job_id)


def test_to_job_response_shape(conn):
    job_id = store.create_job(conn, "document", "shape.pdf", {}, store.DOCUMENT_STAGE_NAMES)
    try:
        job = store.get_job(conn, job_id)
        resp = store.to_job_response(job)
        assert set(resp) == {"id", "kind", "status", "filename", "created_at", "started_at",
                             "finished_at", "document_id", "stages", "result", "error"}
    finally:
        _cleanup(conn, job_id)
