"""Env-driven settings for the API (12-factor; see .env.example)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    """Plain env reader (not pydantic-settings, to keep import light for scripts/gen_schemas.py)."""

    def __init__(self) -> None:
        self.database_url: str = os.environ.get(
            "DATABASE_URL", "postgresql://nila:nila_local_dev@localhost:5439/nilasaatchi"
        )
        # Vite dev server default port; comma-separated list of extra allowed origins.
        origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        self.cors_origins: list[str] = [o.strip() for o in origins.split(",") if o.strip()]
        self.data_dir: Path = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data"))
        # D-032/033: owner names are masked by default everywhere. A caller may pass
        # ?demo_mask=false to request unmasked owner names, but the server only honours that
        # when this flag is also true locally (never set in a deployed/demo environment).
        self.allow_demo_unmask: bool = os.environ.get("ALLOW_DEMO_UNMASK", "false").lower() == "true"
        self.api_mode: str = os.environ.get("API_MODE", "live")  # live | replay
        self.workspace_backend: str = os.environ.get("WORKSPACE_BACKEND", "postgres")  # postgres | dynamodb
        self.aws_region: str = os.environ.get("AWS_REGION", "ap-south-1")
        self.aws_team: str = os.environ.get("AWS_TEAM", "team49")


@lru_cache
def get_settings() -> Settings:
    return Settings()
