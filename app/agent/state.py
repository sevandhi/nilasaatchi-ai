"""Typed run state for the agent graph (contract: app/agent/CONTRACT.md).

Every graph node reads and writes a ``RunState``. ``RunState.private`` (the pseudonym reverse map and
other local-only material) is excluded from ``public_dict()``, events and ledger payloads.
JSON Schemas for every public model are exported to ``schemas/agent/`` by ``export_schemas()``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "1"
SPEC_VERSION = "1"
MAX_PLAN_STEPS = 12
MAX_REROUTES = 2
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "agent"

EvidenceKind = Literal["page_bbox", "sql", "layer_feature", "raster", "satellite_chip", "timeseries", "document"]
VerdictKind = Literal["ACCEPT", "DOWNGRADE", "REROUTE", "REVIEW"]
EventType = Literal["plan", "step_start", "step_end", "verify", "critic", "judge", "fallback", "clarify",
                    "result", "error", "ledger", "status"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Evidence(_M):
    kind: EvidenceKind
    ref: str
    excerpt: str | None = None
    bbox: list[float] | None = None
    page: int | None = None
    document_id: int | None = None


class Attachment(_M):
    id: str
    kind: Literal["pdf", "image", "geojson", "csv", "text"] | None = None
    name: str = ""
    path: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None


class Slots(BaseModel):
    """Deterministically extracted request slots. Domain packs may add keys under ``extra``."""
    model_config = ConfigDict(extra="forbid")
    villages: list[str] = Field(default_factory=list)
    parcel_uids: list[str] = Field(default_factory=list)
    surveys: list[str] = Field(default_factory=list)
    blocks: list[int] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    seasons: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    polygons_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MissingInfo(_M):
    slot: str
    blocking: bool
    question: str


class PlanStep(_M):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
    tool: str | None = None
    llm_task: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_output_schema: str = "ToolResult"
    verify: bool = True
    rationale: str = ""

    @model_validator(mode="after")
    def _one_kind(self) -> PlanStep:
        if bool(self.tool) == bool(self.llm_task):
            raise ValueError(f"step {self.id}: exactly one of tool / llm_task is required")
        return self


class ExecutionPlan(_M):
    goal: str
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    outputs: list[str] = Field(default_factory=list)


class Attempt(_M):
    n: int
    by: str                      # model id or tool name
    outcome: str                 # ok | error | schema_invalid | excluded | timeout | refuted ...
    error: str | None = None
    latency_ms: int = 0
    reason: str = ""


class StepRecord(_M):
    id: str
    tool: str | None = None
    llm_task: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    verify: bool = True
    status: Literal["pending", "running", "ok", "failed", "skipped"] = "pending"
    output_ref: str | None = None
    summary: str = ""
    n_rows: int | None = None
    confidence: float | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    model_id: str | None = None
    vendor: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    shadow_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None


class CheckResult(_M):
    name: str
    passed: bool
    detail: str = ""


class Producer(_M):
    kind: Literal["tool", "model"]
    id: str
    vendor: str | None = None


class Claim(_M):
    id: str
    step_id: str
    kind: Literal["kpi", "table_cell", "discrepancy", "finding", "entity", "narrative"]
    subject: str
    field: str
    value: Any = None
    unit: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    producer: Producer
    checks: list[CheckResult] = Field(default_factory=list)
    status: Literal["pending", "verified", "downgraded", "review"] = "pending"
    verdict: VerdictKind | None = None
    confidence: float = 0.5
    context: dict[str, Any] = Field(default_factory=dict)   # inputs the deterministic checks need


class CriticCheck(_M):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class CriticChallenge(_M):
    claim_id: str
    hypothesis: str
    check: CriticCheck
    result: dict[str, Any] = Field(default_factory=dict)    # {refuted: bool, detail}
    model_id: str | None = None
    vendor: str | None = None
    producer_vendor: str | None = None


class Verdict(_M):
    claim_id: str
    verdict: VerdictKind
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class RouteTraceEntry(_M):
    step_id: str
    task: str
    choice: str | None = None
    reason: str = ""
    candidates: list[str] = Field(default_factory=list)
    filtered: list[dict[str, Any]] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int = 0
    shadow_cost_usd: float = 0.0
    # v1.1 (additive): full "why this model" record, also streamed on step_end / critic / judge events
    scores: dict[str, float] = Field(default_factory=dict)       # model -> total score (deterministic scorer)
    privacy_tier: str | None = None
    effective_tier: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    actual_cost_usd: float = 0.0


class Telemetry(_M):
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    shadow_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0


class Narrative(_M):
    en: str = ""
    ta: str | None = None
    claim_refs: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------ workspace spec
class Kpi(_M):
    id: str
    label: str
    value: Any
    unit: str | None = None
    claim_id: str
    status: Literal["verified", "downgraded", "review"]
    confidence: float = Field(ge=0, le=1)


class LegendItem(_M):
    value: Any
    label: str
    color: str


class MapLayer(_M):
    id: str
    title: str
    kind: Literal["parcels", "polygons", "points", "reference"]
    geojson: dict[str, Any] | None = None
    ref_layer: str | None = None
    color_by: str | None = None
    legend: list[LegendItem] = Field(default_factory=list)


class TimeSlider(_M):
    field: str = "season"
    values: list[str] = Field(default_factory=list)


class MapSpec(_M):
    bbox: list[float] | None = None
    layers: list[MapLayer] = Field(default_factory=list)
    time_slider: TimeSlider | None = None


class Column(_M):
    key: str
    label: str
    type: Literal["string", "number", "date", "bool"] = "string"


class TableSpec(_M):
    id: str
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]] = Field(max_length=5000)
    claim_ids: list[str] = Field(default_factory=list)
    sql: str | None = None


class ChartSpec(_M):
    id: str
    title: str
    type: Literal["bar", "stacked_bar", "line", "pie"]
    x: str
    y: list[str]
    data: list[dict[str, Any]]
    claim_ids: list[str] = Field(default_factory=list)


class TimelineEvent(_M):
    date: str
    stage: str
    label: str = ""
    evidence_ref: str | None = None


class TimelineSpec(_M):
    subject: str
    events: list[TimelineEvent] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceGroup(_M):
    claim_id: str
    items: list[Evidence]


class VerificationClaim(_M):
    claim_id: str
    verdict: VerdictKind | None
    confidence: float
    checks: list[CheckResult] = Field(default_factory=list)
    critic: dict[str, Any] | None = None


class VerificationSummary(_M):
    accepted: int = 0
    downgraded: int = 0
    review: int = 0
    rerouted: int = 0


class VerificationSpec(_M):
    summary: VerificationSummary = Field(default_factory=VerificationSummary)
    claims: list[VerificationClaim] = Field(default_factory=list)


class SqlEntry(_M):
    step_id: str
    sql: str


class WorkspaceSpec(_M):
    spec_version: Literal["1"] = SPEC_VERSION
    workspace_id: str | None = None
    version: int | None = None
    title: str
    domain: str
    run_id: str
    created_at: str
    ledger_head: str | None = None
    lang: Literal["en", "ta"] = "en"
    kpis: list[Kpi] = Field(default_factory=list)
    map: MapSpec = Field(default_factory=MapSpec)
    tables: list[TableSpec] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    timeline: TimelineSpec | None = None
    narrative: Narrative = Field(default_factory=Narrative)
    evidence: list[EvidenceGroup] = Field(default_factory=list)
    verification: VerificationSpec = Field(default_factory=VerificationSpec)
    sql: list[SqlEntry] = Field(default_factory=list)
    route_trace: list[RouteTraceEntry] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------------------------ run state
class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)

    run_id: str
    workspace_id: str | None = None
    contract_version: str = CONTRACT_VERSION
    request: str
    lang: Literal["en", "ta"] = "en"
    domain: str = "land_acquisition"
    attachments: list[Attachment] = Field(default_factory=list)
    status: Literal["queued", "running", "clarify", "done", "failed"] = "queued"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    slots: Slots = Field(default_factory=Slots)
    missing_info: list[MissingInfo] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    plan: ExecutionPlan | None = None
    steps: dict[str, StepRecord] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    critic: list[CriticChallenge] = Field(default_factory=list)
    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    reroutes: int = 0
    workspace: WorkspaceSpec | None = None
    narrative: Narrative = Field(default_factory=Narrative)
    route_trace: list[RouteTraceEntry] = Field(default_factory=list)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    ledger_head: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    # --- graph control (public, small) ---
    base_workspace: dict[str, Any] | None = None
    clarify_answer: str | None = None
    next_node: str | None = None
    plan_attempts: int = 0
    plan_meta: dict[str, Any] = Field(default_factory=dict)
    domain_explicit: bool = False
    reroute_steps: list[str] = Field(default_factory=list)
    replans: int = 0
    domain_scores: dict[str, float] = Field(default_factory=dict)
    # --- never serialised to the wire / ledger ---
    private: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, v: Any) -> Any:
        return v or {}

    def public_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude={"private", "base_workspace", "next_node", "reroute_steps",
                                                  "domain_explicit"})
        return d

    def touch(self) -> None:
        self.updated_at = now_iso()


class AgentEvent(_M):
    type: EventType
    run_id: str
    seq: int
    ts: str
    node: str
    data: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------------------------ LLM output schemas
LLM_TASKS = ("summarise",)


class PlannerStep(BaseModel):
    """One step as the planner model writes it: a single required ``tool`` field whose value is a catalog tool
    or an llm task (the per-pack JSON schema restricts it with an enum, see ``planner_schema``). Converted to a
    ``PlanStep`` (tool | llm_task) by ``PlannerOutput.to_plan``. Unknown extra keys from the model are ignored."""
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    verify: bool = True
    rationale: str = ""


class PlannerOutput(BaseModel):
    """What the planner model must return (JSON-schema validated by the router, repaired once, then fallback)."""
    model_config = ConfigDict(extra="ignore")
    goal: str
    steps: list[PlannerStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    outputs: list[str] = Field(default_factory=list)

    def to_plan(self) -> ExecutionPlan:
        return ExecutionPlan(goal=self.goal, outputs=self.outputs, steps=[
            PlanStep(id=s.id, tool=None if s.tool in LLM_TASKS else s.tool,
                     llm_task=s.tool if s.tool in LLM_TASKS else None, args=s.args, depends_on=s.depends_on,
                     verify=s.verify, rationale=s.rationale[:300]) for s in self.steps])


def planner_schema(tool_names: list[str]) -> dict:
    """Per-pack JSON schema for the planner: `tool` is an enum of the pack's planner-visible tools + llm tasks, so
    constrained decoding (or the router's validation + one repair) rejects unknown / missing tools up front."""
    step = {"type": "object", "required": ["id", "tool", "args"], "properties": {
        "id": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]{0,15}$"},
        "tool": {"type": "string", "enum": sorted(tool_names) + list(LLM_TASKS)},
        "args": {"type": "object"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "verify": {"type": "boolean"},
        "rationale": {"type": "string"}}}
    return {"type": "object", "required": ["goal", "steps"], "properties": {
        "goal": {"type": "string"},
        "steps": {"type": "array", "minItems": 1, "maxItems": MAX_PLAN_STEPS, "items": step},
        "outputs": {"type": "array", "items": {"type": "string"}}}}


class CriticItem(_M):
    claim_id: str
    hypothesis: str
    check: CriticCheck


class CriticOutput(_M):
    challenges: list[CriticItem] = Field(default_factory=list, max_length=6)


class JudgeItem(_M):
    claim_id: str
    verdict: VerdictKind
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class JudgeOutput(_M):
    verdicts: list[JudgeItem]


class PresentOutput(_M):
    en: str
    ta: str | None = None
    claim_refs: list[str] = Field(default_factory=list)


class SqlOutput(_M):
    sql: str
    explanation: str = ""


PUBLIC_MODELS: dict[str, type[BaseModel]] = {
    "RunState": RunState, "WorkspaceSpec": WorkspaceSpec, "ExecutionPlan": ExecutionPlan,
    "StepRecord": StepRecord, "Claim": Claim, "Evidence": Evidence, "AgentEvent": AgentEvent,
    "Attachment": Attachment, "CriticChallenge": CriticChallenge, "Verdict": Verdict,
    "PlannerOutput": PlannerOutput, "CriticOutput": CriticOutput, "JudgeOutput": JudgeOutput,
    "PresentOutput": PresentOutput, "SqlOutput": SqlOutput,
}


def _strip_titles(s: Any) -> Any:
    if isinstance(s, dict):
        return {k: _strip_titles(v) for k, v in s.items() if k != "title" or not isinstance(v, str)}
    if isinstance(s, list):
        return [_strip_titles(x) for x in s]
    return s


def json_schemas() -> dict[str, dict]:
    return {name: m.model_json_schema() for name, m in PUBLIC_MODELS.items()}


def llm_schema(model: type[BaseModel]) -> dict:
    """Compact JSON schema for a model output (titles stripped to save prompt tokens)."""
    return _strip_titles(model.model_json_schema())


def export_schemas(out_dir: Path = SCHEMA_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in json_schemas().items():
        p = out_dir / f"{name}.json"
        p.write_text(json.dumps(schema, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in export_schemas():
        print(p)
