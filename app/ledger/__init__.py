"""Hash-chained, append-only run ledger (plan §5.9).

    hash_n = sha256(prev_hash + run_id + str(seq) + node + canonical_json(payload))
    genesis prev_hash = sha256("nilasaatchi-genesis")

Backends: Postgres table ``agent_ledger`` (migration 0012) when a DSN is given / reachable, else a local
SQLite file (``data/ledger.sqlite``; tests). Payloads are scrubbed of private keys before hashing.
CLI: ``python -m app.ledger verify [--run RUN_ID]`` (``make verify-ledger``) exits 1 on tampering and
prints the first bad seq.
"""
from .chain import GENESIS, canonical_json, entry_hash, scrub
from .store import Ledger, LedgerEntry, VerifyReport, get_ledger, set_ledger

__all__ = ["GENESIS", "Ledger", "LedgerEntry", "VerifyReport", "canonical_json", "entry_hash", "get_ledger",
           "scrub", "set_ledger"]
