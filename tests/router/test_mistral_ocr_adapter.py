"""Mistral OCR adapter (disabled in production by D-018) stays unit-tested with a fake SDK client."""
from __future__ import annotations

import json
import sys
import types

import pytest

from app.router.providers.mistral_ocr import MistralOCRProvider
from app.router.types import ProviderError, ProviderRequest
from tests.router.helpers import make_router

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class _Obj(types.SimpleNamespace):
    pass


def _fake_sdk(monkeypatch, behaviour):
    calls = []

    class FakeMistral:
        def __init__(self, api_key, client=None, timeout_ms=None):
            assert api_key == "test-key-000000"
            self.ocr = _Obj(process=self._process)

        def _process(self, **kw):
            calls.append(kw)
            return behaviour(kw)

    mod = types.ModuleType("mistralai.client")
    mod.Mistral = FakeMistral
    monkeypatch.setitem(sys.modules, "mistralai.client", mod)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-000000")
    return calls


def _req(images, schema=None):
    return ProviderRequest(model_id="mistral-ocr", provider="mistral_ocr", model_name="mistral-ocr-latest",
                           messages=[], schema=schema, images=images, kind="ocr", timeout_s=5)


def _resp(markdown, annotation=None):
    return _Obj(model="mistral-ocr-2512", document_annotation=annotation, usage_info=_Obj(pages_processed=1),
                pages=[_Obj(markdown=markdown, dimensions=None)])


def test_ocr_markdown_and_pages(monkeypatch):
    calls = _fake_sdk(monkeypatch, lambda kw: _resp("| S.No | Extent |\n| 233/2 | 0.45 |"))
    r = MistralOCRProvider().complete(_req([PNG, b"%PDF-1.4 x"]))
    assert "233/2" in r.text and r.pages == 2 and r.resolved_model == "mistral-ocr-2512"
    assert calls[0]["document"]["type"] == "image_url" and calls[1]["document"]["type"] == "document_url"
    assert r.data["pages_processed"] == 2 and len(r.data["pages"]) == 2


def test_ocr_schema_uses_document_annotation(monkeypatch):
    calls = _fake_sdk(monkeypatch, lambda kw: _resp("x", annotation=json.dumps({"total": 238})))
    r = MistralOCRProvider().complete(_req([PNG], schema={"type": "object"}))
    assert json.loads(r.text) == {"total": 238}
    assert calls[0]["document_annotation_format"]["type"] == "json_schema"


def test_ocr_errors_are_mapped(monkeypatch):
    class Http429(Exception):
        status_code = 429

    def boom(kw):
        raise Http429("Rate limit exceeded test-key-000000")

    _fake_sdk(monkeypatch, boom)
    with pytest.raises(ProviderError) as ei:
        MistralOCRProvider().complete(_req([PNG]))
    assert ei.value.kind == "rate_limit" and "test-key-000000" not in str(ei.value)


def test_ocr_needs_image_and_key(monkeypatch):
    with pytest.raises(ProviderError, match="unsupported"):
        MistralOCRProvider().complete(_req([]))
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="no_credentials"):
        MistralOCRProvider().complete(_req([PNG]))


def test_router_ocr_cost_tracking_with_test_registry(tmp_path, monkeypatch):
    """Through the router (test registry, opt-out confirmed): OCR cost is drawn from the credit."""
    from app.router.types import ProviderResponse
    monkeypatch.setenv("ROUTER_MODE", "live")
    monkeypatch.setenv("MISTRAL_TRAINING_OPTOUT_CONFIRMED", "true")
    router, _, _ = make_router(tmp_path, script={"mistral-ocr": [ProviderResponse(
        text="| 233/2 |", pages=3, data={"pages": []}, resolved_model="mistral-ocr-latest")]})
    r = router.call("table_read_pii", {"prompt": "read"}, privacy_tier="PII", images=[PNG])
    assert r.model_id == "mistral-ocr" and r.shadow_cost_usd == pytest.approx(0.012)
    assert router.quota.used("mistral-ocr", "monthly_credit_usd") == pytest.approx(0.012)
