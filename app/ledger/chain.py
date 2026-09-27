"""Hash rule and canonical JSON for the ledger."""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = hashlib.sha256(b"nilasaatchi-genesis").hexdigest()
PRIVATE_KEYS = {"private", "pseudo", "reverse", "forward", "known_names"}


def scrub(payload: Any) -> Any:
    """Drop private keys recursively (RunState.private, pseudonym maps)."""
    if isinstance(payload, dict):
        return {k: scrub(v) for k, v in payload.items() if k not in PRIVATE_KEYS}
    if isinstance(payload, (list, tuple)):
        return [scrub(v) for v in payload]
    return payload


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def entry_hash(prev_hash: str, run_id: str, seq: int, node: str, payload: Any) -> str:
    body = prev_hash + run_id + str(int(seq)) + node + canonical_json(scrub(payload))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
