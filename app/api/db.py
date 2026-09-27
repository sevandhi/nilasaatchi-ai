"""psycopg connection helper: one small pool shared by the API process.

Kept intentionally simple (psycopg3 `ConnectionPool`) rather than SQLAlchemy, since every query
here is either a short guarded SELECT or a single-row upsert; the agent's own guarded SQL tool
(app/tools, owned by agent-architect) is a separate, read-only path.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.api.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=8, open=True,
                               kwargs={"row_factory": dict_row})
    return _pool


def reset_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn
