"""Provider adapters. The ONLY place in the repo allowed to call model SDKs / HTTP APIs.

* `litellm_provider.LiteLLMProvider` — gemini, mistral (chat), groq, cohere, ollama, bedrock
* `mistral_ocr.MistralOCRProvider`   — direct `/v1/ocr` via the mistralai SDK
* `listing.list_models`              — provider model-list endpoints (used by `make doctor`)
"""
from __future__ import annotations

import os
import socket
import time
from typing import Protocol

from ..types import ProviderRequest, ProviderResponse


class Provider(Protocol):
    def complete(self, req: ProviderRequest) -> ProviderResponse: ...


KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "mistral_ocr": ("MISTRAL_API_KEY",),
    "cohere": ("COHERE_API_KEY", "CO_API_KEY"),
}

_cache: dict[str, tuple[float, bool, str]] = {}


def ollama_base() -> str:
    return os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"


def _ollama_up() -> tuple[bool, str]:
    base = ollama_base().split("://", 1)[-1].rstrip("/")
    host, _, port = base.partition(":")
    try:
        with socket.create_connection((host or "localhost", int(port or 11434)), timeout=0.3):
            return True, "ollama reachable"
    except OSError:
        return False, f"ollama not reachable at {host}:{port or 11434}"


def _bedrock_creds() -> tuple[bool, str]:
    try:
        import boto3
    except ImportError:
        return False, "boto3 not installed"
    try:
        creds = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None).get_credentials()
        if creds is not None:
            creds.get_frozen_credentials()      # forces the SSO token check (raises if expired)
    except Exception as e:  # noqa: BLE001  profile not found, SSO expired, ...
        from .bedrock import map_exception
        err = map_exception(e)
        if err.kind == "auth_expired":
            return False, err.args[0]
        return False, f"aws credentials unavailable ({type(e).__name__})"
    return (creds is not None), ("aws credentials found" if creds else "no aws credentials")


def mark_unavailable(provider: str, reason: str, seconds: float = 300.0) -> None:
    """Called by the router on e.g. an expired AWS SSO session: provider filtered for `seconds`."""
    _cache[provider] = (time.monotonic() - 30 + seconds, False, reason)


def credentials_available(provider: str) -> tuple[bool, str]:
    """(available, human reason). Never returns key material."""
    if provider in KEY_ENV:
        ok = any(os.environ.get(k) for k in KEY_ENV[provider])
        return ok, ("key present" if ok else f"missing {KEY_ENV[provider][0]}")
    if provider in ("ollama", "bedrock"):
        now = time.monotonic()
        hit = _cache.get(provider)
        if hit and now - hit[0] < 30:   # mark_unavailable stores a future timestamp to extend this
            return hit[1], hit[2]
        ok, why = _ollama_up() if provider == "ollama" else _bedrock_creds()
        _cache[provider] = (now, ok, why)
        return ok, why
    return False, f"unknown provider {provider}"


def default_providers() -> dict[str, Provider]:
    from .bedrock import BedrockConverseProvider
    from .cohere_v2 import CohereV2Provider
    from .litellm_provider import LiteLLMProvider
    from .mistral_ocr import MistralOCRProvider

    lp = LiteLLMProvider()
    return {"gemini": lp, "groq": lp, "mistral": lp, "cohere": CohereV2Provider(), "ollama": lp,
            "bedrock": BedrockConverseProvider(), "mistral_ocr": MistralOCRProvider()}
