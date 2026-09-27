"""LiteLLM-backed chat adapter for gemini, mistral, groq, cohere, ollama and bedrock."""
from __future__ import annotations

import os

from ..secrets import redact
from ..types import ProviderError, ProviderRequest, ProviderResponse
from . import ollama_base

PREFIX = {"gemini": "gemini", "groq": "groq", "mistral": "mistral", "cohere": "cohere_chat",
          "ollama": "ollama_chat", "bedrock": "bedrock"}
LADDER = ["json_schema", "json_object", "prompt"]


def _litellm():
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True
    litellm.set_verbose = False
    return litellm


def rate_headers(h: dict | None) -> dict:
    out = {}
    for k, v in (h or {}).items():
        kl = str(k).lower()
        kl = kl.removeprefix("llm_provider-")
        if (("ratelimit" in kl or "rate-limit" in kl or "call-limit" in kl or "call-remaining" in kl
             or kl == "retry-after") and "authorization" not in kl):
            out[kl] = str(v)
    return dict(sorted(out.items()))


def response_format(mode: str, schema: dict | None) -> dict | None:
    if schema is None or mode == "prompt":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}


def map_exception(e: Exception) -> ProviderError:
    import litellm

    status = getattr(e, "status_code", None)
    headers = rate_headers(getattr(e, "litellm_response_headers", None) and
                           dict(e.litellm_response_headers))
    msg = redact(str(e))[:400]
    table = [
        (litellm.RateLimitError, "rate_limit"),
        (litellm.Timeout, "timeout"),
        (litellm.AuthenticationError, "auth"),
        (litellm.PermissionDeniedError, "auth"),
        (litellm.NotFoundError, "not_found"),
        (litellm.ContextWindowExceededError, "bad_request"),
        (litellm.BadRequestError, "bad_request"),
        (litellm.APIConnectionError, "connection"),
        (litellm.ServiceUnavailableError, "server"),
        (litellm.InternalServerError, "server"),
    ]
    for cls, kind in table:
        if isinstance(e, cls):
            if kind == "bad_request" and ("does not exist" in msg or "model_not_found" in msg
                                          or "not found" in msg.lower() and "model" in msg.lower()):
                kind = "not_found"
            return ProviderError(kind, msg, status=status, headers=headers)
    if isinstance(status, int):
        if status == 429:
            return ProviderError("rate_limit", msg, status, headers)
        if status >= 500:
            return ProviderError("server", msg, status, headers)
        if status == 404:
            return ProviderError("not_found", msg, status, headers)
        if status in (401, 403):
            return ProviderError("auth", msg, status, headers)
        return ProviderError("bad_request", msg, status, headers)
    return ProviderError("server", f"{type(e).__name__}: {msg}", status, headers)


def upscale_small_images(messages: list[dict], min_px: int) -> list[dict]:
    """Some providers (Groq vision) reject images below `min_px` per side; upscale (nearest) those."""
    import base64
    import io

    from PIL import Image

    def fix(url: str) -> str:
        head, _, b64 = url.partition(",")
        if not head.startswith("data:image/"):
            return url
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        w, h = img.size
        if w >= min_px and h >= min_px:
            return url
        k = -(-min_px // min(w, h))
        buf = io.BytesIO()
        img.resize((w * k, h * k), Image.NEAREST).save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = [{**p, "image_url": {**p["image_url"], "url": fix(p["image_url"]["url"])}}
                 if p.get("type") == "image_url" else p for p in c]
            m = {**m, "content": c}
        out.append(m)
    return out


def _handler(timeout: float, seen: dict):
    """litellm HTTPHandler around an httpx client whose response hook captures rate-limit headers
    (litellm does not surface them in _hidden_params for every provider)."""
    try:
        import httpx
        from litellm.llms.custom_httpx.http_handler import HTTPHandler
    except ImportError:
        return None, None

    def hook(r):
        seen.update(rate_headers(dict(r.headers)))

    http = httpx.Client(timeout=timeout, event_hooks={"response": [hook]})
    return HTTPHandler(timeout=timeout, client=http), http


class LiteLLMProvider:
    def complete(self, req: ProviderRequest) -> ProviderResponse:
        litellm = _litellm()
        messages = req.messages
        if req.extra.get("min_image_px"):
            messages = upscale_small_images(messages, int(req.extra["min_image_px"]))
        kwargs: dict = {
            "model": f"{PREFIX[req.provider]}/{req.model_name}",
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "timeout": req.timeout_s,
            "num_retries": 0,
        }
        if req.provider == "ollama":
            kwargs["api_base"] = ollama_base()
        if req.provider == "bedrock":
            kwargs["aws_region_name"] = req.extra.get("region") or os.environ.get("AWS_REGION", "ap-south-1")
        if req.extra.get("params"):
            kwargs.update(req.extra["params"])        # registry `params:` passthrough
        if req.provider == "gemini":
            kwargs.pop("temperature", None)           # Gemini 3+: sampling params deprecated
        start = LADDER.index(req.json_mode) if req.json_mode in LADDER else 2
        modes = LADDER[start:] if req.schema is not None else ["prompt"]
        last: ProviderError | None = None
        for mode in modes:
            rf = response_format(mode, req.schema)
            call_kwargs = dict(kwargs, **({"response_format": rf} if rf else {}))
            seen: dict = {}
            handler, http = _handler(req.timeout_s, seen)
            if handler is not None:
                call_kwargs["client"] = handler
            try:
                resp = litellm.completion(**call_kwargs)
            except Exception as e:  # noqa: BLE001
                err = map_exception(e)
                if not err.headers:
                    err.headers = dict(seen)
                if err.kind == "bad_request" and mode != "prompt":
                    last = err      # provider rejected this JSON mode; degrade
                    continue
                raise err from None
            finally:
                if http is not None:
                    http.close()
            choice = resp.choices[0]
            text = getattr(choice.message, "content", None)
            usage = getattr(resp, "usage", None)
            hidden = getattr(resp, "_hidden_params", {}) or {}
            return ProviderResponse(
                text=text,
                tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
                tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
                resolved_model=str(getattr(resp, "model", "") or req.model_name),
                headers=rate_headers(hidden.get("additional_headers")) or dict(seen),
                json_mode_used=mode,
            )
        raise last or ProviderError("bad_request", "all JSON modes rejected")
