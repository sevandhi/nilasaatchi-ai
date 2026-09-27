"""Content-hash cache for LLM classify calls (D-030). Reuses the bake-off's `ContentCache`
(spikes/ocr_bakeoff/cache.py, read-only import -- the same idempotent JSON-per-key pattern),
under its own directory so a full run is resumable: re-running after an interruption or a
`--workers` change just skips every already-cached input."""
from __future__ import annotations

from pathlib import Path

from spikes.ocr_bakeoff.cache import ContentCache, content_hash  # noqa: F401 - re-exported

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "classify_cache"

_cache: ContentCache | None = None


def get_cache() -> ContentCache:
    global _cache
    if _cache is None:
        _cache = ContentCache(CACHE_DIR)
    return _cache
