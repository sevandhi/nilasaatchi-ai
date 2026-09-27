"""KG access for tools: trusted parameterised queries (app user) and guarded read-only queries (agent_ro)."""
from __future__ import annotations

import datetime as _dt
import decimal
from typing import Any

import psycopg

STATEMENT_TIMEOUT_MS = 5000


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return int(f) if f.is_integer() and v == v.to_integral_value() and abs(f) < 1e15 else f
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, memoryview):
        return None
    return v


def rows_as_dicts(cur) -> list[dict]:
    cols = [d.name for d in cur.description] if cur.description else []
    return [{c: _jsonable(v) for c, v in zip(cols, r, strict=True)} for r in cur.fetchall()]


def query(dsn: str, sql: str, params: Any = None, *, readonly_role: bool = True,
          timeout_ms: int = STATEMENT_TIMEOUT_MS) -> list[dict]:
    """Run one SELECT. ``readonly_role`` wraps it in ``BEGIN READ ONLY; SET LOCAL ROLE agent_ro``."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.transaction():
            if readonly_role:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL ROLE agent_ro")
            conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cur = conn.execute(sql, params)
            out = rows_as_dicts(cur)
        return out
