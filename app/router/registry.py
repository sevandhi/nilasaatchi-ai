"""Load and validate config/models.yaml into typed specs.

`trains_on_free_tier` is resolved here, fail-closed: only a literal `false` (or
`false_if_opted_out` with the opt-out env confirmed) yields False; anything else is True.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .types import RouterConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = REPO_ROOT / "config" / "models.yaml"


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    provider: str
    model: str
    vendor: str
    fallback_model: str | None = None
    weights: str = "proprietary"
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    json_mode: str = "prompt"
    trains_on_free_tier_raw: Any = Field(default="unknown", alias="trains_on_free_tier")
    optout_env: str | None = None
    cost_class: str
    context_tokens: int = 32768
    limits: dict[str, float] = Field(default_factory=dict)
    list_price_per_mtok: dict[str, Any] = Field(default_factory=dict)
    list_price_per_page_usd: float = 0.0
    actual_cost_per_page_usd: float = 0.0
    actual_cost_is_shadow: bool = False
    cap_usd: float | None = None
    region: str | None = None
    p50_ms_default: float = 2000.0
    expected_confidence: float = 0.7
    timeout_s: float | None = None
    image_timeout_s: float | None = None     # overrides router timeouts_s.image for calls with images
    image_detail: str | None = None          # provider hint (Cohere: low|high|auto)

    def trains_on_free_tier(self, env: dict[str, str] | None = None) -> bool:
        """Resolved training flag. Unknown => True (fail closed)."""
        env = os.environ if env is None else env
        raw = self.trains_on_free_tier_raw
        if raw is False:
            return False
        if isinstance(raw, str) and raw.strip().lower() == "false_if_opted_out":
            var = self.optout_env or "MISTRAL_TRAINING_OPTOUT_CONFIRMED"
            return str(env.get(var, "")).strip().lower() != "true"
        return True

    def price_in(self) -> float:
        return float(self.list_price_per_mtok.get("in", 0.0) or 0.0)

    def price_out(self) -> float:
        return float(self.list_price_per_mtok.get("out", 0.0) or 0.0)


class ChainEntry(BaseModel):
    model: str
    capability: float = 0.5
    when: str | None = None


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    requires: list[str] = Field(default_factory=lambda: ["text"])
    typical_tier: str = "PII"
    chain: list[ChainEntry]
    terminal: str | None = None
    exclude_producer_vendor: bool = False
    strict_order: bool = False               # try candidates in chain order; scores are logged only


class RouterSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    weights: dict[str, float] = Field(default_factory=lambda: {
        "capability": 0.35, "confidence": 0.25, "latency": -0.15,
        "shadow_cost": -0.10, "quota_pressure": -0.15})
    latency_ref_ms: float = 10000.0
    cost_ref_usd: float = 0.01
    daily_reserve_fraction: float = 0.10
    timeouts_s: dict[str, float] = Field(default_factory=lambda: {"text": 20, "image": 60, "ocr": 60})
    retries_per_model: int = 1
    breaker: dict[str, float] = Field(default_factory=lambda: {"failures": 3, "open_seconds": 60})
    default_max_tokens: int = 1024
    p50_min_samples: int = 3


class Registry(BaseModel):
    version: int = 1
    router: RouterSettings = Field(default_factory=RouterSettings)
    models: dict[str, ModelSpec]
    tasks: dict[str, TaskSpec]
    engines: list[dict] = Field(default_factory=list)
    embeddings: dict[str, EmbeddingSpec] = Field(default_factory=dict)

    def aws_billed_ids(self) -> set[str]:
        ids = {m.id for m in self.models.values() if m.provider == "bedrock"}
        return ids | {e.id for e in self.embeddings.values() if e.provider == "bedrock"}

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as e:
            raise RouterConfigError(f"unknown model id {model_id!r}") from e

    def task(self, name: str) -> TaskSpec:
        try:
            return self.tasks[name]
        except KeyError as e:
            raise RouterConfigError(f"unknown task {name!r}; known: {sorted(self.tasks)}") from e


VALID_COST_CLASSES = {"free", "local", "capped_fallback"}
# D-027: the ONLY Bedrock models the user approved. Ministral 3 14B is visible but NOT approved.
APPROVED_BEDROCK_MODELS = frozenset({
    "mistral.ministral-3-3b-instruct",
    "mistral.ministral-3-8b-instruct",
    "amazon.titan-embed-text-v2:0",
    "amazon.titan-embed-image-v1",
})


def _check_bedrock(kind: str, entry_id: str, provider: str, model: str) -> None:
    if provider == "bedrock" and model not in APPROVED_BEDROCK_MODELS:
        raise RouterConfigError(f"{kind} {entry_id}: bedrock model {model!r} is not on the approved list "
                                f"(D-027): {sorted(APPROVED_BEDROCK_MODELS)}")


class EmbeddingSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    provider: str
    model: str
    kind: str = "text"                    # text | image
    dims: int = 1024
    normalize: bool = True
    region: str | None = None
    cost_class: str = "local"
    trains_on_free_tier_raw: Any = Field(default="unknown", alias="trains_on_free_tier")
    price_per_mtok_in: float = 0.0
    price_per_image_usd: float = 0.0


def parse_registry(raw: dict) -> Registry:
    models: dict[str, ModelSpec] = {}
    for m in raw.get("models", []):
        spec = ModelSpec.model_validate(m)
        if spec.id in models:
            raise RouterConfigError(f"duplicate model id {spec.id}")
        if spec.cost_class not in VALID_COST_CLASSES:
            raise RouterConfigError(f"{spec.id}: cost_class {spec.cost_class!r} not free/local/capped_fallback")
        _check_bedrock("model", spec.id, spec.provider, spec.model)
        models[spec.id] = spec
    tasks: dict[str, TaskSpec] = {}
    for name, t in (raw.get("tasks") or {}).items():
        spec = TaskSpec.model_validate({**t, "name": name})
        for entry in spec.chain:
            if entry.model not in models:
                raise RouterConfigError(f"task {name}: chain references unknown model {entry.model}")
        tasks[name] = spec
    embeddings: dict[str, EmbeddingSpec] = {}
    for e in raw.get("embeddings") or []:
        spec = EmbeddingSpec.model_validate(e)
        if spec.cost_class not in VALID_COST_CLASSES:
            raise RouterConfigError(f"{spec.id}: cost_class {spec.cost_class!r} not free/local/capped_fallback")
        _check_bedrock("embedding", spec.id, spec.provider, spec.model)
        embeddings[spec.id] = spec
    return Registry(version=raw.get("version", 1),
                    router=RouterSettings.model_validate(raw.get("router") or {}),
                    models=models, tasks=tasks, engines=raw.get("engines") or [], embeddings=embeddings)


def load_registry(path: str | Path | None = None) -> Registry:
    p = Path(path or os.environ.get("ROUTER_REGISTRY") or DEFAULT_REGISTRY_PATH)
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return parse_registry(raw)
