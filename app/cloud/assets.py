"""Page images and satellite chips are never embedded in the JSON snapshot — the cloud app answers
those URLs with a 302 redirect to a presigned, time-limited S3 URL (`ASSET_BUCKET` stays private;
see `infra/cloud_demo/deploy.py`). Locally (no `ASSET_BUCKET` configured — the default dev/test
setup), the same file is served straight off `SNAPSHOT_URI` when present, so `curl`-ing the cloud
app locally still shows something useful.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.cloud.config import REPO_ROOT, get_cloud_settings
from app.cloud.snapshot import SnapshotStore
from app.cloud.util import safe_name


@lru_cache
def _s3_client():
    import boto3

    return boto3.client("s3", region_name=get_cloud_settings().aws_region)


def presigned_url(key: str) -> str:
    settings = get_cloud_settings()
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.asset_bucket, "Key": key},
        ExpiresIn=settings.presigned_url_ttl_seconds,
    )


class AssetResolver:
    """Looks up a page image / chip in `manifest/pages.json` / `manifest/chips.json` and returns
    either a presigned S3 URL (ASSET_BUCKET configured) or a local snapshot-relative path (dev)."""

    def __init__(self, store: SnapshotStore) -> None:
        self.store = store
        self._pages: dict[str, str] | None = None
        self._chips: dict[str, str] | None = None

    def _load_pages(self) -> dict[str, str]:
        if self._pages is None:
            self._pages = self.store.read_json("manifest/pages.json") or {}
        return self._pages

    def _load_chips(self) -> dict[str, str]:
        if self._chips is None:
            self._chips = self.store.read_json("manifest/chips.json") or {}
        return self._chips

    def page_target(self, document_id: int, page_no: int) -> tuple[str, str] | None:
        """Returns (kind, target) where kind is "redirect" (target = presigned URL) or "local"
        (target = local file path), or None if this page was never exported (not cached / API
        error at export time)."""
        rel = self._load_pages().get(f"{document_id}:{page_no}")
        if rel is None:
            return None
        settings = get_cloud_settings()
        if settings.asset_bucket:
            key = f"{settings.asset_prefix}/pages/{rel}" if settings.asset_prefix else f"pages/{rel}"
            return "redirect", presigned_url(key)
        # Dev/test fallback (no ASSET_BUCKET): the binary was deliberately never copied into the
        # snapshot (only its manifest entry) — serve it straight from the original data/pages/ tree
        # on this machine, if present.
        local = REPO_ROOT / "data" / "pages" / rel
        return ("local", str(local)) if local.is_file() else None

    def chip_target(self, parcel_uid: str, date: str, kind: str) -> tuple[str, str] | None:
        rel = self._load_chips().get(f"{safe_name(parcel_uid)}:{date}:{kind}")
        if rel is None:
            return None  # not cached locally at export time -> the caller returns 404
        settings = get_cloud_settings()
        if settings.asset_bucket:
            key = f"{settings.asset_prefix}/chips/{rel}" if settings.asset_prefix else f"chips/{rel}"
            return "redirect", presigned_url(key)
        local = REPO_ROOT / "data" / "s2" / "chips" / rel
        return ("local", str(local)) if local.is_file() else None
