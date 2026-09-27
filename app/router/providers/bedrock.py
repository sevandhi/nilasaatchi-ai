"""Direct boto3 adapter for Amazon Bedrock (D-027): Converse for chat/vision, InvokeModel for Titan
embeddings. Direct boto3 (not litellm) because Converse returns exact token usage and takes raw
image bytes, and SSO-expiry errors can be recognised precisely.

Profile: AWS_PROFILE (SSO `fai-builder`); region: model `region` or AWS_REGION (ap-south-1).
"""
from __future__ import annotations

import base64
import json
import os
import threading

from ..messages import sniff_mime
from ..secrets import redact
from ..types import ProviderError, ProviderRequest, ProviderResponse

SSO_EXPIRED_MSG = "AWS SSO session expired — run `make aws-login`"
_EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException",
                  "InvalidClientTokenId", "UnauthorizedException"}
_clients: dict[tuple, object] = {}
_lock = threading.Lock()


def _client(service: str, region: str | None, timeout_s: float):
    import boto3
    from botocore.config import Config

    profile = os.environ.get("AWS_PROFILE") or None
    region = region or os.environ.get("AWS_REGION") or "ap-south-1"
    key = (service, profile, region, int(timeout_s))
    with _lock:
        if key not in _clients:
            session = boto3.Session(profile_name=profile, region_name=region)
            _clients[key] = session.client(service, config=Config(
                read_timeout=timeout_s, connect_timeout=10, retries={"max_attempts": 1, "mode": "standard"}))
        return _clients[key]


def reset_clients() -> None:
    with _lock:
        _clients.clear()


def map_exception(e: Exception) -> ProviderError:
    if isinstance(e, ProviderError):
        return e
    name = type(e).__name__
    try:
        from botocore import exceptions as bx
    except ImportError:  # pragma: no cover
        bx = None
    sso_types = tuple(getattr(bx, n) for n in ("SSOTokenLoadError", "UnauthorizedSSOTokenError",
                                               "TokenRetrievalError", "SSOError") if bx and hasattr(bx, n))
    if (sso_types and isinstance(e, sso_types)) or "sso" in name.lower() or "Token has expired" in str(e):
        reset_clients()
        return ProviderError("auth_expired", SSO_EXPIRED_MSG)
    if bx is not None and isinstance(e, bx.NoCredentialsError | bx.ProfileNotFound):
        return ProviderError("no_credentials", f"AWS credentials unavailable ({name})")
    if bx is not None and isinstance(e, bx.ReadTimeoutError | bx.ConnectTimeoutError):
        return ProviderError("timeout", f"bedrock {name}")
    if bx is not None and isinstance(e, bx.EndpointConnectionError):
        return ProviderError("connection", f"bedrock {name}")
    resp = getattr(e, "response", None) or {}
    code = (resp.get("Error") or {}).get("Code", "")
    status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    msg = redact(str(e))[:400]
    if code in _EXPIRED_CODES:
        reset_clients()
        return ProviderError("auth_expired", SSO_EXPIRED_MSG, status)
    if code in ("ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"):
        return ProviderError("rate_limit", msg, status or 429)
    if code in ("ModelTimeoutException",):
        return ProviderError("timeout", msg, status)
    if code in ("ServiceUnavailableException", "InternalServerException", "ModelNotReadyException"):
        return ProviderError("server", msg, status or 503)
    if code in ("AccessDeniedException",):
        return ProviderError("auth", msg, status or 403)
    if code in ("ResourceNotFoundException",) or "model identifier is invalid" in msg:
        return ProviderError("not_found", msg, status or 404)
    if code in ("ValidationException", "ModelErrorException"):
        return ProviderError("bad_request", msg, status or 400)
    if isinstance(status, int) and status >= 500:
        return ProviderError("server", msg, status)
    return ProviderError("server", f"{name}: {msg}", status)


