"""Workspace CRUD + versioning + export.

`GET/POST/PUT /workspaces`, `GET /workspaces/{id}/versions/{v}`,
`POST /workspaces/{id}/export?fmt=geojson|csv|json|pdf`.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.api.db import get_conn
from app.export import export_workspace
from app.workspace.models import WorkspaceCreate, WorkspaceRecord, WorkspaceUpdate
from app.workspace.store import WorkspaceNotFound, WorkspaceStore

router = APIRouter(tags=["workspaces"])


async def _to_thread(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


@router.get("/workspaces", response_model=list[WorkspaceRecord])
async def list_workspaces(limit: int = Query(50, le=200), offset: int = 0) -> list[WorkspaceRecord]:
    def _run():
        with get_conn() as conn:
            return WorkspaceStore(conn).list(limit=limit, offset=offset)

    return await _to_thread(_run)


@router.post("/workspaces", response_model=WorkspaceRecord)
async def create_workspace(body: WorkspaceCreate) -> WorkspaceRecord:
    def _run():
        with get_conn() as conn:
            return WorkspaceStore(conn).create(body)

    return await _to_thread(_run)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRecord)
async def get_workspace(workspace_id: str) -> WorkspaceRecord:
    def _run():
        with get_conn() as conn:
            return WorkspaceStore(conn).get(workspace_id)

    try:
        return await _to_thread(_run)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail="workspace not found") from e


@router.put("/workspaces/{workspace_id}", response_model=WorkspaceRecord)
async def update_workspace(workspace_id: str, body: WorkspaceUpdate) -> WorkspaceRecord:
    def _run():
        with get_conn() as conn:
            return WorkspaceStore(conn).update(workspace_id, body)

    try:
        return await _to_thread(_run)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail="workspace not found") from e


@router.get("/workspaces/{workspace_id}/versions/{version}", response_model=WorkspaceRecord)
async def get_workspace_version(workspace_id: str, version: int) -> WorkspaceRecord:
    def _run():
        with get_conn() as conn:
            return WorkspaceStore(conn).get_version(workspace_id, version)

    try:
        return await _to_thread(_run)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail="workspace version not found") from e


@router.post("/workspaces/{workspace_id}/export")
async def export_workspace_route(
    workspace_id: str, fmt: Literal["geojson", "csv", "json", "pdf"] = Query(...),
    version: int | None = Query(None),
) -> Response:
    """Every export is rebuilt from the saved WorkspaceSpec alone (reproducibility rule)."""

    def _run():
        with get_conn() as conn:
            store = WorkspaceStore(conn)
            record = store.get_version(workspace_id, version) if version else store.get(workspace_id)
            return export_workspace(conn, record, fmt)

    try:
        content, media_type, filename = await _to_thread(_run)
    except WorkspaceNotFound as e:
        raise HTTPException(status_code=404, detail="workspace not found") from e
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
