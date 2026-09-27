"""Hash-chain + tamper detection for app/api/ledger_stub.py, against a fake in-memory connection
(no Postgres needed) so this is a pure unit test. Mirrors the phase4-agentic-core exit criterion
"ledger tamper detected" for the stub ledger used until app.agent.graph writes to `agent_ledger`
(0012_agent_ledger.sql, owner agent-architect) itself."""
from __future__ import annotations

from app.api import ledger_stub


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Enough of psycopg's Connection surface for ledger_stub.append/verify_chain, modelling the
    real `agent_ledger` table (run_id, seq, node, event_type, ts, payload, prev_hash, hash)."""

    def __init__(self):
        self.rows: list[dict] = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT hash FROM agent_ledger"):
            (run_id,) = params
            matches = sorted((r for r in self.rows if r["run_id"] == run_id), key=lambda r: r["seq"])
            return _Cursor([matches[-1]] if matches else [])
        if s.startswith("INSERT INTO agent_ledger"):
            run_id, seq, node, event_type, ts, payload_json, prev_hash, h = params
            import json

            self.rows.append({"run_id": run_id, "seq": seq, "node": node, "event_type": event_type,
                              "ts": ts, "payload": json.loads(payload_json), "prev_hash": prev_hash,
                              "hash": h})
            return _Cursor([])
        if s.startswith("SELECT seq, node, payload, prev_hash, hash FROM agent_ledger"):
            (run_id,) = params
            rows = sorted((r for r in self.rows if r["run_id"] == run_id), key=lambda r: r["seq"])
            return _Cursor(rows)
        raise AssertionError(f"unexpected SQL in FakeConn: {s}")

    def commit(self):
        pass


def _append(conn: FakeConn, run_id: str, seq: int, node: str, payload: dict) -> str:
    return ledger_stub.append(conn, run_id, seq, node, node, payload, "2026-09-27T00:00:00Z")


def test_genesis_hash_is_stable():
    import hashlib

    assert ledger_stub.GENESIS == hashlib.sha256(b"nilasaatchi-genesis").hexdigest()


def test_chain_verifies_clean():
    conn = FakeConn()
    _append(conn, "run-1", 1, "plan", {"goal": "x"})
    _append(conn, "run-1", 2, "step_end", {"step_id": "s1"})
    _append(conn, "run-1", 3, "result", {"workspace_spec": {"name": "n"}})
    ok, bad_seq = ledger_stub.verify_chain(conn, "run-1")
    assert ok and bad_seq is None


def test_tampered_payload_is_detected_at_the_right_seq():
    conn = FakeConn()
    _append(conn, "run-1", 1, "plan", {"goal": "x"})
    _append(conn, "run-1", 2, "step_end", {"step_id": "s1"})
    _append(conn, "run-1", 3, "result", {"workspace_spec": {"name": "n"}})
    for r in conn.rows:
        if r["seq"] == 2:
            r["payload"] = {"step_id": "TAMPERED"}
    ok, bad_seq = ledger_stub.verify_chain(conn, "run-1")
    assert not ok
    assert bad_seq == 2


def test_two_runs_have_independent_chains():
    conn = FakeConn()
    _append(conn, "run-a", 1, "plan", {"goal": "a"})
    _append(conn, "run-b", 1, "plan", {"goal": "b"})
    assert ledger_stub.verify_chain(conn, "run-a") == (True, None)
    assert ledger_stub.verify_chain(conn, "run-b") == (True, None)
