"""Env-driven settings for the cloud app (mirrors app/api/config.py's style, kept import-light and
free of pydantic-settings so it stays cheap in a Lambda cold start)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_DIR = REPO_ROOT / "data" / "cloud" / "snapshot"


class CloudSettings:
    def __init__(self) -> None:
        # Local directory (absolute or relative to cwd) or "s3://bucket/prefix".
        self.snapshot_uri: str = os.environ.get("SNAPSHOT_URI", str(DEFAULT_SNAPSHOT_DIR))
        self.asset_bucket: str | None = os.environ.get("ASSET_BUCKET") or None
        self.asset_prefix: str = os.environ.get("ASSET_PREFIX", "cloud-demo").strip("/")
        self.aws_region: str = os.environ.get("AWS_REGION", "ap-south-1")
        self.bedrock_model_id: str = os.environ.get(
            "CLOUD_BEDROCK_MODEL_ID", "mistral.ministral-3-3b-instruct"
        )
        self.bedrock_health_cache_seconds: int = int(os.environ.get("BEDROCK_HEALTH_CACHE_SECONDS", "600"))
        self.presigned_url_ttl_seconds: int = int(os.environ.get("PRESIGNED_URL_TTL_SECONDS", "600"))
        origins = os.environ.get("CORS_ORIGINS", "*")
        self.cors_origins: list[str] = [o.strip() for o in origins.split(",") if o.strip()]
        # Package-relative static/ dir (web/dist copied in by scripts/cloud_package.sh); absent in
        # dev unless the frontend build has been copied there.
        self.static_dir: Path = Path(os.environ.get("CLOUD_STATIC_DIR", str(Path(__file__).resolve().parent / "static")))


@lru_cache
def get_cloud_settings() -> CloudSettings:
    return CloudSettings()
