"""WorkspaceSpec: the JSON spec behind every saved workspace.

Per app/agent/CONTRACT.md §5 ("the backend stores result.data.workspace and may pass it back as
base_workspace for modify-requests"), a saved workspace's `spec` *is* the agent's `WorkspaceSpec`
(kpis, map, tables, charts, timeline, narrative, evidence, verification, sql, route_trace, caveats
— app/agent/state.py). We import that Pydantic model directly rather than redefining it, so the two
never drift; `app.agent.state` is owned by agent-architect (do not edit), but importing it is the
intended integration point.

A thin, versioned fallback (`_FallbackWorkspaceSpec`) is used only if `app.agent.state` cannot be
imported (e.g. a stripped-down environment) — its `layers`/`filters`/`widgets` are the legacy P4
shape described in the backend-engineer brief, kept for backward compatibility with anything saved
before the contract landed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

try:
    from app.agent.state import SPEC_VERSION, WorkspaceSpec  # canonical shape (contract §4)
except ImportError:  # pragma: no cover - only if app.agent.state is missing/broken
    SPEC_VERSION = "1"

    class WorkspaceSpec(BaseModel):  # type: ignore[no-redef]
        """Fallback shape (legacy P4 UI-workspace-state model), used only if app.agent.state is
        unavailable. Prefer app.agent.state.WorkspaceSpec (contract §4) in every other case."""

        spec_version: str = SPEC_VERSION
        name: str = "Untitled workspace"
        layers: list[dict[str, Any]] = Field(default_factory=list)
        filters: list[dict[str, Any]] = Field(default_factory=list)
        widgets: list[dict[str, Any]] = Field(default_factory=list)
        query_history: list[dict[str, Any]] = Field(default_factory=list)
        ledger_head: str | None = None


class WorkspaceRecord(BaseModel):
    """A saved workspace: the envelope FastAPI returns (spec + versioning + provenance)."""

    id: str
    name: str
    version: int
    spec: WorkspaceSpec
    ledger_head: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceCreate(BaseModel):
    name: str = "Untitled workspace"
    spec: WorkspaceSpec
    created_by: str | None = None


class WorkspaceUpdate(BaseModel):
    spec: WorkspaceSpec
    name: str | None = None
