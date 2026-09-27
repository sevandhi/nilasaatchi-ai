"""Deterministic scoring and the quota DB (windows, reserve, reconcile, demo reserve)."""
from __future__ import annotations

import pytest

from app.router.clock import FakeClock
from app.router.quota import QuotaDB
from tests.router.helpers import SCHEMA, make_router, mini_registry


@pytest.fixture(autouse=True)
def live_fake(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")


def test_scoring_is_deterministic(tmp_path):
    logs = []
    for i in range(3):
        router, _, _ = make_router(tmp_path / str(i))
        r = router.call("sql", {"prompt": "count parcels", "step_id": "s"}, schema=SCHEMA, privacy_tier="PII")
        logs.append((r.route_log["scores"], r.route_log["order"], r.model_id))
    assert logs[0] == logs[1] == logs[2]
    assert logs[0][1] == ["groq-qwen-vl", "groq-gpt-oss", "bedrock-ministral-8b", "local-qwen"]


def test_score_formula_components(tmp_path):
    router, _, _ = make_router(tmp_path, registry=mini_registry())
    r = router.call("t", {"prompt": "x"}, privacy_tier="PUBLIC")
    s = r.route_log["scores"]["a"]
    c = s["components"]
    expected = 0.35 * c["capability"] + 0.25 * c["confidence"] - 0.15 * c["latency"] \
        - 0.10 * c["shadow_cost"] - 0.15 * c["quota_pressure"]
    assert s["total"] == pytest.approx(round(expected, 6))
    assert c["latency"] == 0.1 and c["capability"] == 1.0


def test_tie_broken_by_chain_order(tmp_path):
    router, _, _ = make_router(tmp_path, registry=mini_registry())
    r = router.call("tie", {"prompt": "x"})
    assert r.route_log["scores"]["a"]["total"] == r.route_log["scores"]["b"]["total"]
    assert r.route_log["order"] == ["b", "a"]


def test_quota_pressure_reorders(tmp_path):
    reg = mini_registry()
    reg.models["a"].limits = {"rpd": 100}
    router, _, _ = make_router(tmp_path, registry=reg)
    for _ in range(60):                      # 60% of a's daily budget used
        router.quota.reconcile(router.quota.reserve("a", 10), 10)
    r = router.call("t", {"prompt": "x"})
    assert r.route_log["scores"]["a"]["components"]["quota_pressure"] == pytest.approx(0.6)
    assert r.route_log["order"][0] == "b"


def test_measured_p50_used_after_min_samples(tmp_path):
    router, _, _ = make_router(tmp_path, registry=mini_registry(), latency_s={"a": 4.0})
    for _ in range(3):
        router.call("t", {"prompt": "x"})
    r = router.call("t", {"prompt": "x"})
    sa = r.route_log["scores"]["a"]
    assert sa["p50_source"] == "measured" and sa["p50_ms"] == pytest.approx(4000, rel=0.01)
    assert r.route_log["order"][0] == "b"    # slow measured latency now outweighs capability gap


# ---- quota DB -------------------------------------------------------------------------------------
def test_quota_windows_roll_with_clock(tmp_path):
    clock = FakeClock()
    q = QuotaDB(tmp_path / "q.sqlite", clock)
    for _ in range(3):
        q.reconcile(q.reserve("m", 100), 120)
    assert q.used("m", "rpm") == 3 and q.used("m", "tpm") == 360
    assert not q.check("m", {"rpm": 3}, 10).ok
    clock.advance(61)
    assert q.used("m", "rpm") == 0 and q.used("m", "rpd") == 3
    assert q.check("m", {"rpm": 3}, 10).ok


def test_daily_reserve_and_demo_override(tmp_path, monkeypatch):
    q = QuotaDB(tmp_path / "q.sqlite", FakeClock(), reserve_fraction=0.10)
    for _ in range(9):
        q.reconcile(q.reserve("m", 1), 1)
    chk = q.check("m", {"rpd": 10}, 1)
    assert not chk.ok and "quota:rpd" in chk.reason         # 10th request eats the 10% demo reserve
    monkeypatch.setenv("ROUTER_USE_DEMO_RESERVE", "1")
    assert q.check("m", {"rpd": 10}, 1).ok


def test_reserve_uses_estimate_until_reconciled(tmp_path):
    q = QuotaDB(tmp_path / "q.sqlite", FakeClock())
    rid = q.reserve("m", 5000, est_out=500)
    assert q.used("m", "tpd") == 5000 and q.used("m", "otpm") == 500
    assert not q.check("m", {"tpm": 8000}, 4000).ok
    q.reconcile(rid, 300, tokens_out=40)
    assert q.used("m", "tpd") == 300 and q.used("m", "otpm") == 40


def test_rate_limited_request_not_counted(tmp_path):
    q = QuotaDB(tmp_path / "q.sqlite", FakeClock())
    q.reconcile(q.reserve("m", 10), 0, status="rejected")
    q.reconcile(q.reserve("m", 10), 0, status="failed")
    assert q.used("m", "rpd") == 1


def test_cap_usd_and_credit(tmp_path):
    q = QuotaDB(tmp_path / "q.sqlite", FakeClock())
    q.reconcile(q.reserve("bedrock", 10), 10, cost_usd=5.0)
    assert not q.check("bedrock", {}, 10, cap_usd=5).ok
    q.reconcile(q.reserve("ocr", 0), 0, cost_usd=10.0)
    assert not q.check("ocr", {"monthly_credit_usd": 10}, 0).ok


def test_router_filters_on_quota(tmp_path):
    reg = mini_registry()
    reg.models["a"].limits = {"rpm": 2}
    router, _fake, _ = make_router(tmp_path, registry=reg)
    assert router.call("t", {"prompt": "x"}).model_id == "a"
    router.quota.reconcile(router.quota.reserve("a", 10), 10)      # 2/2 per minute used
    r = router.call("t", {"prompt": "x"})
    assert r.model_id == "b"
    assert any(f["id"] == "a" and "quota:rpm" in f["reason"] for f in r.route_log["filtered"])
    router.clock.advance(61)
    assert router.call("t", {"prompt": "x"}).model_id == "a"


def test_otpm_clamps_max_tokens(tmp_path):
    router, fake, _ = make_router(tmp_path)
    router.call("sql", {"prompt": "x", "max_tokens": 4000}, privacy_tier="PII")
    assert fake.calls[0].model_id == "groq-qwen-vl" and fake.calls[0].max_tokens == 500
