"""Workspace export: every format is rebuilt from the saved WorkspaceSpec alone (reproducibility
rule — no export depends on anything not recoverable from `workspace.spec`)."""
from __future__ import annotations

import psycopg

from app.export.csv import export_csv
from app.export.geojson import build_geojson, export_geojson
from app.export.jsonexp import export_json
from app.export.pdf import PlaywrightNotAvailable, export_pdf, render_html
from app.workspace.models import WorkspaceRecord

__all__ = ["PlaywrightNotAvailable", "build_geojson", "export_workspace", "render_html"]

_MEDIA_TYPES = {
    "geojson": "application/geo+json",
    "csv": "text/csv",
    "json": "application/json",
    "pdf": "application/pdf",
}


def export_workspace(conn: psycopg.Connection, record: WorkspaceRecord, fmt: str) -> tuple[bytes, str, str]:
    """Returns (content, media_type, filename)."""
    if fmt == "geojson":
        content = export_geojson(conn, record.spec)
    elif fmt == "csv":
        content = export_csv(conn, record.spec)
    elif fmt == "json":
        content = export_json(record)
    elif fmt == "pdf":
        content = export_pdf(record.spec)
    else:
        raise ValueError(f"unknown export format {fmt!r}")
    filename = f"{record.id}_v{record.version}.{fmt}"
    return content, _MEDIA_TYPES[fmt], filename
