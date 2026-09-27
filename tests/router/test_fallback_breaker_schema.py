"""Fallback on 429/5xx, circuit breaker open/half-open, schema repair -> alternate model, chaos."""
from __future__ import annotations

import pytest

from app.router import chaos
from app.router.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from app.router.clock import FakeClock
from app.router.jsonutil import extract_json
from tests.router.helpers import OK_JSON, SCHEMA, err429, err503, make_router, mini_registry


@pytest.fixture(autouse=True)
def live_fake(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")


def test_fallback_on_429_retries_once_then_next(tmp_path):
    router, fake, clock = make_router(tmp_path, registry=mini_registry(), script={"a": [err429(), err429()]})
    t0 = clock.now()
    r = router.call("t", {"prompt": "x"}, schema=SCHEMA)
    assert r.ok and r.model_id == "b" and r.data == {"ok": True, "answer": 7}
    assert fake.called_models() == ["a", "a", "b"]
    outcomes = [(a["model_id"], a["outcome"]) for a in r.route_log["attempts"]]
    assert outcomes == [("a", "call_error"), ("a", "call_error"), ("b", "ok")]
    assert 0.5 <= clock.now() - t0 - 0.3 <= 1.0        # one deterministic jitter sleep + 3 fake calls
    assert router.quota.used("a", "rpd") == 0            # 429s are not counted against quota


def test_retry_succeeds_on_same_model(tmp_path):
    router, fake, _ = make_router(tmp_path, registry=mini_registry(), script={"a": [err503()]})
    r = router.call("t", {"prompt": "x"})
    assert r.model_id == "a" and fake.called_models() == ["a", "a"]


def test_non_transient_error_no_retry(tmp_path):
    from app.router.types import ProviderError
    router, fake, _ = make_router(tmp_path, registry=mini_registry(),
                                  script={"a": [ProviderError("bad_request", "nope", 400)]})
    r = router.call("t", {"prompt": "x"})
    assert r.model_id == "b" and fake.called_models() == ["a", "b"]
    assert router.breaker.state("a") == CLOSED


def test_not_found_uses_registry_fallback_model(tmp_path):
    from app.router.types import ProviderError
    router, fake, _ = make_router(tmp_path, script={"gemini-flash-lite": [ProviderError("not_found", "404", 404)]})
    r = router.call("present", {"prompt": "x"}, privacy_tier="PSEUDO")
    assert r.model_id == "gemini-flash-lite"
    assert [c.model_name for c in fake.calls] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    router.call("present", {"prompt": "y"}, privacy_tier="PSEUDO")
    assert fake.calls[-1].model_name == "gemini-3.1-flash-lite"   # dead name remembered


def test_all_fail_returns_terminal(tmp_path):
    router, _, _ = make_router(tmp_path, registry=mini_registry(),
                               script={m: [err503()] * 2 for m in "abc"})
    r = router.call("t", {"prompt": "x"})
    assert not r.ok and r.route_log["terminal"] == "review_queue" and "all candidates failed" in r.error


def test_no_eligible_model(tmp_path):
    router, fake, _ = make_router(tmp_path)
    r = router.call("judge", {"prompt": "x"}, privacy_tier="PII")
    assert not r.ok and "no eligible model" in r.error and fake.calls == []
    assert r.route_log["terminal"] == "deterministic_only_downgrade"


# ---- breaker -----------------------------------------------------------------------------------
def test_breaker_unit_open_half_open_close():
    clock = FakeClock()
    b = CircuitBreaker(clock, failures=3, open_seconds=60)
    for _ in range(3):
        assert b.acquire("m")
        b.record_failure("m")
    assert b.state("m") == OPEN and not b.acquire("m")
    clock.advance(59)
    assert b.state("m") == OPEN
    clock.advance(2)
    assert b.state("m") == HALF_OPEN
    assert b.acquire("m")                  # the single probe
    assert not b.acquire("m")              # second concurrent probe refused
    b.record_failure("m")
    assert b.state("m") == OPEN            # failed probe re-opens
    clock.advance(61)
    assert b.acquire("m")
    b.record_success("m")
    assert b.state("m") == CLOSED


def test_breaker_counts_consecutive_only():
    b = CircuitBreaker(FakeClock(), failures=3)
    b.record_failure("m")
    b.record_failure("m")
    b.record_success("m")
    b.record_failure("m")
    b.record_failure("m")
    assert b.state("m") == CLOSED


def test_router_breaker_opens_and_half_open_probe(tmp_path):
    router, _fake, clock = make_router(tmp_path, registry=mini_registry(),
                                      script={"a": [err503(), err503(), err503()]})
    router.call("t", {"prompt": "1"})            # a fails twice (retry) -> b
    router.call("t", {"prompt": "2"})            # a fails once more -> breaker opens -> b
    assert router.breaker.state("a") == OPEN
    r = router.call("t", {"prompt": "3"})
    assert any(f["id"] == "a" and "breaker:open" in f["reason"] for f in r.route_log["filtered"])
    assert r.model_id == "b"
    clock.advance(61)
    r = router.call("t", {"prompt": "4"})        # half-open probe succeeds (script exhausted -> OK)
    assert r.model_id == "a" and router.breaker.state("a") == CLOSED


# ---- schema ------------------------------------------------------------------------------------
def test_schema_repair_same_model(tmp_path):
    router, fake, _ = make_router(tmp_path, registry=mini_registry(), script={"a": ['{"ok": "yes"}', OK_JSON]})
    r = router.call("t", {"prompt": "x"}, schema=SCHEMA)
    assert r.ok and r.model_id == "a" and r.data["answer"] == 7
    assert [a["outcome"] for a in r.route_log["attempts"]] == ["ok", "schema_invalid", "repair_ok"]
    assert "not valid" in fake.calls[1].messages[-1]["content"]
    assert r.tokens_in == 200                     # both calls accounted


def test_schema_repair_fails_then_alternate_model(tmp_path):
    router, fake, _ = make_router(tmp_path, registry=mini_registry(), script={"a": ["not json", "still not"]})
    r = router.call("t", {"prompt": "x"}, schema=SCHEMA)
    assert r.ok and r.model_id == "b"
    assert fake.called_models() == ["a", "a", "b"]
    assert [a["outcome"] for a in r.route_log["attempts"]] == [
        "ok", "schema_invalid", "repair_ok", "schema_invalid_after_repair", "ok"]


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('<think>hmm {"no": 0}</think> Sure: {"a": 2} done') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("nothing here")


# ---- chaos -------------------------------------------------------------------------------------
def test_chaos_down_forces_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_CHAOS", "gemini:down")
    router, fake, _ = make_router(tmp_path)
    r = router.call("plan", {"prompt": "x"}, schema=SCHEMA, privacy_tier="PSEUDO")
    assert r.ok and r.model_id == "groq-qwen-vl"
    assert "gemini-flash-lite" not in fake.called_models()       # chaos fires before the provider


def test_chaos_badjson_repair_then_alternate(tmp_path):
    chaos.set_chaos("a:badjson")
    router, _, _ = make_router(tmp_path, registry=mini_registry())
    r = router.call("t", {"prompt": "x"}, schema=SCHEMA)
    assert r.model_id == "b"


def test_chaos_slow_adds_latency(tmp_path):
    chaos.set_chaos("a:slow")
    router, _, _ = make_router(tmp_path, registry=mini_registry())
    r = router.call("t", {"prompt": "x"})
    assert r.model_id == "a" and r.latency_ms >= 8000


def test_chaos_429_by_model_id(tmp_path):
    chaos.set_chaos("groq-qwen-vl:429")
    router, _, _ = make_router(tmp_path)
    r = router.call("sql", {"prompt": "x"}, privacy_tier="PII")
    assert r.model_id == "groq-gpt-oss"
