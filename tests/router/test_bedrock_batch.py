"""D-042 deferred Bedrock batches: collect -> (fake Lambda) responses -> import -> cache_only."""
from __future__ import annotations

import io
import json

import pytest

from app.router.batch_import import import_batch
from app.router.embeddings import Embedder, EmbeddingCache, EmbeddingDeferred
from app.router.registry import load_registry
from tests.router.helpers import SCHEMA, make_router

BID = "b-test-001"


def _png(color=(200, 200, 200), size=48):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def collect(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")          # fake providers; they must never be called for bedrock
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "collect")
    monkeypatch.setenv("ROUTER_BATCH_ID", BID)


def _router(tmp_path, **kw):
    router, fake, clock = make_router(tmp_path, registry=load_registry(), **kw)
    return router, fake, clock


def _call(router, prompt="read the table", img=None, **kw):
    return router.call("table_read_pii", {"prompt": prompt, "max_tokens": 800}, schema=SCHEMA,
                       privacy_tier="PII", images=[img or _png()], **kw)


def _lines(router):
    f = router.batch.dir(BID) / "requests.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _write_responses(router, answers: dict[str, dict]):
    d = router.batch.dir(BID) / "responses"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "part-0000.jsonl", "w") as f:
        f.writelines(json.dumps({"key": key, **a}) + "\n" for key, a in answers.items())


def test_collect_writes_spec_line_and_defers_without_fallback(tmp_path, collect):
    router, fake, _ = _router(tmp_path)
    r = _call(router)
    assert not r.ok and r.error_kind == "deferred" and r.error == f"deferred to batch {BID}"
    assert r.model_id == "bedrock-ministral-8b"
    assert fake.calls == []                                  # no provider call, and NO fallback to groq
    [ln] = _lines(router)
    assert ln["model_id"] == "mistral.ministral-3-8b-instruct" and ln["router_model_id"] == "bedrock-ministral-8b"
    assert ln["batch_id"] == BID and ln["task"] == "table_read_pii" and ln["privacy_tier"] == "PII"
    assert ln["inferenceConfig"] == {"maxTokens": 800, "temperature": 0.0} and ln["schema"] == SCHEMA
    assert "JSON Schema" in ln["system"] and ln["created_at"] and ln["est_cost_usd"] > 0
    blocks = ln["messages"][0]["content"]
    assert blocks[0]["text"].startswith("read the table")
    img = blocks[1]["image"]
    assert img["format"] == "jpeg" and img["s3_key"].startswith("img/")
    jpg = (router.batch.dir(BID) / img["s3_key"]).read_bytes()
    import hashlib
    assert jpg[:3] == b"\xff\xd8\xff" and img["s3_key"] == f"img/{hashlib.sha256(jpg).hexdigest()}.jpg"


def test_collect_dedups_by_key(tmp_path, collect):
    router, _, _ = _router(tmp_path)
    for _ in range(3):
        _call(router)
    _call(router, prompt="another prompt")
    assert len(_lines(router)) == 2
    r2, _, _ = _router(tmp_path)                             # a new process re-reads the index
    _call(r2)
    assert len(_lines(r2)) == 2


def test_allow_fallback_on_defer(tmp_path, collect):
    router, fake, _ = _router(tmp_path)
    r = _call(router, allow_fallback_on_defer=True)
    assert r.ok and r.model_id == "groq-qwen-vl" and fake.called_models() == ["groq-qwen-vl"]
    assert r.route_log["deferred"][0]["model_id"] == "bedrock-ministral-8b" and len(_lines(router)) == 1


def test_collect_import_cache_only_roundtrip(tmp_path, collect, monkeypatch):
    router, fake, _ = _router(tmp_path)
    _call(router, img=_png((10, 10, 10)))
    _call(router, img=_png((250, 250, 250)))
    _call(router, prompt="will fail")
    l1, l2, l3 = _lines(router)
    _write_responses(router, {
        l1["key"]: {"ok": True, "output_text": '```json\n{"ok": true, "answer": 7}\n```',
                    "usage": {"inputTokens": 1500, "outputTokens": 40}, "latency_ms": 2900, "error": None},
        l2["key"]: {"ok": True, "output_text": "not json at all",
                    "usage": {"inputTokens": 1400, "outputTokens": 5}, "latency_ms": 2500, "error": None},
        l3["key"]: {"ok": False, "output_text": None, "usage": {}, "latency_ms": 10,
                    "error": "ThrottlingException"},
    })
    s = import_batch(BID, router=router)
    assert (s["imported"], s["schema_invalid"], s["errors"], s["unknown_key"]) == (2, 1, 1, 0)
    assert s["cost_usd"] == pytest.approx((1540 + 1405) * 0.15 / 1e6)
    assert router.quota.cost_since({"bedrock-ministral-8b"}, 0) == pytest.approx(s["cost_usd"])
    rows = [x for x in router.telemetry.rows(10) if x.get("mode") == "batch"]
    assert {x["outcome"] for x in rows} == {"ok", "schema_invalid", "error"}
    assert import_batch(BID, router=router)["skipped_existing"] == 2          # idempotent
    assert router.quota.cost_since({"bedrock-ministral-8b"}, 0) == pytest.approx(s["cost_usd"])

    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "cache_only")
    monkeypatch.setenv("ROUTER_BATCH_ID", "b-test-002")
    r = _call(router, img=_png((10, 10, 10)))
    assert r.ok and r.model_id == "bedrock-ministral-8b" and r.data == {"ok": True, "answer": 7}
    assert (r.tokens_in, r.tokens_out, r.latency_ms) == (1500, 40, 2900)
    assert r.route_log["attempts"][0]["outcome"] == "batch_cache_ok" and fake.calls == []
    # schema-invalid cached answer -> repair request is a cache miss -> collected into the next batch
    r = _call(router, img=_png((250, 250, 250)))
    assert r.error_kind == "deferred" and "b-test-002" in r.error
    nxt = (router.batch.dir("b-test-002") / "requests.jsonl").read_text().splitlines()
    assert len(nxt) == 1 and "not valid" in json.loads(nxt[0])["messages"][-1]["content"][-1]["text"]
    # failed line is not served -> re-collected
    r = _call(router, prompt="will fail")
    assert r.error_kind == "deferred"
    assert len((router.batch.dir("b-test-002") / "requests.jsonl").read_text().splitlines()) == 2


