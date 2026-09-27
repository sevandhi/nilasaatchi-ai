"""Content hashing — sha256 (the document identity / dedup key) and md5 (kept for reference)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def hash_file(path: str | Path, chunk_size: int = 1 << 20) -> tuple[str, str, int]:
    """Return (sha256_hex, md5_hex, size_bytes). Streams the file so large PDFs don't blow up
    memory."""
    sha = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            sha.update(block)
            md5.update(block)
            size += len(block)
    return sha.hexdigest(), md5.hexdigest(), size
