"""The read-only cloud FastAPI app (T7.1, D-067). Every route here mirrors one `app.api` route's
GET behaviour and JSON shape — see `app/cloud/ENDPOINTS.md` for the full table of how each is
served (verbatim snapshot replay / DuckDB-over-Parquet / presigned asset redirect) and the small
number of documented deviations.

Run locally: `SNAPSHOT_URI=data/cloud/snapshot uv run uvicorn app.cloud.app:app --port 8100`.
On Lambda: `app.cloud.lambda_handler.handler` wraps this app with Mangum.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from app.cloud import duckdb_store
from app.cloud.assets import AssetResolver
from app.cloud.bedrock_health import bedrock_health
from app.cloud.config import get_cloud_settings
from app.cloud.snapshot import get_manifest, get_store

API_VERSION = "0.1.0"

app = FastAPI(
    title="NilaSaatchi AI API — cloud read-only demo",
    description="Paper vs Planet, read-only cloud demo (D-067): serves the same GET paths and "
                "JSON shapes as the local API from a pre-built snapshot, no database. Every "
                "mutating verb returns 405 (D-067: uploads, the live agent, review actions and "
                "the satellite refresh stay in the local app).",
    version=API_VERSION,
)
app.add_middleware(GZipMiddleware, minimum_size=500)

_NOT_READONLY = JSONResponse(
    status_code=405, content={"detail": "read-only cloud demo: available in the full local app"}
)


# UI pages whose path is also an API list endpoint (same origin in the cloud): a browser navigation
# (Accept: text/html, e.g. a shared link or a refresh) gets the app; the UI's data fetches get JSON.
_SPA_API_OVERLAP = {"/findings", "/documents"}


@app.middleware("http")
async def _spa_for_browser_navigation(request: Request, call_next):
    if (request.method == "GET" and request.url.path in _SPA_API_OVERLAP
            and "text/html" in request.headers.get("accept", "")):
        index = get_cloud_settings().static_dir / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
    return await call_next(request)


@app.middleware("http")
async def _read_only_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return _NOT_READONLY
    return await call_next(request)


# --------------------------------------------------------------------------------- helpers

def _verbatim(rel: str, *, media_type: str = "application/json", not_found: str | None = None) -> Response:
    store = get_store()
    body = store.read_bytes(rel)
    if body is None:
        raise HTTPException(status_code=404, detail=not_found or f"not found in the cloud snapshot: {rel}")
    return Response(content=body, media_type=media_type)


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    raise TypeError(type(v))


def _json(obj) -> Response:
    return Response(content=json.dumps(obj, default=_json_default, ensure_ascii=False),
                    media_type="application/json")


# --------------------------------------------------------------------------------- health

@app.get("/health", tags=["meta"])
async def health() -> dict:
    manifest = get_manifest()
    return {"status": "ok", "version": API_VERSION, "api_mode": "cloud-readonly", "mode": "cloud-readonly",
            "snapshot_at": manifest.get("snapshot_at")}


@app.get("/health/bedrock", tags=["meta"])
async def health_bedrock() -> dict:
    return bedrock_health()


# --------------------------------------------------------------------------------- meta (verbatim snapshot replay)

_META_FILES = {
    "/stats/overview": "meta/stats_overview.json",
    "/router/models": "meta/router_models.json",
    "/router/usage": "meta/router_usage.json",
    "/router/chains": "meta/router_chains.json",
    "/examples": "meta/examples.json",
    "/classifier/summary": "meta/classifier_summary.json",
    "/eval/summary": "meta/eval_summary.json",
    "/decisions": "meta/decisions.json",
    "/progress": "meta/progress.json",
    "/findings/summary": "meta/findings_summary.json",
    "/idle-land": "meta/idle_land.json",
    "/ingest/doc-types": "meta/ingest_doc_types.json",
    "/ingest/satellite/status": "meta/ingest_satellite_status.json",
    "/findings/facets": "meta/findings_facets.json",
    "/documents/facets": "meta/documents_facets.json",
    "/review-queue/facets": "meta/review_facets.json",
}
for _path, _rel in _META_FILES.items():
    def _make(rel: str):
        async def _handler():
            return _verbatim(rel)
        return _handler
    app.add_api_route(_path, _make(_rel), methods=["GET"], tags=["meta"])


# --------------------------------------------------------------------------------- layers

@app.get("/layers/{name}.geojson", tags=["layers"])
async def get_layer(name: str, season: str | None = Query(None)) -> Response:
    if name == "parcel" and season:
        rel = f"layers/parcel__season-{season}.geojson"
        not_found = (f"season {season!r} was not part of the exported bounded key space "
                    "(scripts/cloud_export.py SEASONS, 2019-2026 x kharif/rabi/summer)")
    else:
        rel = f"layers/{name}.geojson"
        not_found = f"unknown layer {name!r}, or not exported (see app/cloud/ENDPOINTS.md)"
    return _verbatim(rel, media_type="application/geo+json", not_found=not_found)


# --------------------------------------------------------------------------------- parcels
# Sub-routes registered before the bare `/parcels/{parcel_uid:path}` for the same reason as
# app/api/routers/parcels.py: the `:path` converter is greedy.

@app.get("/parcels/{parcel_uid:path}/timeline", tags=["parcels"])
async def get_parcel_timeline(parcel_uid: str) -> Response:
    from app.cloud.util import safe_parcel_key
    return _verbatim(f"parcels/{safe_parcel_key(parcel_uid)}__timeline.json",
                     not_found="parcel not found")


@app.get("/parcels/{parcel_uid:path}/extractions", tags=["parcels"])
async def get_parcel_extractions(parcel_uid: str) -> Response:
    from app.cloud.util import safe_parcel_key
    return _verbatim(f"parcels/{safe_parcel_key(parcel_uid)}__extractions.json",
                     not_found="parcel not found")


@app.get("/parcels/{parcel_uid:path}/satellite", tags=["parcels"])
async def get_parcel_satellite(parcel_uid: str) -> Response:
    from app.cloud.util import safe_parcel_key
    return _verbatim(f"parcels/{safe_parcel_key(parcel_uid)}__satellite.json",
                     not_found="parcel not found")


@app.get("/parcels/{parcel_uid:path}/findings", tags=["parcels"])
async def get_parcel_findings(parcel_uid: str) -> Response:
    from app.cloud.util import safe_parcel_key
    return _verbatim(f"parcels/{safe_parcel_key(parcel_uid)}__findings.json",
                     not_found="parcel not found")


@app.get("/parcels/{parcel_uid:path}", tags=["parcels"])
async def get_parcel(parcel_uid: str) -> Response:
    from app.cloud.util import safe_parcel_key
    return _verbatim(f"parcels/{safe_parcel_key(parcel_uid)}__detail.json",
                     not_found="parcel not found")


# --------------------------------------------------------------------------------- findings

@app.get("/findings", tags=["findings"])
async def list_findings(category: str | None = None, severity: str | None = None, village: str | None = None,
                        block: int | None = Query(None), level: str | None = Query(None),
                        limit: int = Query(100, le=1000), offset: int = 0) -> Response:
    result = duckdb_store.list_findings(get_store(), category=category, severity=severity, village=village,
                                        block=block, level=level, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="findings table missing from the cloud snapshot")
    return _json(result)


@app.get("/findings/{finding_id}/plain-summary", tags=["findings"])
async def get_plain_summary(finding_id: int, lang: str = Query("en", pattern="^(en|ta)$")) -> Response:
    import asyncio

    from app.cloud.explain import explain
    out = await asyncio.to_thread(explain, get_store(), finding_id, lang)
    if out is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return _json(out)


@app.get("/findings/{finding_id}/evidence-pack", tags=["findings"])
async def get_evidence_pack(finding_id: int, render_chips: bool = False) -> Response:
    # `render_chips` is accepted for URL/query-shape parity but ignored: the cloud snapshot only
    # has the `render_chips=false` (default) response pre-rendered (see ENDPOINTS.md deviations).
    return _verbatim(f"findings/{finding_id}__evidence-pack.json", not_found="finding not found")


# --------------------------------------------------------------------------------- documents

@app.get("/documents", tags=["documents"])
async def list_documents(village: str | None = None, classified_type: str | None = None, stage: str | None = None,
                         scheme_relevance: str | None = None, q: str | None = None,
                         limit: int = Query(50, le=500), offset: int = 0) -> Response:
    result = duckdb_store.list_documents(get_store(), village=village, classified_type=classified_type,
                                         stage=stage, scheme_relevance=scheme_relevance, q=q,
                                         limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="documents table missing from the cloud snapshot")
    return _json(result)


@app.get("/documents/{document_id}/meta", tags=["documents"])
async def get_document_meta(document_id: int) -> Response:
    row = duckdb_store.document_meta(get_store(), document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _json(row)


@app.get("/review/{review_id}/rows", tags=["review"])
async def get_review_rows(review_id: int) -> Response:
    return _verbatim(f"review/{review_id}__rows.json", not_found="review item not found in the cloud snapshot")


@app.get("/documents/{document_id}/pages/{page_no}.webp", tags=["documents"])
async def get_page_preview(document_id: int, page_no: int) -> Response:
    resolver = AssetResolver(get_store())
    target = resolver.page_target(document_id, page_no)
    if target is None:
        raise HTTPException(status_code=404, detail="page preview not in the cloud snapshot (not cached at export time)")
    kind, value = target
    if kind == "redirect":
        return RedirectResponse(url=value, status_code=302)
    return FileResponse(value, media_type="image/webp")


@app.get("/evidence/{extraction_id}", tags=["documents"])
async def get_evidence(extraction_id: int, demo_mask: bool = True) -> Response:
    # demo_mask is accepted for parity but ignored: the cloud demo never serves unmasked owner
    # data, regardless of the query string (D-032/D-033; ALLOW_DEMO_UNMASK is a local-only knob).
    return _verbatim(f"evidence/{extraction_id}.json",
                     not_found="extraction not found, or not in the cloud snapshot's referenced set "
                                "(scripts/cloud_export.py _referenced_extraction_ids)")


# --------------------------------------------------------------------------------- chips

@app.get("/chips/{parcel_uid:path}/{date}.png", tags=["chips"])
async def get_chip(parcel_uid: str, date: str, kind: str = "truecolor") -> Response:
    resolver = AssetResolver(get_store())
    target = resolver.chip_target(parcel_uid, date, kind)
    if target is None:
        raise HTTPException(status_code=404,
                            detail=f"chip not cached in the cloud snapshot for {parcel_uid!r}/{date}/{kind} "
                                    "(only chips already cached under data/s2/chips at export time are available)")
    resolved_kind, value = target
    if resolved_kind == "redirect":
        return RedirectResponse(url=value, status_code=302)
    return FileResponse(value, media_type="image/png")


# --------------------------------------------------------------------------------- review queue

@app.get("/review-queue", tags=["review"])
async def review_queue(status: str = "open", reason: str | None = None,
                       limit: int = Query(100, le=1000), offset: int = 0) -> Response:
    result = duckdb_store.list_review_queue(get_store(), status=status, reason=reason, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="review_queue table missing from the cloud snapshot")
    return _json(result)


# --------------------------------------------------------------------------------- runs (read-only history)

@app.get("/runs", tags=["runs"])
async def list_runs(status: str | None = None, limit: int = 50, offset: int = 0) -> Response:
    result = duckdb_store.list_runs(get_store(), status=status, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="runs table missing from the cloud snapshot")
    return _json(result)


@app.get("/runs/{run_id}", tags=["runs"])
async def get_run(run_id: str) -> Response:
    return _verbatim(f"runs/{run_id}.json", not_found="run not found")


# --------------------------------------------------------------------------------- static SPA (web/dist)

_settings = get_cloud_settings()
if _settings.static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    assets_dir = _settings.static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir), html=False), name="assets")

    _index_html = _settings.static_dir / "index.html"

    @app.get("/{full_path:path}", tags=["spa"])
    async def spa_fallback(full_path: str) -> Response:
        # Any other static file the build emitted at the top level (favicon, manifest, ...).
        candidate = _settings.static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        if _index_html.is_file():
            return FileResponse(_index_html, media_type="text/html")
        raise HTTPException(status_code=404, detail="not found")
