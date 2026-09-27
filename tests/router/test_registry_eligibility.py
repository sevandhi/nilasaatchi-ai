"""Registry resolution + hard eligibility filters (privacy fail-closed, modality, vendor, context...)."""
from __future__ import annotations

import pytest

from app.router import PrivacyViolation, RouterConfigError, gateway
from app.router.registry import load_registry, parse_registry
from tests.router.helpers import SCHEMA, load_test_registry, make_router

REQUIRED_TASKS = {"plan", "sql", "tool_args", "json_repair", "classify_text", "table_read_pii", "cell_reread",
                  "critic", "judge", "present", "satellite_teacher", "satellite_second_opinion"}


@pytest.fixture
def live_fake(monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")    # fake providers only; no network


@pytest.mark.parametrize("which", ["production", "test"])
def test_registry_loads_all_tasks_and_honest_flags(which):
    reg = load_registry() if which == "production" else load_test_registry()
    assert REQUIRED_TASKS <= set(reg.tasks)
    assert {m.cost_class for m in reg.models.values()} <= {"free", "local", "capped_fallback"}
    # proprietary free tiers are training-tier; unknown => True
    assert reg.model("gemini-flash-lite").trains_on_free_tier() is True
    assert reg.model("cohere-command-a").trains_on_free_tier() is True
    assert reg.model("groq-qwen-vl").trains_on_free_tier() is False


@pytest.mark.parametrize("env,expected", [({}, True), ({"MISTRAL_TRAINING_OPTOUT_CONFIRMED": "false"}, True),
                                          ({"MISTRAL_TRAINING_OPTOUT_CONFIRMED": "yes"}, True),
                                          ({"MISTRAL_TRAINING_OPTOUT_CONFIRMED": "TRUE"}, False)])
def test_mistral_optout_resolution(env, expected):
    assert load_test_registry().model("mistral-ocr").trains_on_free_tier(env) is expected


@pytest.mark.parametrize("raw", ["unknown", None, "maybe", "false", 0, "no"])
def test_unknown_training_policy_fails_closed(raw):
    reg = parse_registry({"models": [{"id": "x", "provider": "groq", "model": "x", "vendor": "v",
                                      "trains_on_free_tier": raw, "cost_class": "free"}], "tasks": {}})
    assert reg.model("x").trains_on_free_tier() is True


def test_non_free_cost_class_rejected():
    with pytest.raises(ValueError):
        parse_registry({"models": [{"id": "x", "provider": "groq", "model": "x", "vendor": "v",
                                    "cost_class": "paid"}], "tasks": {}})


def test_production_config_has_no_training_tier_model_in_pii_chains():
    """No chain of a PII-typical task may list a model whose training flag is true/unknown/conditional."""
    reg = load_registry()
    bad = []
    for name, t in reg.tasks.items():
        if t.typical_tier != "PII":
            continue
        for e in t.chain:
            m = reg.model(e.model)
            if m.trains_on_free_tier_raw is not False or m.trains_on_free_tier({}) is not False:
                bad.append((name, m.id, m.trains_on_free_tier_raw))
    assert bad == []


def test_production_config_mistral_disabled():
    """D-018: Mistral stays out of the production registry (re-enabling needs user approval)."""
    reg = load_registry()
    assert not [m for m in reg.models.values() if m.provider in ("mistral", "mistral_ocr")]


@pytest.mark.parametrize("which", ["production", "test"])
def test_pii_never_reaches_training_tier_any_task(tmp_path, live_fake, which):
    router, fake, _ = make_router(tmp_path, registry=load_registry() if which == "production" else None)
    reg = router.registry
    for task in reg.tasks:
        res = router.call(task, {"prompt": "x"}, privacy_tier="PII", producer_vendor="nobody",
                          images=[b"\x89PNG\r\n\x1a\n" + b"0" * 32] if "image" in reg.tasks[task].requires else None)
        for f in res.route_log["filtered"]:
            if reg.model(f["id"]).trains_on_free_tier():
                assert "privacy:PII" in f["reason"]
        for mid in res.route_log["order"]:
            assert reg.model(mid).trains_on_free_tier() is False, (task, mid)
    assert all(not reg.model(c.model_id).trains_on_free_tier() for c in fake.calls)


def test_plan_with_pii_skips_gemini(tmp_path, live_fake):
    router, _fake, _ = make_router(tmp_path)
    res = router.call("plan", {"prompt": "plan"}, schema=SCHEMA, privacy_tier="PII")
    assert res.ok and res.model_id == "groq-qwen-vl"
    reasons = {f["id"]: f["reason"] for f in res.route_log["filtered"]}
    assert "privacy:PII" in reasons["gemini-flash-lite"]


def test_mistral_ocr_only_with_confirmed_optout(tmp_path, live_fake, monkeypatch):
    img = [b"\x89PNG\r\n\x1a\n" + b"0" * 32]
    router, _, _ = make_router(tmp_path)
    res = router.call("table_read_pii", {"prompt": "read"}, privacy_tier="PII", images=img)
    assert "mistral-ocr" not in res.route_log["order"] and res.model_id == "groq-qwen-vl"
    monkeypatch.setenv("MISTRAL_TRAINING_OPTOUT_CONFIRMED", "true")
    res = router.call("table_read_pii", {"prompt": "read"}, privacy_tier="PII", images=img)
    assert res.route_log["order"][0] == "mistral-ocr"


def test_unknown_privacy_tier_treated_as_pii(tmp_path, live_fake):
    router, _, _ = make_router(tmp_path)
    res = router.call("present", {"prompt": "x"}, privacy_tier="SECRETISH")
    assert res.route_log["effective_tier"] == "PII"
    assert res.model_id == "groq-qwen-vl"


@pytest.mark.parametrize("text,escalates", [
    ("Land of Ramasamy S/o Muthu, survey 233/2", True),
    ("முருகன் த/பெ ராமசாமி", True),
    ("Thiru. Ganesan owns the parcel", True),
    ("⟨OWNER_1⟩ S/o ⟨OWNER_2⟩ holds survey 233/2", False),
    ("Thiruvallur district, taluk Ponneri", False),
    ("NDVI amplitude 0.42 in the kharif window", False),
])
def test_pseudo_payload_with_raw_pii_escalates(text, escalates):
    tier, notes = gateway.effective_tier({"prompt": text}, "PSEUDO")
    assert (tier == "PII") is escalates, notes


def test_pii_field_escalates():
    assert gateway.effective_tier({"rows": [{"owner": "Kannan"}]}, "PUBLIC")[0] == "PII"
    assert gateway.effective_tier({"rows": [{"owner": "⟨OWNER_3⟩"}]}, "PUBLIC")[0] == "PUBLIC"


def test_gateway_guard_raises_on_training_tier():
    reg = load_registry()
    with pytest.raises(PrivacyViolation):
        gateway.guard(reg.model("gemini-flash-lite"), "PII")
    with pytest.raises(PrivacyViolation):
        gateway.guard(reg.model("cohere-command-a"), "whatever")      # unknown tier => PII
    gateway.guard(reg.model("gemini-flash-lite"), "PSEUDO")
    gateway.guard(reg.model("groq-qwen-vl"), "PII")


def test_guard_is_defence_in_depth(tmp_path, live_fake, monkeypatch):
    """Even if the eligibility filter were broken, the pre-call guard blocks PII."""
    from app.router import eligibility
    monkeypatch.setattr(eligibility, "privacy_ok", lambda spec, tier: (True, None))
    router, fake, _ = make_router(tmp_path)
    with pytest.raises(PrivacyViolation):
        router.call("judge", {"prompt": "x"}, privacy_tier="PII")
    assert fake.calls == []


def test_critic_excludes_producer_vendor(tmp_path, live_fake):
    router, _, _ = make_router(tmp_path)
    res = router.call("critic", {"prompt": "challenge"}, privacy_tier="PSEUDO", producer_vendor="google")
    assert res.ok and res.model_id == "groq-qwen-vl"
    assert any(f["id"] == "gemini-flash-lite" and "vendor:" in f["reason"] for f in res.route_log["filtered"])
    res = router.call("critic", {"prompt": "challenge"}, privacy_tier="PSEUDO", producer_vendor="alibaba-open")
    assert res.model_id == "gemini-flash-lite"


def test_critic_requires_producer_vendor(tmp_path, live_fake):
    """D-017: a critic call without producer_vendor is rejected before any model is contacted."""
    router, fake, _ = make_router(tmp_path)
    with pytest.raises(RouterConfigError, match="producer_vendor"):
        router.call("critic", {"prompt": "challenge"}, privacy_tier="PSEUDO")
    with pytest.raises(RouterConfigError):
        router.call("critic", {"prompt": "challenge"}, privacy_tier="PSEUDO", producer_vendor="")
    assert fake.calls == []


def test_vendor_of_helper():
    from app.router import vendor_of
    assert vendor_of("gemini-flash-lite") == "google"


def test_modality_filters(tmp_path, live_fake, monkeypatch):
    router, _, _ = make_router(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    res = router.call("sql", {"prompt": "x"}, privacy_tier="PII", images=[png])
    assert any(f["id"] == "groq-gpt-oss" and "modality" in f["reason"] for f in res.route_log["filtered"])
    monkeypatch.setenv("MISTRAL_TRAINING_OPTOUT_CONFIRMED", "true")
    res = router.call("table_read_pii", {"prompt": "x"}, privacy_tier="PII", images=[b"%PDF-1.4 tiny"])
    assert res.route_log["order"] == ["mistral-ocr"]          # groq vision cannot take PDFs


def test_hard_replan_gate(tmp_path, live_fake):
    router, _, _ = make_router(tmp_path)
    r1 = router.call("plan", {"prompt": "x"}, privacy_tier="PSEUDO")
    assert "gemini-flash" not in r1.route_log["order"]
    r2 = router.call("plan", {"prompt": "x", "hard_replan": True}, privacy_tier="PSEUDO")
    assert "gemini-flash" in r2.route_log["order"]


def test_context_filter(tmp_path, live_fake):
    router, _, _ = make_router(tmp_path)
    res = router.call("classify_text", {"prompt": "word " * 60000}, privacy_tier="PII")
    assert any(f["id"] == "local-qwen" and "context" in f["reason"] for f in res.route_log["filtered"])


def test_credentials_filter_live_but_not_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    router, _, _ = make_router(tmp_path, credentials=lambda p: (p != "groq", "missing GROQ_API_KEY"))
    res = router.call("sql", {"prompt": "x"}, privacy_tier="PII")
    assert {"groq-qwen-vl", "groq-gpt-oss"} <= {f["id"] for f in res.route_log["filtered"]}
    monkeypatch.setenv("ROUTER_MODE", "replay")
    res = router.call("sql", {"prompt": "x"}, privacy_tier="PII")
    assert "groq-qwen-vl" in res.route_log["order"]
