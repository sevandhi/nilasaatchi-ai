"""Content-hash cache for OCR bake-off candidate outputs.

Every (page image bytes, candidate id, candidate params) tuple hashes to one JSON
file under ``data/bakeoff/cache/``. Re-running `run.py` is a no-op for anything
already cached, which is what makes every step idempotent and safe to re-run for a
`--limit N` smoke test without re-paying for the full corpus.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def content_hash(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8") if isinstance(p, str) else p)
        h.update(b"\x00")
    return h.hexdigest()[:24]


class ContentCache:
    """A tiny read-through JSON cache keyed by content hash, not by filename, so a
    changed candidate implementation or a re-rendered page invalidates itself."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self.path_for(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        p = self.path_for(key)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def get_or_compute(self, key: str, compute) -> dict[str, Any]:
        cached = self.get(key)
        if cached is not None:
            cached = dict(cached)
            cached["from_cache"] = True
            return cached
        value = compute()
        value = dict(value)
        value["from_cache"] = False
        self.set(key, value)
        return value
