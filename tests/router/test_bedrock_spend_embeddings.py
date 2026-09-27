"""Bedrock Converse adapter, SSO expiry, AWS spend guard (D-028), Titan embeddings, approved-model list."""
from __future__ import annotations

import io
import json

import pytest

from app.router.embeddings import Embedder, EmbeddingCache, EmbeddingUnavailable
from app.router.providers import bedrock
from app.router.registry import APPROVED_BEDROCK_MODELS, load_registry, parse_registry
from app.router.spend import SpendGuard
from app.router.types import ProviderError, ProviderRequest, RouterConfigError
from tests.router.helpers import SCHEMA, make_router


def _png(size=40):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (200, 200, 200)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def live_fake(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")


# ---- Converse adapter ----------------------------------------------------------------------------
class FakeBedrockClient:
    def __init__(self, text='```json\n{"ok": true, "answer": 7}\n```', exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "usage": {"inputTokens": 1512, "outputTokens": 88}, "metrics": {"latencyMs": 2900}}


def _client_error(code, status=400):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
                       "Converse")


def test_to_converse_blocks():
    from app.router.messages import build_messages
    msgs = build_messages({"system": "sys", "prompt": "read the table"}, SCHEMA, [_png(), b"%PDF-1.4 x"])
    system, conv = bedrock.to_converse(msgs)
    assert "sys" in system[0]["text"] and "JSON Schema" in system[0]["text"]
    blocks = conv[0]["content"]
    assert blocks[0]["text"].startswith("read the table")
    assert blocks[1]["image"]["format"] == "png" and blocks[1]["image"]["source"]["bytes"][:4] == b"\x89PNG"
    assert blocks[2]["document"]["format"] == "pdf"
    _, conv = bedrock.to_converse([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    assert len(conv) == 1 and len(conv[0]["content"]) == 2        # alternating roles enforced


def test_converse_usage_and_fenced_json_through_router(tmp_path, live_fake, monkeypatch):
    fake = FakeBedrockClient()
    monkeypatch.setattr(bedrock, "_client", lambda *a, **k: fake)
    router, _, _ = make_router(tmp_path, registry=load_registry())
    router._providers["bedrock"] = bedrock.BedrockConverseProvider()
    r = router.call("table_read_pii", {"prompt": "read"}, schema=SCHEMA, privacy_tier="PII", images=[_png()])
    assert r.ok and r.model_id == "bedrock-ministral-8b" and r.data == {"ok": True, "answer": 7}
    assert (r.tokens_in, r.tokens_out) == (1512, 88)
    assert r.shadow_cost_usd == pytest.approx((1512 + 88) * 0.15 / 1e6)
    assert fake.calls[0]["modelId"] == "mistral.ministral-3-8b-instruct"
    assert router.quota.cost_since({"bedrock-ministral-8b"}, 0) == pytest.approx(r.shadow_cost_usd)


@pytest.mark.parametrize("code,kind", [("ThrottlingException", "rate_limit"), ("ServiceUnavailableException", "server"),
                                       ("ModelTimeoutException", "timeout"), ("AccessDeniedException", "auth"),
                                       ("ValidationException", "bad_request"), ("ExpiredTokenException", "auth_expired")])
def test_bedrock_error_mapping(code, kind):
    assert bedrock.map_exception(_client_error(code)).kind == kind


def test_sso_expiry_mapping():
    from botocore.exceptions import SSOTokenLoadError, UnauthorizedSSOTokenError
    for e in (SSOTokenLoadError(error_msg="x"), UnauthorizedSSOTokenError()):
        err = bedrock.map_exception(e)
        assert err.kind == "auth_expired" and "make aws-login" in str(err)


def test_sso_expired_parks_provider_and_chain_continues(tmp_path, live_fake):
    expired = ProviderError("auth_expired", bedrock.SSO_EXPIRED_MSG)
    router, fake, clock = make_router(tmp_path, registry=load_registry(), script={"bedrock-ministral-8b": [expired]})
    r = router.call("cell_reread", {"prompt": "digit?"}, privacy_tier="PII", images=[_png()])
    assert r.ok and r.model_id == "groq-qwen-vl"
    assert fake.called_models() == ["bedrock-ministral-8b", "groq-qwen-vl"]     # no retry on auth_expired
    r = router.call("cell_reread", {"prompt": "digit?"}, privacy_tier="PII", images=[_png()])
    reason = {f["id"]: f["reason"] for f in r.route_log["filtered"]}["bedrock-ministral-8b"]
    assert "make aws-login" in reason
    clock.advance(301)
    r = router.call("cell_reread", {"prompt": "digit?"}, privacy_tier="PII", images=[_png()])
    assert r.model_id == "bedrock-ministral-8b"


# ---- spend guard ---------------------------------------------------------------------------------
def test_spend_guard_blocks_capped_at_80pct(tmp_path, live_fake):
    router, _, _ = make_router(tmp_path, registry=load_registry())
    router.quota.record("bedrock-ministral-8b", 1000, 11.99)
    r = router.call("classify_text", {"prompt": "x"}, privacy_tier="PII")
    assert r.model_id == "bedrock-ministral-3b"                          # $11.99 < $12
    router.quota.record("titan-embed-text-v2", 1000, 0.02)                # embeddings count too
    r = router.call("classify_text", {"prompt": "x"}, privacy_tier="PII")
    assert r.model_id == "groq-gpt-oss"
    reason = {f["id"]: f["reason"] for f in r.route_log["filtered"]}["bedrock-ministral-3b"]
    assert "quota:aws_spend" in reason and "80%" in reason
    r = router.call("sql", {"prompt": "x"}, privacy_tier="PII")          # non-AWS models unaffected
    assert r.model_id == "groq-qwen-vl"


def test_spend_guard_cost_explorer_refresh_hourly(tmp_path):
    from app.router.clock import FakeClock
    from app.router.quota import QuotaDB
    clock = FakeClock()
    q = QuotaDB(tmp_path / "q.sqlite", clock)
    calls = []

    def ce():
        calls.append(1)
        return 5.0

    g = SpendGuard(q, clock, tmp_path / "s.json", aws_ids={"bedrock-x"}, ce_fetch=ce, cap_usd=15)
    assert g.status().spend_usd == 5.0 and g.status().source == "cost_explorer+local"
    clock.advance(1800)
    q.record("bedrock-x", 10, 2.0)
    assert g.status().spend_usd == 7.0 and len(calls) == 1               # cached; local spend added
    clock.advance(1801)
    g.status()
    assert len(calls) == 2                                               # refreshed after an hour
    assert g.status().spend_usd == 7.0                                   # CE lag window keeps local $2


def test_spend_guard_ce_failure_falls_back_to_local(tmp_path):
    from app.router.clock import FakeClock
    from app.router.quota import QuotaDB
    clock = FakeClock()
    q = QuotaDB(tmp_path / "q.sqlite", clock)
    calls = []

    def ce():
        calls.append(1)
        raise RuntimeError("Token has expired and refresh failed")

    g = SpendGuard(q, clock, tmp_path / "s.json", aws_ids={"b"}, ce_fetch=ce, cap_usd=10)
    q.record("b", 1, 8.5)
    st = g.status()
    assert not st.ok and st.source == "local" and g.last_error
    g.status()
    assert len(calls) == 1                                               # failed fetch not retried every call


# ---- embeddings ----------------------------------------------------------------------------------
def _embedder(tmp_path, router, text_calls, image_calls=None, fail=None):
    def text_invoke(model, text, dims, normalize, region):
        if fail:
            raise fail
        text_calls.append(text)
        return [float(len(text))] * dims, len(text.split())

    def image_invoke(model, png, dims, region):
        (image_calls if image_calls is not None else []).append(len(png))
        return [0.5] * dims

    return Embedder(router=router, cache=EmbeddingCache(tmp_path / "emb.sqlite"), text_invoke=text_invoke,
                    image_invoke=image_invoke, batch_size=2)


def test_embed_text_batches_caches_and_records_spend(tmp_path):
    router, _, _ = make_router(tmp_path)
    calls = []
    e = _embedder(tmp_path, router, calls)
    r = e.embed_text_detailed(["a b", "c", "d e f", "a b", "g"])
    assert r.model_id == "titan-embed-text-v2" and len(r.vectors) == 5 and len(r.vectors[0]) == 1024
    assert sorted(calls) == ["a b", "c", "d e f", "g"]                    # dedup by content hash
    assert r.vectors[0] == r.vectors[3] and r.tokens_in == 7
    r2 = e.embed_text_detailed(["g", "a b"])
    assert r2.cached == 2 and len(calls) == 4                            # cache hit, no new invoke
    assert router.quota.cost_since({"titan-embed-text-v2"}, 0) == pytest.approx(7 * 0.02 / 1e6)


def test_embed_blocked_by_spend_guard_uses_local_stub(tmp_path):
    router, _, _ = make_router(tmp_path)
    router.quota.record("bedrock-ministral-8b", 1, 12.5)
    calls = []
    e = _embedder(tmp_path, router, calls)
    with pytest.raises(EmbeddingUnavailable, match="aws_spend.*BGE-M3"):
        e.embed_text_detailed(["x"])
    assert calls == []
    e.local_embed = lambda texts: [[0.0] * 1024 for _ in texts]
    assert e.embed_text_detailed(["x"]).model_id == "bge-m3"


def test_embed_sso_expired(tmp_path):
    router, _, _ = make_router(tmp_path)
    e = _embedder(tmp_path, router, [], fail=ProviderError("auth_expired", bedrock.SSO_EXPIRED_MSG))
    with pytest.raises(EmbeddingUnavailable, match="make aws-login"):
        e.embed_text_detailed(["x"])
    assert "make aws-login" in router._credentials("bedrock")[1]


def test_embed_image_cached(tmp_path):
    router, _, _ = make_router(tmp_path)
    img_calls = []
    e = _embedder(tmp_path, router, [], img_calls)
    v1 = e.embed_image_detailed(_png())
    v2 = e.embed_image_detailed(_png())
    assert len(v1.vectors[0]) == 1024 and v2.cached == 1 and len(img_calls) == 1


# ---- approved Bedrock models (D-027) -------------------------------------------------------------
def test_production_bedrock_models_are_approved():
    reg = load_registry()
    used = {m.model for m in reg.models.values() if m.provider == "bedrock"}
    used |= {e.model for e in reg.embeddings.values() if e.provider == "bedrock"}
    assert used and used <= APPROVED_BEDROCK_MODELS
    assert not any("14b" in m.lower() for m in used)


@pytest.mark.parametrize("section", ["models", "embeddings"])
def test_unapproved_bedrock_model_rejected(section):
    entry = {"id": "x", "provider": "bedrock", "model": "mistral.ministral-3-14b-instruct", "vendor": "v",
             "cost_class": "capped_fallback", "trains_on_free_tier": False}
    with pytest.raises(RouterConfigError, match="not on the approved list"):
        parse_registry({"models": [entry] if section == "models" else [], "tasks": {},
                        section: [entry]} if section == "embeddings" else {"models": [entry], "tasks": {}})


def test_provider_request_region_passthrough(monkeypatch):
    seen = {}

    def fake_client(service, region, timeout):
        seen.update(service=service, region=region)
        return FakeBedrockClient(text=json.dumps({"ok": True}))

    monkeypatch.setattr(bedrock, "_client", fake_client)
    req = ProviderRequest(model_id="m", provider="bedrock", model_name="mistral.ministral-3-3b-instruct",
                          messages=[{"role": "user", "content": "hi"}], extra={"region": "ap-south-1"})
    r = bedrock.BedrockConverseProvider().complete(req)
    assert seen == {"service": "bedrock-runtime", "region": "ap-south-1"} and r.tokens_in == 1512
