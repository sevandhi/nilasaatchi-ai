"""Chaos hook: `ROUTER_CHAOS=gemini:down,groq:slow,groq-qwen-vl:429,cohere:badjson`.

Targets match a provider name or a model id. Effects:
  down    -> 503 server error          429   -> rate-limit error
  timeout -> timeout error             slow  -> +8 s latency (then timeout if over budget)
  badjson -> provider returns invalid JSON (exercises repair -> alternate model)
A runtime override (e.g. the UI toggle) can be set with `set_chaos()`.
"""
from __future__ import annotations

import os

from .types import ProviderError

_override: str | None = None
SLOW_EXTRA_S = 8.0


def set_chaos(spec: str | None) -> None:
    global _override
    _override = spec


def parse(spec: str | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        target, effect = part.split(":", 1)
        out.setdefault(target.strip(), set()).add(effect.strip().lower())
    return out


def effects_for(model_id: str, provider: str) -> set[str]:
    spec = _override if _override is not None else os.environ.get("ROUTER_CHAOS", "")
    table = parse(spec)
    return table.get(model_id, set()) | table.get(provider, set()) | table.get("*", set())


def pre_call(effects: set[str]) -> None:
    """Raise the forced failure, if any. `slow`/`badjson` are applied by the caller."""
    if "down" in effects:
        raise ProviderError("server", "chaos: provider down", status=503)
    if "429" in effects:
        raise ProviderError("rate_limit", "chaos: rate limited", status=429)
    if "timeout" in effects:
        raise ProviderError("timeout", "chaos: timeout")
