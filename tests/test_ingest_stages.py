"""Unit tests for app.ingest.document_stages, with the model/OCR-calling stages stubbed out (no
quota). `run_catalog_stage` runs for real (hashing/text-layer/render — all local, no network) and
cleans up every row it creates; `run_classify_stage`/`run_extract_stage`'s router/VLM calls are
never reached in the cases exercised here (scheme_relevance='other_scheme' / doc_type not
extraction-eligible both short-circuit before any model call).

`conn` deliberately uses **plain tuple rows** (psycopg3's default), matching the connection
`app.ingest.db.get_conn()` gives every stage function in production: several `pipeline.*` modules
these stages call into (pipeline.match, pipeline.load.extractions) assume tuple-row unpacking on
whatever connection they're handed, and silently misbehave (each column *name* becomes a row
"value") on a `dict_row` connection instead of raising — this bit the first draft of these tests
(a dict_row `conn` fixture) before it was caught here. `_row`/`_rows` below open their own
`dict_row` cursor per assertion query, the same pattern app.ingest.store uses.
"""
from __future__ import annotations

import os
import pathlib

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg.rows import dict_row

from app.ingest import document_stages as ds

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
        c = psycopg.connect(database_url, connect_timeout=3)  # plain tuple rows, see module docstring
    except psycopg.OperationalError as e:
        pytest.skip(f"PostGIS not reachable: {e}")
    if c.execute("SELECT to_regclass('document')").fetchone()[0] is None:
        pytest.skip("document table not migrated (run: make migrate)")
    yield c
    c.rollback()
    c.close()


def _row(conn, sql: str, params: tuple = ()) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


@pytest.fixture()
def pdf_dir():
    """A repo-relative scratch dir (git-ignored data/, cleaned up after each test): every stage
    function under test resolves paths relative to REPO_ROOT (matching the real upload path,
    data/inbox/<md5>.pdf), so fixtures outside the repo tree (pytest's default tmp_path) don't
    exercise the same code path."""
    import shutil
    import uuid

    d = ROOT / "data" / "tmp_test_pdfs" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_pdf(pdf_dir: pathlib.Path, n_pages: int = 1) -> pathlib.Path:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(n_pages):
        doc.new_page(200, 200)
    path = pdf_dir / "sample.pdf"
    with open(path, "wb") as f:
        doc.save(f)
    return path


def _delete_document(conn, document_id: int) -> None:
    conn.execute("DELETE FROM document WHERE id = %s", (document_id,))  # cascades to page
    conn.commit()


# --------------------------------------------------------------------------------------- store
def test_store_pdf_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "INBOX_DIR", tmp_path / "inbox")
    content = b"%PDF-1.4\n%fake\n" + os.urandom(64)
    md5_1, path_1 = ds.store_pdf(content)
    md5_2, path_2 = ds.store_pdf(content)
    assert md5_1 == md5_2
    assert path_1 == path_2
    assert path_1.exists()
    assert path_1.name == f"{md5_1}.pdf"


def test_store_pdf_different_content_different_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "INBOX_DIR", tmp_path / "inbox")
    md5_a, path_a = ds.store_pdf(b"%PDF-1.4\naaaa")
    md5_b, path_b = ds.store_pdf(b"%PDF-1.4\nbbbb")
    assert md5_a != md5_b
    assert path_a != path_b


# --------------------------------------------------------------------------------- find_duplicate
def test_find_duplicate_none_for_unknown_md5(conn):
    assert ds.find_duplicate(conn, "0" * 32) is None


def test_find_duplicate_finds_existing_row(conn, pdf_dir):
    path = _make_pdf(pdf_dir)
    res = ds.run_catalog_stage(conn, path, "test_folder")
    document_id = res["document_id"]
    try:
        row = _row(conn, "SELECT md5 FROM document WHERE id = %s", (document_id,))
        assert ds.find_duplicate(conn, row["md5"]) == document_id
    finally:
        _delete_document(conn, document_id)


