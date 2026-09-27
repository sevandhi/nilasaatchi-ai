import types

import pytest

from pipeline.classify import llm_classifier
from pipeline.classify.llm_cache import ContentCache


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    from pipeline.classify import llm_cache

    monkeypatch.setattr(llm_cache, "_cache", ContentCache(tmp_path / "cache"))
    yield


class _FakeResult:
    def __init__(self, ok=True, data=None, model_id="bedrock-ministral-3b", error=None):
        self.ok = ok
        self.data = data
        self.model_id = model_id
        self.error = error


def _stub_router(monkeypatch, fn):
    monkeypatch.setitem(__import__("sys").modules, "app.router", types.SimpleNamespace(call=fn))


def test_needs_vision_fallback_true_for_empty_text():
    assert llm_classifier.needs_vision_fallback("") is True


def test_needs_vision_fallback_false_for_long_good_text():
    text = "GOVERNMENT OF TAMIL NADU GAZETTE EXTRAORDINARY PUBLISHED BY AUTHORITY " * 5
    assert llm_classifier.needs_vision_fallback(text) is False


def test_rule_hints_returns_top_matches():
    hints = llm_classifier.rule_hints("FORM E (See Rule 8) notice under section 4(2)", 40)
    assert hints[0]["doc_type"] == "FORM_E"


def test_classify_document_llm_text_path(monkeypatch):
    seen = {}

    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        seen["task"] = task
        seen["images"] = images
        seen["privacy_tier"] = privacy_tier
        return _FakeResult(data={
            "doc_type": "FORM_E", "stage": "POSSESSION_NOTICE", "scheme_relevance": "allikulam",
            "segments": [{"page_from": 1, "page_to": 2, "doc_type": "FORM_E",
                           "scheme_relevance": "allikulam"}],
            "confidence": 0.9, "rationale": "Form E heading",
        })

    _stub_router(monkeypatch, fake_call)

    good_text = "FORM E (See Rule 8) notice under section 4(2) to surrender possession " * 4
    result = llm_classifier.classify_document_llm(
        class_text=good_text, folder_label="Form E", filename="e1.pdf", page_count=2,
    )
    assert result["doc_type"] == "FORM_E"
    assert seen["task"] == "classify_text"
    assert seen["images"] is None
    assert seen["privacy_tier"] == "PII"
    assert result["_meta"]["from_cache"] is False


def test_classify_document_llm_vision_path_when_text_poor(monkeypatch):
    seen = {}

    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        seen["task"] = task
        seen["images"] = images
        return _FakeResult(data={"doc_type": "OTHER", "scheme_relevance": "unknown",
                                  "confidence": 0.3, "rationale": "blank page"})

    _stub_router(monkeypatch, fake_call)
    monkeypatch.setattr(llm_classifier, "page1_image_bytes", lambda p, long_side=1600: b"fake-jpeg-bytes")

    result = llm_classifier.classify_document_llm(
        class_text="", folder_label="Court Deposit", filename="dd.pdf", page_count=1,
        page1_preview_path="data/pages/xx/xx/1.webp",
    )
    assert seen["task"] == "classify_page"
    assert seen["images"] == [b"fake-jpeg-bytes"]
    assert result["doc_type"] == "OTHER"


def test_classify_document_llm_caches_second_call(monkeypatch):
    calls = {"n": 0}

    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        calls["n"] += 1
        return _FakeResult(data={"doc_type": "CHITTA", "scheme_relevance": "allikulam",
                                  "confidence": 0.8, "rationale": "chitta extract"})

    _stub_router(monkeypatch, fake_call)

    text = "நில உரிமை விபரங்கள் வட்டாட்சியர் அலுவலக இணைய சேவை " * 5
    kwargs = {"class_text": text, "folder_label": "Patta Transferred", "filename": "p.pdf", "page_count": 1}
    r1 = llm_classifier.classify_document_llm(**kwargs)
    r2 = llm_classifier.classify_document_llm(**kwargs)

    assert calls["n"] == 1  # second call served from cache
    assert r1["doc_type"] == r2["doc_type"] == "CHITTA"
    assert r2["_meta"]["from_cache"] is True


