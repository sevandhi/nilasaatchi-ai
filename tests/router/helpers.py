"""Fake provider + router factory for offline router tests."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.router.clock import FakeClock
from app.router.core import Router
from app.router.registry import load_registry, parse_registry
from app.router.types import ProviderError, ProviderRequest, ProviderResponse

TEST_REGISTRY_PATH = Path(__file__).parent / "fixtures" / "models_test.yaml"


def load_test_registry():
    """Frozen test registry (includes the D-018-disabled mistral-ocr), independent of production config."""
    return load_registry(TEST_REGISTRY_PATH)

OK_JSON = json.dumps({"ok": True, "answer": 7})
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "answer": {"type": "integer"}},
          "required": ["ok", "answer"], "additionalProperties": False}


class FakeProvider:
    """Scripted provider. script[model_id] is a list consumed per call; items are a str (text),
    ProviderResponse, ProviderError, or callable(req)->any of those. Empty script => OK_JSON.
    Each call advances the fake clock by `latency_s[model_id]` (default 0.1 s)."""

    def __init__(self, clock: FakeClock, script: dict | None = None, latency_s: dict | None = None):
        self.clock = clock
        self.script = defaultdict(list, {k: list(v) for k, v in (script or {}).items()})
        self.latency_s = latency_s or {}
        self.calls: list[ProviderRequest] = []

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        self.calls.append(req)
        self.clock.advance(self.latency_s.get(req.model_id, 0.1))
        item = self.script[req.model_id].pop(0) if self.script[req.model_id] else OK_JSON
        if callable(item) and not isinstance(item, (ProviderError, ProviderResponse)):
            item = item(req)
        if isinstance(item, ProviderError):
            raise item
        if isinstance(item, ProviderResponse):
            return item
        return ProviderResponse(text=item, tokens_in=100, tokens_out=20, resolved_model=req.model_name,
                                headers={"x-ratelimit-remaining-requests": "99"})

    def called_models(self) -> list[str]:
        return [c.model_id for c in self.calls]


def err429() -> ProviderError:
    return ProviderError("rate_limit", "fake 429", status=429)


def err503() -> ProviderError:
    return ProviderError("server", "fake 503", status=503)


def make_router(tmp_path, script=None, registry=None, clock=None, credentials=None, latency_s=None):
    clock = clock or FakeClock()
    fake = FakeProvider(clock, script, latency_s)
    providers = {p: fake for p in ("gemini", "groq", "mistral", "cohere", "ollama", "bedrock", "mistral_ocr")}
    router = Router(registry=registry or load_test_registry(), providers=providers, clock=clock,
                    credentials=credentials or (lambda p: (True, "test")), doctor={},
                    data_path=tmp_path / "rdata")
    return router, fake, clock


MINI_REGISTRY = {
    "router": {"retries_per_model": 1, "breaker": {"failures": 3, "open_seconds": 60}},
    "models": [
        {"id": "a", "provider": "groq", "model": "a-1", "vendor": "va", "modalities": ["text"],
         "trains_on_free_tier": False, "cost_class": "free", "json_mode": "json_schema",
         "list_price_per_mtok": {"in": 0.1, "out": 0.1}, "p50_ms_default": 1000, "expected_confidence": 0.8},
        {"id": "b", "provider": "groq", "model": "b-1", "vendor": "vb", "modalities": ["text"],
         "trains_on_free_tier": False, "cost_class": "free", "json_mode": "json_schema",
         "list_price_per_mtok": {"in": 0.1, "out": 0.1}, "p50_ms_default": 1000, "expected_confidence": 0.8},
        {"id": "c", "provider": "gemini", "model": "c-1", "vendor": "vc", "modalities": ["text"],
         "trains_on_free_tier": True, "cost_class": "free", "json_mode": "json_schema",
         "p50_ms_default": 1000, "expected_confidence": 0.8},
    ],
    "tasks": {
        "t": {"requires": ["text"], "typical_tier": "PII", "terminal": "review_queue",
              "chain": [{"model": "a", "capability": 1.0}, {"model": "b", "capability": 0.9},
                        {"model": "c", "capability": 0.8}]},
        "tie": {"requires": ["text"], "chain": [{"model": "b", "capability": 0.9},
                                                {"model": "a", "capability": 0.9}]},
    },
}


def mini_registry(**overrides):
    import copy
    raw = copy.deepcopy(MINI_REGISTRY)
    for k, v in overrides.items():
        raw[k] = v
    return parse_registry(raw)