# ---------------------------------------------------------------------------------------- catalog
def test_run_catalog_stage_creates_document_and_pages(conn, pdf_dir):
    path = _make_pdf(pdf_dir, n_pages=2)
    res = ds.run_catalog_stage(conn, path, "ingest_upload")
    document_id = res["document_id"]
    try:
        assert res["status"] == "done"
        assert res["pages"] == 2
        doc = _row(conn, "SELECT pages, folder_label, path FROM document WHERE id = %s", (document_id,))
        assert doc["pages"] == 2
        assert doc["folder_label"] == "ingest_upload"
        pages = _row(conn, "SELECT count(*) AS n FROM page WHERE document_id = %s", (document_id,))
        assert pages["n"] == 2
    finally:
        _delete_document(conn, document_id)


def test_run_catalog_stage_is_idempotent(conn, pdf_dir):
    path = _make_pdf(pdf_dir)
    res1 = ds.run_catalog_stage(conn, path, "ingest_upload")
    res2 = ds.run_catalog_stage(conn, path, "ingest_upload")
    try:
        assert res1["document_id"] == res2["document_id"]
        n = _row(conn, "SELECT count(*) AS n FROM document WHERE id = %s", (res1["document_id"],))["n"]
        assert n == 1
    finally:
        _delete_document(conn, res1["document_id"])


# ----------------------------------------------------------------------------------- classify gate
def test_classify_stage_returns_failed_for_missing_document(conn):
    res = ds.run_classify_stage(conn, 999_999_999)
    assert res["status"] == "failed"


# ------------------------------------------------------------------------------------ extract gate
def test_extract_stage_skips_other_scheme_without_any_model_call(conn, pdf_dir):
    path = _make_pdf(pdf_dir)
    cat = ds.run_catalog_stage(conn, path, "ingest_upload")
    document_id = cat["document_id"]
    try:
        conn.execute("UPDATE document SET scheme_relevance = 'other_scheme' WHERE id = %s", (document_id,))
        conn.commit()
        res = ds.run_extract_stage(conn, document_id)
        assert res["status"] == "done"
        assert res["pages_selected"] == 0
        assert "other_scheme" in res["detail"]
    finally:
        _delete_document(conn, document_id)


def test_extract_stage_skips_unclassified_document(conn, pdf_dir):
    path = _make_pdf(pdf_dir)
    cat = ds.run_catalog_stage(conn, path, "ingest_upload")
    document_id = cat["document_id"]
    try:
        # freshly catalogued: classified_type is NULL, scheme_relevance is NULL -> "unknown"
        res = ds.run_extract_stage(conn, document_id)
        assert res["status"] == "done"
        assert res["pages_selected"] == 0
        assert "not extraction-eligible" in res["detail"]
    finally:
        _delete_document(conn, document_id)


def test_extract_stage_missing_document_fails(conn):
    res = ds.run_extract_stage(conn, 999_999_999)
    assert res["status"] == "failed"


# -------------------------------------------------------------------------------------- load/match
def test_load_stage_handles_missing_extraction_dir(conn):
    res = ds.run_load_stage(conn, 999_999_999)
    assert res["status"] == "done"
    assert res["pages"] == 0
    assert res["extractions"] == 0


def test_match_stage_runs_without_error(conn):
    res = ds.run_match_stage(conn)
    assert res["status"] == "done"


# ---------------------------------------------------------------------------------- document_summary
def test_document_summary_counts_zero_for_fresh_document(conn, pdf_dir):
    path = _make_pdf(pdf_dir)
    cat = ds.run_catalog_stage(conn, path, "ingest_upload")
    document_id = cat["document_id"]
    try:
        summary = ds.document_summary(conn, document_id)
        assert summary["document_id"] == document_id
        assert summary["pages"] == 1
        assert summary["extraction_rows"] == 0
        assert summary["facts"] == 0
        assert summary["events"] == 0
        assert summary["doc_type"] is None
    finally:
        _delete_document(conn, document_id)
