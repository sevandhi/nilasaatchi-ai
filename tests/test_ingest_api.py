"""API-level unit tests for /ingest/* (app/api/routers/ingest.py), against the real PostGIS
container but with the background orchestrator stubbed out — no subprocess, no model/router calls,
no quota. Exercises exactly the request/response contract."""
from __future__ import annotations

import io
import os
import pathlib

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[1]


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
def pg_conn(database_url):
    try:
        c = psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row)
    except psycopg.OperationalError as e:
        pytest.skip(f"PostGIS not reachable: {e}")
    if c.execute("SELECT to_regclass('ingest_job')").fetchone()["to_regclass"] is None:
        pytest.skip("ingest_job not migrated (run: make migrate)")
    yield c
    c.rollback()
    c.close()


@pytest.fixture()
def _clean_inbox():
    """`POST /ingest/documents` always writes to the real data/inbox/ (git-ignored; same path the
    live endpoint uses) so path-relative-to-REPO_ROOT logic is exercised for real. Remove only the
    files a test added."""
    from app.ingest.document_stages import INBOX_DIR

    before = set(INBOX_DIR.glob("*.pdf")) if INBOX_DIR.exists() else set()
    yield
    after = set(INBOX_DIR.glob("*.pdf")) if INBOX_DIR.exists() else set()
    for p in after - before:
        p.unlink(missing_ok=True)


@pytest.fixture()
def client(database_url, monkeypatch, _clean_inbox):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("AGENT_FORCE_STUB", "1")
    from fastapi.testclient import TestClient

    from app.api import db as db_module
    db_module.reset_pool()
    from app.api.config import get_settings
    get_settings.cache_clear()

    from app.api.routers import ingest as ingest_router

    # No real background processing in unit tests: the orchestrator would spawn `uv run` subprocesses
    # that call the router (quota) and OCR docker. `_started` records what *would* have run.
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(ingest_router.orchestrator, "start_document_job",
                        lambda job_id: started.append(("document", job_id)))
    monkeypatch.setattr(ingest_router.orchestrator, "start_satellite_job",
                        lambda job_id: started.append(("satellite", job_id)))

    from app.api.main import app
    with TestClient(app) as c:
        c._started = started  # type: ignore[attr-defined]
        yield c
    db_module.reset_pool()


def _mini_pdf_bytes(marker: bytes = b"") -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue() + marker  # marker keeps two calls' md5s distinct without touching PDF parsing


def _cleanup_jobs(pg_conn, job_ids: list[int]) -> None:
    if job_ids:
        pg_conn.execute("DELETE FROM ingest_job WHERE id = ANY(%s)", (job_ids,))
        pg_conn.commit()


pytestmark = pytest.mark.db


