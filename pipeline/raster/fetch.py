"""Anonymous HTTPS downloads for the team-sourced rasters (T1.5), cached under
data/raster/_cache/ by filename (idempotent: a fully-downloaded file is never re-fetched)."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "raster" / "_cache"


class FetchError(RuntimeError):
    """Raised when a source needs auth/a key, or is otherwise unreachable, so the caller can
    record it and continue with the other rasters (T1.5: "if a source needs a login or key:
    stop, record it, escalate, and continue with the others")."""


def fetch(url: str, dest_name: str | None = None, timeout: float = 120.0) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / (dest_name or url.rsplit("/", 1)[-1])
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            if r.status_code in (401, 403):
                raise FetchError(f"{url} needs authentication (HTTP {r.status_code})")
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                f.writelines(r.iter_bytes(1 << 20))
            tmp.rename(dest)
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"{url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"{url}: network error: {exc}") from exc
    return dest
