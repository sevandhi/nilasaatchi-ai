"""Snapshot access: `SNAPSHOT_URI` is either a local directory (tests, `uvicorn app.cloud.app:app`
run locally) or `s3://bucket/prefix` (deployed Lambda). In S3 mode, files are downloaded lazily to
`/tmp` and cached there for the lifetime of the execution environment (Lambda warm-start reuse) —
this keeps cold starts fast (only `manifest.json` + whatever the first requests touch gets
downloaded) without ever needing DuckDB's S3/httpfs extension.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.cloud.config import get_cloud_settings


class SnapshotStore:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.is_s3 = uri.startswith("s3://")
        self._lock = threading.Lock()
        if self.is_s3:
            rest = uri[len("s3://") :]
            bucket, _, prefix = rest.partition("/")
            self.bucket = bucket
            self.prefix = prefix.rstrip("/")
            self._cache_dir = Path(tempfile.gettempdir()) / "nilasaatchi-cloud-snapshot"
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._s3_client = None
        else:
            self.root = Path(uri)

    # ---------------------------------------------------------------------------- resolution

    def _s3(self):
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3", region_name=get_cloud_settings().aws_region)
        return self._s3_client

    def local_path(self, rel: str) -> Path | None:
        """Returns a local filesystem path for snapshot-relative key `rel`, downloading it from S3
        first if needed. Returns None if the object/file does not exist."""
        rel = rel.lstrip("/")
        if not self.is_s3:
            p = self.root / rel
            return p if p.is_file() else None

        dest = self._cache_dir / rel
        if dest.exists():
            return dest
        with self._lock:
            if dest.exists():  # re-check inside the lock (another thread may have just fetched it)
                return dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            key = f"{self.prefix}/{rel}" if self.prefix else rel
            try:
                self._s3().download_file(self.bucket, key, str(dest) + ".part")
            except Exception:
                return None
            os.replace(str(dest) + ".part", dest)
            return dest

    # ---------------------------------------------------------------------------- readers

    def read_json(self, rel: str) -> Any | None:
        p = self.local_path(rel)
        if p is None:
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def read_bytes(self, rel: str) -> bytes | None:
        p = self.local_path(rel)
        if p is None:
            return None
        return p.read_bytes()

    def exists(self, rel: str) -> bool:
        return self.local_path(rel) is not None


@lru_cache
def get_store() -> SnapshotStore:
    return SnapshotStore(get_cloud_settings().snapshot_uri)


@lru_cache
def get_manifest() -> dict:
    """`manifest.json`: snapshot_at, counts, source. Never raises — an absent/corrupt manifest
    degrades to an empty dict so `/health` can still respond (with `snapshot_at: null`) rather than
    500ing the whole app."""
    try:
        return get_store().read_json("manifest.json") or {}
    except Exception:  # noqa: BLE001
        return {}
