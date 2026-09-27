"""GET /chips/{parcel_uid}/{date}.png — true-colour/NDVI satellite chip (evidence + VLM input),
via app.tools.satellite.satellite_chip (owner: eo-engineer)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["chips"])


@router.get("/chips/{parcel_uid}/{date}.png")
async def get_chip(parcel_uid: str, date: str, kind: Literal["truecolor", "ndvi"] = "truecolor") -> FileResponse:
    def _run():
        from app.tools.satellite import satellite_chip

        return satellite_chip(parcel_uid, date)

    try:
        out = await asyncio.to_thread(_run)
    except LookupError as e:
        # planet.chips.render raises KeyError (unknown parcel_uid) or LookupError (no usable scene)
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"satellite chip unavailable: {e}") from e
    path = Path(out[kind])
    if not path.exists():
        raise HTTPException(status_code=404, detail="chip file missing on disk")
    return FileResponse(path, media_type="image/png")
