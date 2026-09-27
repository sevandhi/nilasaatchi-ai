"""Shared types for the router: public result, provider request/response, errors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

PRIVACY_TIERS = ("PII", "PSEUDO", "PUBLIC")


class RouterResult(BaseModel):
    """Public result of `app.router.call`. Contract shared with every other agent."""

    ok: bool
    model_id: str = ""
    text: str | None = None
    data: dict | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    shadow_cost_usd: float = 0.0
    route_log: dict = Field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None        # deferred | no_eligible | all_failed (None when ok)


class PrivacyViolation(RuntimeError):
    """Raised when a payload of a given privacy tier would reach an ineligible model."""


class RouterConfigError(ValueError):
    """Unknown task, bad registry entry, etc."""


# error kinds that count toward the circuit breaker and are retried once
TRANSIENT_KINDS = frozenset({"rate_limit", "server", "timeout", "connection"})


class ProviderError(Exception):
    """Normalised provider failure. `kind` drives retry / breaker / fallback decisions."""

    def __init__(self, kind: str, message: str = "", status: int | None = None,
                 headers: dict | None = None):
        super().__init__(message or kind)
        self.kind = kind          # rate_limit|server|timeout|connection|auth|not_found|bad_request|
        #                           no_credentials|fixture_missing|unsupported|chaos
        self.status = status
        self.headers = headers or {}

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_KINDS

    def __str__(self) -> str:  # never include request details (keys live in headers)
        s = f"{self.kind}"
        if self.status:
            s += f" ({self.status})"
        msg = super().__str__()
        if msg and msg != self.kind:
            s += f": {msg}"
        return s


@dataclass
class ProviderRequest:
    """What a provider adapter receives. `model_name` is the provider-side model id."""

    model_id: str
    provider: str
    model_name: str
    messages: list[dict]
    schema: dict | None = None
    json_mode: str = "prompt"          # json_schema | json_object | prompt
    max_tokens: int = 1024
    temperature: float = 0.0
    timeout_s: float = 20.0
    images: list[bytes] = field(default_factory=list)
    kind: str = "chat"                 # chat | ocr
    extra: dict[str, Any] = field(default_factory=dict)   # region, api_base, ...


@dataclass
class ProviderResponse:
    text: str | None
    tokens_in: int = 0
    tokens_out: int = 0
    resolved_model: str = ""
    headers: dict = field(default_factory=dict)        # rate-limit headers only
    data: dict | None = None                           # OCR structured output
    pages: int = 0
    latency_ms: int | None = None                      # set by fixtures on replay
    json_mode_used: str | None = None
    image_tokens: int = 0                              # provider-reported image tokens (in tokens_in)
