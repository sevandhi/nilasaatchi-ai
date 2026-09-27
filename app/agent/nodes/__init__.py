"""Graph nodes. Each node takes a RunState and returns the full updated field dict (domain-agnostic)."""
from __future__ import annotations

from app.agent.state import RunState


def out(st: RunState) -> dict:
    return {k: getattr(st, k) for k in RunState.model_fields}
