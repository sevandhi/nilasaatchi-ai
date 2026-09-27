"""Shared DB connection helper for pipeline.catalog / pipeline.classify / pipeline.raster.

Data-engineer owns db/migrations/0002_documents.sql (document, page) and this helper is a thin
wrapper so those three packages don't each reinvent it. Never import from packages owned by
other agents (pipeline.gis, pipeline.extract, ...).
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)


def get_conn(autocommit: bool = False) -> psycopg.Connection:
    """A fresh psycopg connection to DATABASE_URL. Caller is responsible for closing it
    (use as a context manager)."""
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=autocommit)


def table_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
        (name,),
    ).fetchone()
    return row is not None
