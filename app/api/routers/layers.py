"""GET /layers/{name}.geojson?bbox=&zoom= — simplified geometries for parcel, fmb_qa and the
ref_layer_* tables (plan.md §5.10)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.db import get_conn
from app.api.layers import LAYER_NAMES, layer_geojson

router = APIRouter(tags=["layers"])


@router.get("/layers/{name}.geojson")
async def get_layer(
    name: str,
    bbox: str | None = Query(None, description="minx,miny,maxx,maxy in EPSG:4326"),
    zoom: int | None = Query(None, ge=0, le=20),
    limit: int = Query(5000, le=20000),
    season: str | None = Query(None, description="'parcel' layer only, e.g. '2024-rabi'"),
) -> JSONResponse:
    if name not in LAYER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown layer {name!r}; known: {LAYER_NAMES}")
    bbox_t = None
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            bbox_t = (parts[0], parts[1], parts[2], parts[3])
        except ValueError as e:
            raise HTTPException(status_code=422, detail="bbox must be 'minx,miny,maxx,maxy'") from e

    def _run():
        with get_conn() as conn:
            return layer_geojson(conn, name, bbox=bbox_t, zoom=zoom, limit=limit, season=season)

    fc = await asyncio.to_thread(_run)
    return JSONResponse(content=fc, media_type="application/geo+json")
