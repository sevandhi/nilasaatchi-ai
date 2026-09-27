"""Plain psycopg connection helper for app/ingest's own processes and `stage_cli` subprocesses.

Kept separate from `app.api.db` (which holds the FastAPI process's pooled connection): stage_cli
runs as a fresh `uv run python -m ...` subprocess per stage, so it needs its own short-lived
connection, not a pool shared with the API's async event loop.

Deliberately plain **tuple** rows (psycopg3's default), not `dict_row`: every `pipeline.*` module
this package calls into (pipeline.catalog, pipeline.classify, pipeline.extract, pipeline.load,
pipeline.match, pipeline.findings) assumes tuple-row unpacking on a connection it did not open
itself. `app.ingest.store` opens its own `dict_row` cursor per query, so it works correctly on top
of this plain connection (or on `app.api.db`'s dict_row pool, when called from the API process).
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()
