"""VCR-style recorder/replayer for provider calls.

ROUTER_MODE=live    call the provider, record nothing
ROUTER_MODE=record  call the provider and write a fixture per request
ROUTER_MODE=replay  never touch the network; serve fixtures (missing => ProviderError fixture_missing)

Fixtures are keyed by a SHA-256 of the canonical request (images hashed, never embedded) and
store only the response (text, tokens, resolved model, rate-limit headers, latency). Prompt text
is not written, so recording never persists PII or keys.
Directory: ROUTER_FIXTURE_DIR (default tests/router/fixtures).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from .registry import REPO_ROOT
from .secrets import redact
from .types import ProviderError, ProviderRequest, ProviderResponse

DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "router" / "fixtures"
MODES = ("live", "record", "replay")
_DATA_URL = re.compile(r"data:[\w/+.-]+;base64,([A-Za-z0-9+/=]+)")


def current_mode() -> str:
    m = os.environ.get("ROUTER_MODE", "").strip().lower()
    if m in MODES:
        return m
    return "replay" if os.environ.get("PYTEST_CURRENT_TEST") else "live"


def fixture_dir() -> Path:
    return Path(os.environ.get("ROUTER_FIXTURE_DIR") or DEFAULT_FIXTURE_DIR)


def _hash_data_urls(s: str) -> str:
    return _DATA_URL.sub(lambda m: "sha256:" + hashlib.sha256(m.group(1).encode()).hexdigest(), s)


def request_key(req: ProviderRequest) -> str:
    body = {
        "model_id": req.model_id, "provider": req.provider, "model_name": req.model_name,
        "kind": req.kind, "messages": req.messages, "schema": req.schema, "json_mode": req.json_mode,
        "max_tokens": req.max_tokens, "temperature": req.temperature,
        "images": [hashlib.sha256(b).hexdigest() for b in req.images],
    }
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(_hash_data_urls(canon).encode()).hexdigest()


def _path(req: ProviderRequest, key: str) -> Path:
    return fixture_dir() / req.model_id / f"{key[:24]}.json"


def save(req: ProviderRequest, key: str, resp: ProviderResponse | None, err: ProviderError | None,
         latency_ms: int) -> Path:
    p = _path(req, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"key": key, "model_id": req.model_id, "model_name": req.model_name, "kind": req.kind,
           "latency_ms": latency_ms}
    if resp is not None:
        d = asdict(resp)
        d["latency_ms"] = latency_ms
        rec["response"] = d
    if err is not None:
        rec["error"] = {"kind": err.kind, "status": err.status, "message": redact(str(err))}
    p.write_text(redact(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False)), encoding="utf-8")
    return p


def load(req: ProviderRequest, key: str) -> ProviderResponse:
    p = _path(req, key)
    if not p.exists():
        raise ProviderError("fixture_missing", f"no fixture for {req.model_id} key={key[:12]}")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if "error" in rec:
        e = rec["error"]
        raise ProviderError(e["kind"], e.get("message", ""), status=e.get("status"))
    r = rec["response"]
    return ProviderResponse(**{k: r.get(k) for k in ProviderResponse.__dataclass_fields__ if k in r})
