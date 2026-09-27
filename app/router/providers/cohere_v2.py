"""Direct Cohere v2 chat adapter (POST https://api.cohere.com/v2/chat), used for text and vision.

Why not litellm: its Cohere path ignores injected HTTP clients (no rate-limit headers) and reports only
`usage.tokens.input_tokens`, which excludes `image_tokens`, so vision calls looked as if the image was
never sent (D-036). Here images are sent as v2 content parts
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail": <optional>}}
and tokens_in = input_tokens + image_tokens (image_tokens also reported separately).
"""
from __future__ import annotations

import os

import httpx

from ..secrets import redact
from ..types import ProviderError, ProviderRequest, ProviderResponse
from .litellm_provider import rate_headers

URL = "https://api.cohere.com/v2/chat"


def build_body(req: ProviderRequest, mode: str) -> dict:
    """Cohere v2 request body from provider-neutral messages. `mode`: json_schema|json_object|prompt."""
    detail = req.extra.get("image_detail")
    msgs = []
    for m in req.messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                t = p.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": p.get("text", "")})
                elif t == "image_url":
                    iu = {"url": p["image_url"]["url"]}
                    if detail:
                        iu["detail"] = detail
                    parts.append({"type": "image_url", "image_url": iu})
                elif t == "file":
                    raise ProviderError("unsupported", "Cohere v2 chat does not accept PDF/file parts")
            content = parts
        msgs.append({"role": m.get("role", "user"), "content": content})
    body: dict = {"model": req.model_name, "messages": msgs, "max_tokens": int(req.max_tokens),
                  "temperature": float(req.temperature)}
    if req.schema is not None and mode == "json_schema":
        body["response_format"] = {"type": "json_object", "json_schema": req.schema}
    elif req.schema is not None and mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    return body


def _error(r: httpx.Response) -> ProviderError:
    msg = redact(r.text)[:400]
    hdr = rate_headers(dict(r.headers))
    s = r.status_code
    if s == 429:
        return ProviderError("rate_limit", msg, s, hdr)
    if s in (401, 403):
        return ProviderError("auth", msg, s, hdr)
    if s == 404 or (s == 400 and "not found" in msg.lower() and "model" in msg.lower()):
        return ProviderError("not_found", msg, s, hdr)
    if s >= 500:
        return ProviderError("server", msg, s, hdr)
    return ProviderError("bad_request", msg, s, hdr)


class CohereV2Provider:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport          # tests inject httpx.MockTransport

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        key = os.environ.get("COHERE_API_KEY") or os.environ.get("CO_API_KEY")
        if not key:
            raise ProviderError("no_credentials", "missing COHERE_API_KEY")
        ladder = ["json_schema", "json_object", "prompt"]
        modes = ladder[ladder.index(req.json_mode):] if (req.schema is not None and req.json_mode in ladder) \
            else ["prompt"]
        last: ProviderError | None = None
        with httpx.Client(timeout=req.timeout_s, transport=self.transport) as client:
            for mode in modes:
                body = build_body(req, mode)
                try:
                    r = client.post(URL, json=body, headers={"Authorization": f"Bearer {key}"})
                except httpx.TimeoutException:
                    raise ProviderError("timeout", "cohere timeout") from None
                except httpx.TransportError as e:
                    raise ProviderError("connection", redact(str(e))[:300]) from None
                if r.status_code >= 400:
                    err = _error(r)
                    if err.kind == "bad_request" and mode != "prompt":
                        last = err
                        continue
                    raise err
                j = r.json()
                text = "".join(c.get("text", "") for c in (j.get("message") or {}).get("content") or []
                               if c.get("type", "text") == "text")
                tok = (j.get("usage") or {}).get("tokens") or {}
                img = int(tok.get("image_tokens", 0) or 0)
                return ProviderResponse(
                    text=text, tokens_in=int(tok.get("input_tokens", 0) or 0) + img,
                    tokens_out=int(tok.get("output_tokens", 0) or 0), resolved_model=req.model_name,
                    headers=rate_headers(dict(r.headers)), json_mode_used=mode, image_tokens=img)
        raise last or ProviderError("bad_request", "all JSON modes rejected")
