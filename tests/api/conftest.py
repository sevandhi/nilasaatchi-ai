"""API test fixtures. `client`/`pg_conn` are `db`-marked (skip if PostGIS isn't reachable, same
pattern as tests/gis/test_db_integration.py); everything else in tests/api/ is a pure unit test."""
from __future__ import annotations

import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _database_url() -> str | None:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from dotenv import dotenv_values

    return dotenv_values(ROOT / ".env").get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture()
def pg_conn(database_url):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    try:
        conn = psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row)
    except psycopg.OperationalError as e:
        pytest.skip(f"PostGIS not reachable: {e}")
    if conn.execute("SELECT to_regclass('run')").fetchone()["to_regclass"] is None:
        pytest.skip("run/workspace tables not migrated (run: make migrate)")
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture()
def client(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    # Pin app/api/runner.py to the deterministic stub runner regardless of whether the real
    # app.agent.run_request/resume_run exist today — tests/api must stay green independent of the
    # agent's day-to-day state (see app/api/runner.py's `_force_stub`).
    monkeypatch.setenv("AGENT_FORCE_STUB", "1")
    from fastapi.testclient import TestClient

    from app.api import db as db_module
    db_module.reset_pool()
    from app.api.config import get_settings
    get_settings.cache_clear()
    from app.api.main import app
    with TestClient(app) as c:
        yield c
    db_module.reset_pool()
