"""FastAPI app: NilaSaatchi AI API (plan.md §5.11, phase4-agentic-core skill).

Run locally with `make api` (`uvicorn app.api.main:app --reload`). The OpenAPI document at
`/openapi.json` is the source of truth `scripts/gen_schemas.py` converts into
`web/src/api/schemas.js` (zod, D-011) via `make gen-schemas`.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config import get_settings
from app.api.routers import (
    chips,
    documents,
    examples,
    findings,
    ingest,
    layers,
    meta,
    parcels,
    review,
    router_meta,
    runs,
    workspaces,
)

API_VERSION = "0.1.0"

app = FastAPI(
    title="NilaSaatchi AI API",
    description="Paper vs Planet — agentic cross-examination of land-acquisition documents "
                "against satellite evidence. See plan.md and the phase4-agentic-core skill.",
    version=API_VERSION,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(workspaces.router)
app.include_router(documents.router)
app.include_router(layers.router)
app.include_router(chips.router)
app.include_router(review.router)
app.include_router(examples.router)
app.include_router(parcels.router)
app.include_router(findings.router)
app.include_router(meta.router)
app.include_router(router_meta.router)
app.include_router(ingest.router)


@app.on_event("startup")
async def _resume_stale_ingest_jobs() -> None:
    """A `queued`/`running` ingest job left over from a killed API process can never finish (no
    background thread is driving it any more) — mark it failed honestly instead of leaving it
    stuck forever (app/ingest/orchestrator.py)."""
    import asyncio

    from app.ingest.orchestrator import resume_stale_jobs

    n = await asyncio.to_thread(resume_stale_jobs)
    if n:
        print(f"ingest: marked {n} stale job(s) as failed (interrupted) on startup")


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": API_VERSION, "api_mode": settings.api_mode}
