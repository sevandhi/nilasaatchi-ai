"""Hash-chain helper for app/api/runner.py's stub run_request only.

Writes into `agent_ledger` (db/migrations/0012_agent_ledger.sql, owner agent-architect, D-039) using
the exact same rule the real graph will use, so the stub produces a genuinely tamper-evident chain
and needs no migration when app.agent.graph starts writing to the same table itself:

    hash_n = SHA256(prev_hash || run_id || seq || node || canonical_json(payload))
    genesis = SHA256("nilasaatchi-genesis")

`payload` is the AgentEvent's `data` (type-specific fields) — `run_id`/`seq`/`node` are already part
of the hash formula, so hashing them again inside `payload` would be redundant.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

GENESIS = hashlib.sha256(b"nilasaatchi-genesis").hexdigest()


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash(prev_hash: str, run_id: str, seq: int, node: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{prev_hash}{run_id}{seq}{node}".encode() + canonical_json(payload)).hexdigest()


def append(conn: psycopg.Connection, run_id: str, seq: int, node: str, event_type: str,
          payload: dict[str, Any], ts: str) -> str:
    """Append one ledger entry for (run_id, seq); returns the new head hash."""
    row = conn.execute(
        "SELECT hash FROM agent_ledger WHERE run_id = %s ORDER BY seq DESC LIMIT 1", (run_id,)
    ).fetchone()
    prev_hash = row["hash"] if row else GENESIS
    h = _hash(prev_hash, run_id, seq, node, payload)
    conn.execute(
        "INSERT INTO agent_ledger (run_id, seq, node, event_type, ts, payload, prev_hash, hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id, seq) DO NOTHING",
        (run_id, seq, node, event_type, ts, json.dumps(payload, default=str), prev_hash, h),
    )
    conn.commit()
    return h


def verify_chain(conn: psycopg.Connection, run_id: str) -> tuple[bool, int | None]:
    """Recompute the chain for `run_id` from the stored payloads; returns (ok, first_bad_seq)."""
    rows = conn.execute(
        "SELECT seq, node, payload, prev_hash, hash FROM agent_ledger WHERE run_id = %s ORDER BY seq",
        (run_id,),
    ).fetchall()
    prev_hash = GENESIS
    for r in rows:
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        h = _hash(prev_hash, run_id, r["seq"], r["node"], payload)
        if r["prev_hash"] != prev_hash or h != r["hash"]:
            return False, r["seq"]
        prev_hash = h
    return True, None