def test_classify_document_llm_handles_router_failure_gracefully(monkeypatch):
    def fake_call(*a, **k):
        raise RuntimeError("bedrock unavailable")

    _stub_router(monkeypatch, fake_call)

    result = llm_classifier.classify_document_llm(
        class_text="some text " * 30, folder_label=None, filename="x.pdf", page_count=1,
    )
    assert result["doc_type"] is None
    assert "error" in result["_meta"]


def test_classify_document_llm_handles_not_ok_result(monkeypatch):
    def fake_call(*a, **k):
        return _FakeResult(ok=False, error="no eligible model")

    _stub_router(monkeypatch, fake_call)

    result = llm_classifier.classify_document_llm(
        class_text="some text " * 30, folder_label=None, filename="x.pdf", page_count=1,
    )
    assert result["doc_type"] is None


def test_classify_document_llm_rejects_non_bedrock_fallback_model(monkeypatch):
    # Post-incident directive (2026-09-27): this pipeline is tuned and gated against Bedrock
    # Ministral only. A Groq/local model answering must be treated the same as a failure --
    # never accepted, never cached, so a later retry (once Bedrock is back) is not blocked.
    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        return _FakeResult(model_id="groq-gpt-oss", data={
            "doc_type": "FORM_E", "scheme_relevance": "allikulam", "confidence": 0.9,
            "rationale": "looks like Form E",
        })

    _stub_router(monkeypatch, fake_call)

    result = llm_classifier.classify_document_llm(
        class_text="FORM E notice under section 4(2) " * 5, folder_label="Form E",
        filename="e.pdf", page_count=1,
    )
    assert result["doc_type"] is None
    assert result["_meta"]["fallback_model"] is True
    assert result["_meta"]["model_id"] == "groq-gpt-oss"

    # and it must not have been cached: a second call with Bedrock now "back" must call again
    calls = {"n": 0}

    def fake_call_bedrock(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        calls["n"] += 1
        return _FakeResult(model_id="bedrock-ministral-3b", data={
            "doc_type": "FORM_E", "scheme_relevance": "allikulam", "confidence": 0.9,
            "rationale": "Form E heading",
        })

    _stub_router(monkeypatch, fake_call_bedrock)
    result2 = llm_classifier.classify_document_llm(
        class_text="FORM E notice under section 4(2) " * 5, folder_label="Form E",
        filename="e.pdf", page_count=1,
    )
    assert calls["n"] == 1
    assert result2["doc_type"] == "FORM_E"


def test_preflight_bedrock_ok(monkeypatch):
    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        return _FakeResult(model_id="bedrock-ministral-3b", data={"ok": True})

    _stub_router(monkeypatch, fake_call)
    ok, model = llm_classifier.preflight_bedrock()
    assert ok is True
    assert model == "bedrock-ministral-3b"


def test_preflight_bedrock_fails_on_fallback_model(monkeypatch):
    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None):
        return _FakeResult(model_id="groq-gpt-oss", data={"ok": True})

    _stub_router(monkeypatch, fake_call)
    ok, reason = llm_classifier.preflight_bedrock()
    assert ok is False
    assert "groq-gpt-oss" in reason


def test_preflight_bedrock_detects_sso_expired(monkeypatch):
    def fake_call(*a, **k):
        return _FakeResult(ok=False, error="AWS SSO session expired: TokenRetrievalError")

    _stub_router(monkeypatch, fake_call)
    ok, reason = llm_classifier.preflight_bedrock()
    assert ok is False
    assert "sso_expired=True" in reason


def test_preflight_bedrock_handles_router_exception(monkeypatch):
    def fake_call(*a, **k):
        raise RuntimeError("network down")

    _stub_router(monkeypatch, fake_call)
    ok, reason = llm_classifier.preflight_bedrock()
    assert ok is False
    assert "network down" in reason
