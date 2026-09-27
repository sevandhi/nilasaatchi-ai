"""D-036: Cohere v2 image delivery, strict_order, pinned calls, Gemini 3.1 for satellite tasks."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest

from app.router.messages import build_messages
from app.router.providers.cohere_v2 import CohereV2Provider, build_body
from app.router.registry import load_registry
from app.router.types import ProviderError, ProviderRequest
from tests.router.helpers import SCHEMA, make_router, mini_registry


def _png(color=(20, 60, 220), size=64):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def _req(images, schema=SCHEMA, json_mode="json_object", detail=None):
    msgs = build_messages({"system": "sys", "prompt": "dominant colour?"}, schema, images)
    return ProviderRequest(model_id="cohere-command-a-vision", provider="cohere",
                           model_name="command-a-vision-07-2025", messages=msgs, schema=schema,
                           json_mode=json_mode, images=images, extra={"image_detail": detail})


# ---- Cohere v2 request / response ----------------------------------------------------------------
def test_cohere_body_contains_image_part():
    png = _png()
    body = build_body(_req([png], detail="high"), "json_object")
    user = next(m for m in body["messages"] if m["role"] == "user")
    img_parts = [p for p in user["content"] if p["type"] == "image_url"]
    assert len(img_parts) == 1
    url = img_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,") and base64.b64decode(url.split(",", 1)[1]) == png
    assert img_parts[0]["image_url"]["detail"] == "high"
    assert body["response_format"] == {"type": "json_object"} and body["model"] == "command-a-vision-07-2025"
    assert "detail" not in build_body(_req([png]), "prompt")["messages"][1]["content"][1]["image_url"]
    assert "response_format" not in build_body(_req([png]), "prompt")


def test_cohere_rejects_pdf_parts():
    with pytest.raises(ProviderError, match="unsupported"):
        build_body(_req([b"%PDF-1.4 x"]), "prompt")


def _mock(handler):
    return CohereV2Provider(transport=httpx.MockTransport(handler))


def test_cohere_tokens_include_image_tokens_and_headers(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-cohere-key-123")
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"x-endpoint-monthly-call-limit": "1000"}, json={
            "message": {"content": [{"type": "text", "text": '{"ok": true, "answer": 7}'}]},
            "usage": {"tokens": {"input_tokens": 510, "output_tokens": 9, "image_tokens": 259}}})

    r = _mock(handler).complete(_req([_png()]))
    assert (r.tokens_in, r.tokens_out, r.image_tokens) == (769, 9, 259)
    assert r.headers == {"x-endpoint-monthly-call-limit": "1000"} and r.json_mode_used == "json_object"
    assert any(p["type"] == "image_url" for p in seen["body"]["messages"][1]["content"])
    assert seen["auth"] == "Bearer test-cohere-key-123"


def test_cohere_json_mode_ladder_and_errors(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "k-000000")
    modes = []

    def handler(request):
        b = json.loads(request.content)
        modes.append(b.get("response_format"))
        if "response_format" in b and "json_schema" in b["response_format"]:
            return httpx.Response(400, json={"message": "invalid json_schema"})
        return httpx.Response(200, json={"message": {"content": [{"type": "text", "text": "{}"}]},
                                         "usage": {"tokens": {"input_tokens": 5, "output_tokens": 1}}})

    r = _mock(handler).complete(_req([_png()], json_mode="json_schema"))
    assert r.json_mode_used == "json_object" and modes[0]["json_schema"] == SCHEMA
    for status, kind in [(429, "rate_limit"), (503, "server"), (401, "auth"), (404, "not_found")]:
        with pytest.raises(ProviderError) as ei:
            _mock(lambda req, s=status: httpx.Response(s, json={"message": "x"})).complete(_req([_png()]))
        assert ei.value.kind == kind


def test_cohere_image_tokens_surface_in_route_log(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    monkeypatch.setenv("COHERE_API_KEY", "k-000000")
    router, _, _ = make_router(tmp_path, registry=load_registry())
    router._providers["cohere"] = _mock(lambda req: httpx.Response(200, json={
        "message": {"content": [{"type": "text", "text": '{"ok": true, "answer": 7}'}]},
        "usage": {"tokens": {"input_tokens": 600, "output_tokens": 9, "image_tokens": 1287}}}))
    r = router.call("satellite_second_opinion", {"prompt": "x"}, schema=SCHEMA, images=[_png()])
    assert r.model_id == "cohere-command-a-vision" and r.tokens_in == 1887
    assert r.route_log["image_tokens"] == 1287 and r.route_log["attempts"][0]["image_tokens"] == 1287


# ---- strict_order ----------------------------------------------------------------------------------
def test_strict_order_ignores_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    reg = mini_registry()
    reg.tasks["t"].chain[0].capability = 0.1         # "a" would lose on score...
    router, _, _ = make_router(tmp_path, registry=reg)
    assert router.call("t", {"prompt": "x"}).model_id == "b"
    reg.tasks["t"].strict_order = True               # ...but strict order tries it first
    r = router.call("t", {"prompt": "x"})
    assert r.model_id == "a" and r.route_log["strict_order"] is True
    assert r.route_log["scores"]["a"]["total"] < r.route_log["scores"]["b"]["total"]


def test_satellite_chains_strict_order_even_under_quota_pressure(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    router, _, clock = make_router(tmp_path, registry=load_registry())
    for _ in range(700):                             # heavy Cohere usage -> would reorder if scored
        router.quota.record("cohere-command-a-vision", 10, 0.0)
    clock.advance(61)                                # clear the rpm window; monthly pressure stays 0.7
    r = router.call("satellite_second_opinion", {"prompt": "x"}, images=[_png()])
    assert r.route_log["order"] == ["cohere-command-a-vision", "groq-qwen-vl", "gemini-flash-lite-31"]
    sc = r.route_log["scores"]
    assert sc["cohere-command-a-vision"]["total"] < sc["groq-qwen-vl"]["total"]     # scores disagree
    assert r.model_id == "cohere-command-a-vision"


# ---- production config (D-036) ---------------------------------------------------------------------
def test_production_satellite_chains():
    reg = load_registry()
    t, s = reg.tasks["satellite_teacher"], reg.tasks["satellite_second_opinion"]
    assert t.strict_order and s.strict_order
    assert t.chain[0].model == "gemini-flash-lite-31"
    assert [e.model for e in s.chain] == ["cohere-command-a-vision", "groq-qwen-vl", "gemini-flash-lite-31"]
    assert not any(reg.model(e.model).provider == "bedrock" for e in s.chain)
    g = reg.model("gemini-flash-lite-31")
    assert g.model == "gemini-3.1-flash-lite" and g.fallback_model is None and g.image_timeout_s == 120


def test_gemini31_not_forced_to_35_by_doctor(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    router, fake, _ = make_router(tmp_path, registry=load_registry())
    router.doctor = {"gemini-flash-lite": {"status": "OK", "resolved_model_name": "gemini-3.5-flash-lite"},
                     "gemini-flash-lite-31": {"status": "OK", "resolved_model_name": "gemini-3.5-flash-lite"}}
    r = router.call("satellite_teacher", {"prompt": "x"}, images=[_png()])
    assert r.model_id == "gemini-flash-lite-31"
    assert fake.calls[0].model_name == "gemini-3.1-flash-lite" and fake.calls[0].timeout_s == 120


# ---- pinning ---------------------------------------------------------------------------------------
def test_pinned_call_keeps_task_settings_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    router, fake, _ = make_router(tmp_path, registry=load_registry())
    r = router.call("satellite_second_opinion", {"prompt": "x"}, images=[_png()], only_model="groq-qwen-vl")
    assert r.model_id == "groq-qwen-vl" and fake.called_models() == ["groq-qwen-vl"]
    assert r.route_log["pinned"] == "groq-qwen-vl" and r.route_log["task"] == "satellite_second_opinion"
    r = router.call("judge", {"prompt": "x"}, privacy_tier="PII", only_model="gemini-flash-lite")
    assert not r.ok and "privacy:PII" in r.error                    # pin never bypasses privacy
    from app.router import RouterConfigError
    with pytest.raises(RouterConfigError):
        router.call("critic", {"prompt": "x"}, only_model="groq-qwen-vl")        # D-017 still applies
    with pytest.raises(RouterConfigError):
        router.call("present", {"prompt": "x"}, only_model="no-such-model")


def test_public_call_accepts_only_model(monkeypatch):
    from app.router import core
    captured = {}

    class R:
        def call(self, task, payload, **kw):
            captured.update(kw, task=task)
    monkeypatch.setattr(core, "get_router", lambda: R())
    from app.router import call
    call("satellite_teacher", {}, only_model="cohere-command-a-vision")
    assert captured["only_model"] == "cohere-command-a-vision"