# ------------------------------------------------------------------------------- POST /ingest/documents
def test_upload_rejects_non_pdf(client):
    r = client.post("/ingest/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_fake_pdf_extension(client):
    r = client.post("/ingest/documents", files={"file": ("fake.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 400


def test_upload_queues_a_new_document_job(client, pg_conn):
    content = _mini_pdf_bytes(b"queue-test")
    r = client.post("/ingest/documents", files={"file": ("upload.pdf", content, "application/pdf")},
                    data={"village": "Melathattaparai"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["duplicate_of"] is None
    job_id = body["job_id"]
    try:
        assert ("document", job_id) in client._started
        r2 = client.get(f"/ingest/jobs/{job_id}")
        assert r2.status_code == 200
        job = r2.json()
        assert job["kind"] == "document"
        assert job["status"] == "queued"
        assert job["filename"] == "upload.pdf"
        names = [s["name"] for s in job["stages"]]
        assert names == ["store", "catalog", "classify", "extract", "load", "match", "findings"]
        by_name = {s["name"]: s for s in job["stages"]}
        assert by_name["store"]["status"] == "done"
        assert by_name["catalog"]["status"] == "pending"
    finally:
        _cleanup_jobs(pg_conn, [job_id])


def test_upload_is_rejected_over_size_limit(client, monkeypatch):
    from app.api.routers import ingest as ingest_router

    monkeypatch.setattr(ingest_router, "MAX_BYTES", 10)
    content = _mini_pdf_bytes(b"too-big")
    r = client.post("/ingest/documents", files={"file": ("big.pdf", content, "application/pdf")})
    assert r.status_code == 413


def test_upload_duplicate_of_existing_document(client, pg_conn):
    content = _mini_pdf_bytes(b"dup-test")
    import hashlib

    md5 = hashlib.md5(content).hexdigest()
    sha256 = hashlib.sha256(content).hexdigest()
    row = pg_conn.execute(
        "INSERT INTO document (sha256, md5, dup_group_id, bytes, pages, folder_label, path, all_paths, status) "
        "VALUES (%s, %s, %s, %s, 1, 'test', 'data/inbox/dup.pdf', ARRAY['data/inbox/dup.pdf'], 'catalogued') "
        "RETURNING id", (sha256, md5, sha256, len(content)),
    ).fetchone()
    pg_conn.commit()
    existing_id = row["id"]
    job_ids = []
    try:
        r = client.post("/ingest/documents", files={"file": ("upload.pdf", content, "application/pdf")})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "duplicate"
        assert body["duplicate_of"] == existing_id
        job_ids.append(body["job_id"])
        assert ("document", body["job_id"]) not in client._started  # no background processing at all

        job = client.get(f"/ingest/jobs/{body['job_id']}").json()
        assert job["status"] == "duplicate"
        assert job["document_id"] == existing_id
        by_name = {s["name"]: s for s in job["stages"]}
        assert by_name["store"]["status"] == "done"
        assert by_name["catalog"]["status"] == "skipped"
    finally:
        _cleanup_jobs(pg_conn, job_ids)
        pg_conn.execute("DELETE FROM document WHERE id = %s", (existing_id,))
        pg_conn.commit()


# ----------------------------------------------------------------------------------- GET /ingest/jobs
def test_list_jobs_filters_by_kind(client, pg_conn):
    content = _mini_pdf_bytes(b"list-test")
    r = client.post("/ingest/documents", files={"file": ("upload.pdf", content, "application/pdf")})
    job_id = r.json()["job_id"]
    try:
        r2 = client.get("/ingest/jobs", params={"kind": "document", "limit": 5})
        assert r2.status_code == 200
        jobs = r2.json()["jobs"]
        assert any(j["id"] == job_id for j in jobs)
        assert all(j["kind"] == "document" for j in jobs)
    finally:
        _cleanup_jobs(pg_conn, [job_id])


def test_get_job_404_for_unknown_id(client):
    r = client.get("/ingest/jobs/999999999")
    assert r.status_code == 404


# --------------------------------------------------------------------------- POST /ingest/satellite-refresh
def test_satellite_refresh_then_second_call_conflicts(client, pg_conn):
    r1 = client.post("/ingest/satellite-refresh", json={"max_scenes": 5})
    assert r1.status_code == 202
    job_id = r1.json()["job_id"]
    try:
        assert ("satellite", job_id) in client._started
        r2 = client.post("/ingest/satellite-refresh", json={})
        assert r2.status_code == 409
    finally:
        _cleanup_jobs(pg_conn, [job_id])


# ------------------------------------------------------------------------------ GET /ingest/satellite/status
def test_satellite_status_shape(client):
    r = client.get("/ingest/satellite/status")
    assert r.status_code == 200
    body = r.json()
    assert "latest_scene_date" in body
    assert "scene_count" in body
    assert "last_refresh_at" in body
    assert "running_job_id" in body


def test_upload_rejects_unknown_declared_type(client):
    r = client.post("/ingest/documents", files={"file": ("a.pdf", _mini_pdf_bytes(b"t"), "application/pdf")},
                    data={"doc_type": "NOT_A_TYPE"})
    assert r.status_code == 400 and "unknown document type" in r.json()["detail"]


def test_doc_types_lists_extractable_types(client):
    types = client.get("/ingest/doc-types").json()["doc_types"]
    assert "AWARD_7_2" in types and "CHITTA" in types