def test_cache_only_result_identical_to_live(tmp_path, monkeypatch):
    """The data a pipeline gets from cache_only equals what a live call with the same output would give."""
    monkeypatch.setenv("ROUTER_MODE", "live")
    text = '```json\n{"ok": true, "answer": 7}\n```'
    from app.router.types import ProviderResponse
    live_router, _, _ = _router(tmp_path / "live", script={"bedrock-ministral-8b": [ProviderResponse(
        text=text, tokens_in=1500, tokens_out=40, resolved_model="mistral.ministral-3-8b-instruct")]})
    live = _call(live_router)
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "collect")
    monkeypatch.setenv("ROUTER_BATCH_ID", BID)
    router, _, _ = _router(tmp_path / "batch")
    _call(router)
    [ln] = _lines(router)
    _write_responses(router, {ln["key"]: {"ok": True, "output_text": text,
                                          "usage": {"inputTokens": 1500, "outputTokens": 40}, "latency_ms": 5}})
    import_batch(BID, router=router)
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "cache_only")
    cached = _call(router)
    for f in ("ok", "model_id", "text", "data", "tokens_in", "tokens_out", "shadow_cost_usd"):
        assert getattr(cached, f) == getattr(live, f), f


def test_collect_time_spend_guard(tmp_path, collect, monkeypatch):
    monkeypatch.setenv("AWS_SPEND_CAP_USD", "0.001")         # 80% = $0.0008 budget
    router, _, _ = _router(tmp_path)
    assert _call(router, prompt="p1").error_kind == "deferred"          # est ~$0.0003 each (image + 800 out)
    n = 1
    while n < 20:
        r = _call(router, prompt=f"p{n + 1}")
        if r.error_kind != "deferred":
            break
        n += 1
    est = sum(ln["est_cost_usd"] for ln in _lines(router))
    assert 2 <= n < 20 and len(_lines(router)) == n and est <= 0.0008
    assert r.ok and r.model_id == "groq-qwen-vl"                        # refused -> next model in chain
    reason = next(a for a in r.route_log["attempts"] if a["model_id"] == "bedrock-ministral-8b")["error"]
    assert "quota:batch_budget" in reason
    router.quota.record("bedrock-ministral-8b", 1, 1.0)                 # spend to date counts too
    monkeypatch.setenv("AWS_SPEND_CAP_USD", "1.2")
    assert _call(router, prompt="zzz").model_id == "groq-qwen-vl"


def test_cache_only_needs_no_aws_and_skips_live_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "cache_only")
    monkeypatch.setenv("ROUTER_BATCH_ID", BID)
    router, fake, _ = _router(tmp_path, credentials=lambda p: (p != "bedrock", "AWS SSO session expired"))
    r = _call(router)
    assert r.error_kind == "deferred" and fake.calls == []


def test_batch_mode_env_validation(tmp_path, monkeypatch):
    from app.router import RouterConfigError
    monkeypatch.setenv("ROUTER_MODE", "live")
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "collect")
    monkeypatch.delenv("ROUTER_BATCH_ID", raising=False)
    router, _, _ = _router(tmp_path)
    with pytest.raises(RouterConfigError, match="ROUTER_BATCH_ID"):
        _call(router)
    monkeypatch.setenv("ROUTER_BEDROCK_MODE", "sometimes")
    with pytest.raises(RouterConfigError):
        _call(router)


# ---- embeddings ----------------------------------------------------------------------------------
def test_embeddings_collect_import_then_cached(tmp_path, collect):
    router, _, _ = _router(tmp_path)
    ecache = EmbeddingCache(tmp_path / "emb.sqlite")
    calls = []
    e = Embedder(router=router, cache=ecache, text_invoke=lambda *a: calls.append(a),
                 image_invoke=lambda *a: calls.append(a))
    with pytest.raises(EmbeddingDeferred) as ei:
        e.embed_text_detailed(["survey 233/2", "award", "survey 233/2"])
    assert ei.value.n_collected == 2 and calls == []
    with pytest.raises(EmbeddingDeferred):
        e.embed_image_detailed(_png())
    lines = _lines(router)
    assert {ln["op"] for ln in lines} == {"embed_text", "embed_image"} and len(lines) == 3
    _write_responses(router, {ln["key"]: {"ok": True, "embedding": [0.1] * 1024,
                                          "usage": {"inputTokens": 4, "outputTokens": 0}} for ln in lines})
    s = import_batch(BID, router=router, embed_cache=ecache)
    assert s["imported"] == 3
    r = e.embed_text_detailed(["award", "survey 233/2"])
    assert r.model_id == "titan-embed-text-v2" and r.cached == 2 and len(r.vectors[1]) == 1024
    assert len(e.embed_image_detailed(_png()).vectors[0]) == 1024 and calls == []
