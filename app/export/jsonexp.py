"""JSON export: the full WorkspaceRecord (spec + versioning + provenance), verbatim."""
from __future__ import annotations

from app.workspace.models import WorkspaceRecord


def export_json(record: WorkspaceRecord) -> bytes:
    return record.model_dump_json(indent=2).encode("utf-8")
