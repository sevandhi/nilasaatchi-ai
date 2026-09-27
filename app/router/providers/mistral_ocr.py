"""Direct Mistral OCR adapter (`POST /v1/ocr`) via the mistralai SDK.

Input: one or more images / PDFs (bytes). Output text = page markdown (tables inline);
data = {"pages": [{index, markdown, dimensions}], "pages_processed"}. When a JSON schema is
given, it is sent as `document_annotation_format` and the annotation JSON becomes `text`
(the router validates it).
"""
from __future__ import annotations

import json
import os

import httpx

from ..messages import data_url, sniff_mime
from ..secrets import redact
from ..types import ProviderError, ProviderRequest, ProviderResponse
from .litellm_provider import rate_headers


def _map(e: Exception) -> ProviderError:
    if isinstance(e, ProviderError):
        return e
    if isinstance(e, httpx.TimeoutException):
        return ProviderError("timeout", "mistral ocr timeout")
    if isinstance(e, httpx.TransportError):
        return ProviderError("connection", redact(str(e))[:300])
    status = getattr(e, "status_code", None)
    raw = getattr(e, "raw_response", None)
    if status is None and raw is not None:
        status = getattr(raw, "status_code", None)
    msg = redact(str(e))[:400]
    if status == 429:
        return ProviderError("rate_limit", msg, 429)
    if status in (401, 403):
        return ProviderError("auth", msg, status)
    if status == 404:
        return ProviderError("not_found", msg, 404)
    if isinstance(status, int) and status >= 500:
        return ProviderError("server", msg, status)
    if isinstance(status, int):
        return ProviderError("bad_request", msg, status)
    return ProviderError("server", f"{type(e).__name__}: {msg}")


def _document(b: bytes) -> dict:
    if sniff_mime(b) == "application/pdf":
        return {"type": "document_url", "document_url": data_url(b)}
    return {"type": "image_url", "image_url": data_url(b)}


class MistralOCRProvider:
    def complete(self, req: ProviderRequest) -> ProviderResponse:
        if not req.images:
            raise ProviderError("unsupported", "mistral OCR needs an image or PDF input")
        key = os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise ProviderError("no_credentials", "missing MISTRAL_API_KEY")
        from mistralai.client import Mistral

        seen: dict = {}

        def hook(r: httpx.Response) -> None:
            seen.update(rate_headers(dict(r.headers)))

        http = httpx.Client(timeout=req.timeout_s, event_hooks={"response": [hook]})
        pages: list[dict] = []
        annotations: list[str] = []
        processed = 0
        resolved = req.model_name
        try:
            client = Mistral(api_key=key, client=http, timeout_ms=int(req.timeout_s * 1000))
            for b in req.images:
                kwargs: dict = {"model": req.model_name, "document": _document(b),
                                "include_image_base64": False}
                if req.schema is not None:
                    kwargs["document_annotation_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": "output", "schema": req.schema, "strict": True}}
                resp = client.ocr.process(**kwargs)
                resolved = getattr(resp, "model", None) or resolved
                for p in resp.pages or []:
                    dims = getattr(p, "dimensions", None)
                    pages.append({"index": len(pages), "markdown": p.markdown,
                                  "dimensions": dims.model_dump() if hasattr(dims, "model_dump") else None})
                usage = getattr(resp, "usage_info", None)
                processed += int(getattr(usage, "pages_processed", 0) or len(resp.pages or []))
                ann = getattr(resp, "document_annotation", None)
                if ann:
                    annotations.append(ann if isinstance(ann, str) else json.dumps(ann))
        except Exception as e:  # noqa: BLE001
            err = _map(e)
            if not err.headers:
                err.headers = dict(seen)
            raise err from None
        finally:
            http.close()
        text = "\n\n".join(p["markdown"] or "" for p in pages)
        if req.schema is not None:
            text = annotations[0] if len(annotations) == 1 else (
                json.dumps({"documents": [json.loads(a) for a in annotations]}) if annotations else None)
        return ProviderResponse(text=text, resolved_model=str(resolved), headers=seen,
                                data={"pages": pages, "pages_processed": processed},
                                pages=processed, json_mode_used="ocr")
