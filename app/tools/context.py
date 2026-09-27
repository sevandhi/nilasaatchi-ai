"""Per-step tool context: DB access, LLM access through the router (with exclusions), pseudonymiser, events."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any

from app.agent.state import Attachment, Slots


def today() -> _date:
    """The analysis date for day-count rules (days in stage, idle >= 6 months). AGENT_TODAY=YYYY-MM-DD pins it so
    recorded eval runs replay byte-identically; default = the local date."""
    v = os.environ.get("AGENT_TODAY")
    return _date.fromisoformat(v) if v else _date.today()


def kg_dsn() -> str:
    """The knowledge graph the tools read (AGENT_KG_URL for the fixture KG; defaults to DATABASE_URL)."""
    d = os.environ.get("AGENT_KG_URL") or os.environ.get("DATABASE_URL")
    if not d:
        from dotenv import load_dotenv

        from app.agent.state import REPO_ROOT
        load_dotenv(REPO_ROOT / ".env", override=False)
        d = os.environ.get("AGENT_KG_URL") or os.environ.get("DATABASE_URL")
    if not d:
        raise RuntimeError("AGENT_KG_URL / DATABASE_URL not set")
    return d


@dataclass
class ToolContext:
    run_id: str
    step_id: str
    domain: str = "land_acquisition"
    slots: Slots = field(default_factory=Slots)
    attachments: list[Attachment] = field(default_factory=list)
    excluded_models: list[str] = field(default_factory=list)
    pseudo: Any = None                                   # app.router.gateway.Pseudonymiser
    emit: Callable[[str, dict], None] | None = None      # emit(event_type, data)
    route_logs: list[dict] = field(default_factory=list)
    dsn: str | None = None

    @property
    def kg(self) -> str:
        return self.dsn or kg_dsn()

    def llm(self, task: str, payload: dict, *, schema: dict | None = None, privacy_tier: str = "PUBLIC",
            producer_vendor: str | None = None):
        """Synchronous router call (tools run in worker threads). Logs the route; emits fallbacks."""
        from app.router import call
        payload = {**payload, "step_id": self.step_id}
        excl = sorted(set(self.excluded_models or []) | set(agent_excluded_models()))
        res = call(task, payload, schema=schema, privacy_tier=privacy_tier, producer_vendor=producer_vendor,
                   exclude_models=excl or None)
        self.route_logs.append(res.route_log)
        _emit_fallbacks(self, task, res.route_log)
        return res

    async def allm(self, *a, **kw):
        return await asyncio.to_thread(self.llm, *a, **kw)


def agent_excluded_models() -> list[str]:
    """Models the agent never uses (paid/AWS by default: the agent runs on free models only).
    AGENT_EXCLUDE_MODELS="" re-enables them; comma-separated model ids."""
    import os
    v = os.environ.get("AGENT_EXCLUDE_MODELS", "bedrock-ministral-8b,bedrock-ministral-3b")
    return [m.strip() for m in v.split(",") if m.strip()]


def _emit_fallbacks(ctx: ToolContext, task: str, log: dict) -> None:
    if not ctx.emit:
        return
    prev = None
    for a in log.get("attempts", []):
        if prev and a["model_id"] != prev["model_id"] and prev["outcome"] not in ("ok", "repair_ok"):
            ctx.emit("fallback", {"step_id": ctx.step_id, "task": task, "from": prev["model_id"],
                                  "to": a["model_id"], "reason": prev.get("error_kind") or prev["outcome"]})
        prev = a
