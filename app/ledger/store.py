"""Ledger storage backends (Postgres / SQLite) with append + verify."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .chain import GENESIS, canonical_json, entry_hash, scrub

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LedgerEntry:
    run_id: str
    seq: int
    node: str
    event_type: str
    ts: str
    payload: dict
    prev_hash: str
    hash: str


@dataclass
class VerifyReport:
    ok: bool
    runs_checked: int = 0
    entries_checked: int = 0
    first_bad: dict | None = None            # {run_id, seq, reason}
    heads: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "runs_checked": self.runs_checked, "entries_checked": self.entries_checked,
                "first_bad": self.first_bad}


class Ledger:
    """Append-only chain per run. Thread-safe within a process (one lock per ledger)."""

    def __init__(self, dsn: str | None = None, sqlite_path: str | Path | None = None):
        self.dsn = dsn
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        if not dsn and not sqlite_path:
            raise ValueError("Ledger needs a Postgres DSN or a SQLite path")
        self._lock = threading.Lock()
        self._heads: dict[str, tuple[int, str]] = {}
        if self.sqlite_path:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            with self._sq() as c:
                c.execute("CREATE TABLE IF NOT EXISTS agent_ledger (run_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                          "node TEXT NOT NULL, event_type TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL, "
                          "prev_hash TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (run_id, seq))")

    @property
    def backend(self) -> str:
        return "postgres" if self.dsn else "sqlite"

    def _sq(self):
        c = sqlite3.connect(str(self.sqlite_path), timeout=10)
        c.isolation_level = None
        return _Closing(c)

    def _pg(self):
        import psycopg
        return psycopg.connect(self.dsn, autocommit=True)

    # -- append -------------------------------------------------------------------------------------
    def head(self, run_id: str) -> tuple[int, str]:
        if run_id in self._heads:
            return self._heads[run_id]
        row = self._query_one("SELECT seq, hash FROM agent_ledger WHERE run_id = {p} ORDER BY seq DESC LIMIT 1",
                              (run_id,))
        h = (int(row[0]), row[1]) if row else (0, GENESIS)
        self._heads[run_id] = h
        return h

    def append(self, run_id: str, node: str, event_type: str, payload: dict[str, Any],
               ts: str | None = None) -> LedgerEntry:
        clean = json.loads(canonical_json(scrub(payload)))
        ts = ts or datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._lock:
            seq, prev = self.head(run_id)
            seq += 1
            h = entry_hash(prev, run_id, seq, node, clean)
            if self.dsn:
                from psycopg.types.json import Jsonb
                with self._pg() as c:
                    c.execute("INSERT INTO agent_ledger (run_id, seq, node, event_type, ts, payload, prev_hash, hash) "
                              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                              (run_id, seq, node, event_type, ts, Jsonb(clean), prev, h))
            else:
                with self._sq() as c:
                    c.execute("INSERT INTO agent_ledger VALUES (?,?,?,?,?,?,?,?)",
                              (run_id, seq, node, event_type, ts, canonical_json(clean), prev, h))
            self._heads[run_id] = (seq, h)
        return LedgerEntry(run_id, seq, node, event_type, ts, clean, prev, h)

    # -- read / verify ----------------------------------------------------------------------------------
    def _query(self, q: str, params=()) -> list[tuple]:
        if self.dsn:
            with self._pg() as c:
                return c.execute(q.replace("{p}", "%s"), params).fetchall()
        with self._sq() as c:
            return c.execute(q.replace("{p}", "?"), params).fetchall()

    def _query_one(self, q: str, params=()):
        rows = self._query(q, params)
        return rows[0] if rows else None

    def entries(self, run_id: str) -> list[LedgerEntry]:
        rows = self._query("SELECT run_id, seq, node, event_type, ts, payload, prev_hash, hash FROM agent_ledger "
                           "WHERE run_id = {p} ORDER BY seq", (run_id,))
        out = []
        for r in rows:
            payload = r[5] if isinstance(r[5], dict) else json.loads(r[5])
            out.append(LedgerEntry(r[0], int(r[1]), r[2], r[3], str(r[4]), payload, r[6], r[7]))
        return out

    def runs(self) -> list[str]:
        return [r[0] for r in self._query("SELECT DISTINCT run_id FROM agent_ledger ORDER BY run_id")]

    def verify(self, run_id: str | None = None) -> VerifyReport:
        rep = VerifyReport(ok=True)
        for rid in ([run_id] if run_id else self.runs()):
            rep.runs_checked += 1
            prev = GENESIS
            expected_seq = 1
            for e in self.entries(rid):
                rep.entries_checked += 1
                reason = None
                if e.seq != expected_seq:
                    reason = f"sequence gap: expected {expected_seq}, found {e.seq}"
                elif e.prev_hash != prev:
                    reason = "prev_hash does not match the previous entry"
                elif entry_hash(prev, rid, e.seq, e.node, e.payload) != e.hash:
                    reason = "hash mismatch (payload, node or seq altered)"
                if reason:
                    rep.ok = False
                    rep.first_bad = {"run_id": rid, "seq": e.seq, "reason": reason}
                    return rep
                prev = e.hash
                expected_seq += 1
            rep.heads[rid] = prev
        return rep

    def clear_cache(self) -> None:
        self._heads.clear()


class _Closing:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        self.c.close()


_default: Ledger | None = None
_dlock = threading.Lock()


def get_ledger() -> Ledger:
    """Default ledger: LEDGER_DSN / DATABASE_URL (Postgres) when LEDGER_BACKEND != sqlite, else data/ledger.sqlite."""
    global _default
    with _dlock:
        if _default is None:
            backend = os.environ.get("LEDGER_BACKEND", "").lower()
            dsn = os.environ.get("LEDGER_DSN") or os.environ.get("DATABASE_URL")
            if backend != "sqlite" and dsn and _pg_ok(dsn):
                _default = Ledger(dsn=dsn)
            else:
                d = Path(os.environ.get("ROUTER_DATA_DIR") or REPO_ROOT / "data")
                _default = Ledger(sqlite_path=d / "ledger.sqlite")
        return _default


def set_ledger(ledger: Ledger | None) -> None:
    global _default
    with _dlock:
        _default = ledger


def _pg_ok(dsn: str) -> bool:
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as c:
            c.execute("SELECT 1 FROM agent_ledger LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
