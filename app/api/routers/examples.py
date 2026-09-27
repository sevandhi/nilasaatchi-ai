"""GET /examples — example query chips, pulled from eval/queries.yaml ids/text (never hard-coded
in code, per CLAUDE.md)."""
from __future__ import annotations

from functools import lru_cache

import yaml
from fastapi import APIRouter

from app.api.config import REPO_ROOT
from app.api.schemas import ExampleQuery, ExamplesResponse

router = APIRouter(tags=["examples"])

QUERIES_PATH = REPO_ROOT / "eval" / "queries.yaml"


@lru_cache
def _load() -> list[ExampleQuery]:
    if not QUERIES_PATH.exists():
        return []
    doc = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8")) or {}
    return [ExampleQuery(id=q["id"], text=q["text"], category=q.get("category"))
           for q in doc.get("queries", [])]


@router.get("/examples", response_model=ExamplesResponse)
async def list_examples() -> ExamplesResponse:
    return ExamplesResponse(items=_load())