def _decode_data_url(url: str) -> bytes | None:
    head, _, b64 = url.partition(",")
    return base64.b64decode(b64) if head.startswith("data:") else None


def _block_for_bytes(b: bytes, idx: int) -> dict:
    mime = sniff_mime(b)
    if mime == "application/pdf":
        return {"document": {"format": "pdf", "name": f"doc{idx}", "source": {"bytes": b}}}
    fmt = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}.get(mime)
    if fmt is None:        # tiff etc. -> PNG
        import io

        from PIL import Image
        buf = io.BytesIO()
        Image.open(io.BytesIO(b)).convert("RGB").save(buf, "PNG")
        b, fmt = buf.getvalue(), "png"
    return {"image": {"format": fmt, "source": {"bytes": b}}}


def to_converse(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """OpenAI-style messages (text / image_url / file parts) -> (system, messages) for Converse."""
    system, out = [], []
    n = 0
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "system":
            system.append({"text": content if isinstance(content, str) else json.dumps(content)})
            continue
        blocks = []
        parts = content if isinstance(content, list) else [{"type": "text", "text": str(content or "")}]
        for p in parts:
            if p.get("type") == "text":
                if p.get("text"):
                    blocks.append({"text": p["text"]})
            elif p.get("type") in ("image_url", "file"):
                url = p["image_url"]["url"] if p["type"] == "image_url" else p["file"]["file_data"]
                b = _decode_data_url(url)
                if b is not None:
                    n += 1
                    blocks.append(_block_for_bytes(b, n))
        if not blocks:
            blocks = [{"text": "(empty)"}]
        r = "assistant" if role == "assistant" else "user"
        if out and out[-1]["role"] == r:        # Converse requires alternating roles
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": r, "content": blocks})
    return system, out


class BedrockConverseProvider:
    def complete(self, req: ProviderRequest) -> ProviderResponse:
        try:
            client = _client("bedrock-runtime", req.extra.get("region"), req.timeout_s)
            system, msgs = to_converse(req.messages)
            kwargs = {"modelId": req.model_name, "messages": msgs,
                      "inferenceConfig": {"maxTokens": int(req.max_tokens), "temperature": float(req.temperature)}}
            if system:
                kwargs["system"] = system
            r = client.converse(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise map_exception(e) from None
        blocks = (r.get("output", {}).get("message", {}) or {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks if "text" in b)
        usage = r.get("usage", {}) or {}
        return ProviderResponse(
            text=text, tokens_in=int(usage.get("inputTokens", 0)), tokens_out=int(usage.get("outputTokens", 0)),
            resolved_model=req.model_name,
            headers={"bedrock-latency-ms": str((r.get("metrics") or {}).get("latencyMs", ""))},
            json_mode_used="prompt")


def invoke_titan_text(model: str, text: str, dims: int, normalize: bool, region: str | None,
                      timeout_s: float = 30) -> tuple[list[float], int]:
    try:
        client = _client("bedrock-runtime", region, timeout_s)
        r = client.invoke_model(modelId=model, contentType="application/json", accept="application/json",
                                body=json.dumps({"inputText": text, "dimensions": dims, "normalize": normalize}))
        j = json.loads(r["body"].read())
    except Exception as e:  # noqa: BLE001
        raise map_exception(e) from None
    return j["embedding"], int(j.get("inputTextTokenCount", 0))


def invoke_titan_image(model: str, png: bytes, dims: int, region: str | None,
                       timeout_s: float = 30) -> list[float]:
    try:
        client = _client("bedrock-runtime", region, timeout_s)
        body = {"inputImage": base64.b64encode(png).decode(), "embeddingConfig": {"outputEmbeddingLength": dims}}
        r = client.invoke_model(modelId=model, contentType="application/json", accept="application/json",
                                body=json.dumps(body))
        j = json.loads(r["body"].read())
    except Exception as e:  # noqa: BLE001
        raise map_exception(e) from None
    return j["embedding"]
