"""Tool registry: typed contracts for every agent tool.

A ``Tool`` = (name, description, input_model, output = ToolResult, privacy_tier, fn). ``registry.run`` validates
args against the input model, runs the tool (sync fns in a worker thread) under a timeout and always returns a
``ToolResult`` or raises ``ToolError`` (never a bare exception) so the executor can record the attempt.

Every tool returns ``{data, evidence[], sql?, code?, confidence, caveats[], summary, claims[]}``. ``claims`` are
claim seeds (numbers/entities destined for KPIs, tables, findings or narrative) that the verify node turns into
``Claim``s; ``claims[].checks`` names the deterministic checks (app/agent/checks.py) to run on each.
Domain packs register their own tools; the graph never special-cases a tool name.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.state import Evidence


class ClaimSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["kpi", "table_cell", "discrepancy", "finding", "entity", "narrative"] = "kpi"
    subject: str
    field: str
    value: Any = None
    unit: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)        # names in app/agent/checks.py CHECKS
    context: dict[str, Any] = Field(default_factory=dict)  # inputs for the checks (sql, rows, geometry refs ...)
    confidence: float = 0.8


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    sql: str | None = None
    code: str | None = None
    confidence: float = 0.8
    caveats: list[str] = Field(default_factory=list)
    summary: str = ""
    claims: list[ClaimSeed] = Field(default_factory=list)
    model_id: str | None = None          # when the tool used an LLM internally (e.g. sql_query)
    vendor: str | None = None
    route_logs: list[dict] = Field(default_factory=list)
    n_rows: int | None = None
    degraded: bool = False


class ToolError(Exception):
    def __init__(self, kind: str, message: str, *, retryable: bool = False, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind            # bad_args | timeout | sql_error | not_found | unavailable | failed | low_confidence
        self.retryable = retryable
        self.detail = detail or {}


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[..., Any]
    privacy_tier: Literal["PUBLIC", "PSEUDO", "PII"] = "PUBLIC"    # tier of the tool's OUTPUT
    timeout_s: float = 30.0
    planner_visible: bool = True
    uses_llm: bool = False
    tags: list[str] = field(default_factory=list)

    def catalog_entry(self) -> dict:
        schema = self.input_model.model_json_schema()
        props = {k: {kk: vv for kk, vv in v.items() if kk in ("type", "enum", "description", "default", "items",
                                                              "anyOf", "maximum", "minimum")}
                 for k, v in (schema.get("properties") or {}).items()}
        return {"name": self.name, "description": self.description, "args": props,
                "required": schema.get("required", []), "output": "ToolResult{data, evidence[], sql?, confidence}"}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError("not_found", f"unknown tool {name!r}")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def catalog(self) -> list[dict]:
        return [self._tools[n].catalog_entry() for n in self.names() if self._tools[n].planner_visible]

    def validate_args(self, name: str, args: dict) -> BaseModel:
        tool = self.get(name)
        try:
            return tool.input_model.model_validate(args or {})
        except ValidationError as e:
            raise ToolError("bad_args", f"{name}: {e.errors(include_url=False)[:3]}") from None

    async def run(self, name: str, args: dict, ctx: Any) -> ToolResult:
        tool = self.get(name)
        parsed = self.validate_args(name, args)
        t0 = time.monotonic()
        try:
            if inspect.iscoroutinefunction(tool.fn):
                res = await asyncio.wait_for(tool.fn(parsed, ctx), timeout=tool.timeout_s)
            else:
                res = await asyncio.wait_for(asyncio.to_thread(tool.fn, parsed, ctx), timeout=tool.timeout_s)
        except TimeoutError:
            raise ToolError("timeout", f"{name} exceeded {tool.timeout_s:.0f} s", retryable=True) from None
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001 - normalise every tool failure
            raise ToolError("failed", f"{name}: {type(e).__name__}: {str(e)[:300]}") from None
        if isinstance(res, dict):
            res = ToolResult.model_validate(res)
        if not isinstance(res, ToolResult):
            raise ToolError("failed", f"{name} returned {type(res).__name__}, expected ToolResult")
        res.caveats = list(dict.fromkeys(res.caveats))
        _ = time.monotonic() - t0
        return res
