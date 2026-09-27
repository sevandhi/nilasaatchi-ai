"""VCR-style record/replay determinism, secret redaction, telemetry, vision message format."""
from __future__ import annotations

import base64
import io
import json

import pytest

from app.router import fixtures
from app.router.messages import build_messages, sniff_mime
from app.router.secrets import redact
from app.router.types import ProviderError
from tests.router.helpers import SCHEMA, make_router, mini_registry


class Exploding:
    def complete(self, req):
        raise AssertionError("provider must not be called in replay mode")


def test_record_then_replay_is_deterministic(tmp_path, monkeypatch):
    fx = tmp_path / "fx"
    monkeypatch.setenv("ROUTER_FIXTURE_DIR", str(fx))
    monkeypatch.setenv("ROUTER_MODE", "record")
    router, fake, _ = make_router(tmp_path / "rec", registry=mini_registry(), latency_s={"a": 0.25})
    rec = router.call("t", {"prompt": "secret prompt text", "step_id": "s1"}, schema=SCHEMA)
    assert rec.ok and len(fake.calls) == 1
    files = list(fx.rglob("*.json"))
    assert len(files) == 1 and "secret prompt text" not in files[0].read_text()

    monkeypatch.setenv("ROUTER_MODE", "replay")
    results = []
    for i in range(2):
        r2, _, _ = make_router(tmp_path / f"rep{i}", registry=mini_registry())
        r2._providers = {p: Exploding() for p in ("groq", "gemini")}
        results.append(r2.call("t", {"prompt": "secret prompt text", "step_id": "s1"}, schema=SCHEMA))
    for r in results:
        assert (r.ok, r.model_id, r.text, r.data, r.latency_ms, r.tokens_in) == \
               (rec.ok, rec.model_id, rec.text, rec.data, rec.latency_ms, rec.tokens_in)
    assert results[0].route_log["scores"] == results[1].route_log["scores"]


def test_replay_missing_fixture_falls_through(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_FIXTURE_DIR", str(tmp_path / "empty"))
    router, fake, _ = make_router(tmp_path, registry=mini_registry())
    r = router.call("t", {"prompt": "x"})
    assert not r.ok and fake.calls == []
    assert {a["error_kind"] for a in r.route_log["attempts"]} == {"fixture_missing"}


def test_recorded_errors_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_FIXTURE_DIR", str(tmp_path / "fx"))
    monkeypatch.setenv("ROUTER_MODE", "record")
    router, _, _ = make_router(tmp_path / "r", registry=mini_registry(),
                               script={"a": [ProviderError("bad_request", "x", 400)]})
    router.call("t", {"prompt": "x"})
    monkeypatch.setenv("ROUTER_MODE", "replay")
    r2, _, _ = make_router(tmp_path / "r2", registry=mini_registry())
    r = r2.call("t", {"prompt": "x"})
    assert r.model_id == "b" and r.route_log["attempts"][0]["error_kind"] == "bad_request"


def test_default_mode_under_pytest_is_replay(monkeypatch):
    monkeypatch.delenv("ROUTER_MODE", raising=False)
    assert fixtures.current_mode() == "replay"


def test_secrets_are_redacted_everywhere(tmp_path, monkeypatch):
    secret = "sk-TESTSECRET-1234567890"
    monkeypatch.setenv("FAKE_PROVIDER_API_KEY", secret)
    monkeypatch.setenv("ROUTER_MODE", "record")
    monkeypatch.setenv("ROUTER_FIXTURE_DIR", str(tmp_path / "fx"))
    router, _, _ = make_router(tmp_path, registry=mini_registry(),
                               script={m: [ProviderError("auth", f"bad key {secret}", 401)] for m in "abc"})
    r = router.call("t", {"prompt": "x"})
    assert secret not in (r.error or "") and secret not in json.dumps(r.route_log)
    assert all(secret not in p.read_text() for p in (tmp_path / "fx").rglob("*.json"))
    rows = json.dumps(router.telemetry.rows())
    assert secret not in rows and "***" in rows
    assert redact("postgresql://u:pw123456@h/db") == "postgresql://u:***@h/db"


def test_telemetry_record_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTER_MODE", "live")
    router, _, _ = make_router(tmp_path, registry=mini_registry())
    router.call("t", {"prompt": "x", "step_id": "step-7", "run_id": "run-1"}, privacy_tier="PII")
    row = router.telemetry.rows(1)[0]
    for k in ("step_id", "task", "candidates", "filtered", "scores", "choice", "attempt", "latency_ms",
              "tokens_in", "tokens_out", "shadow_cost_usd", "actual_cost_usd", "privacy_tier", "outcome"):
        assert k in row
    assert row["step_id"] == "step-7" and row["choice"] == "a" and row["shadow_cost_usd"] > 0
    assert row["actual_cost_usd"] == 0.0


# ---- messages -----------------------------------------------------------------------------------
def _png(size=8):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (0, 128, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_vision_message_format():
    png, pdf = _png(), b"%PDF-1.4\n..."
    msgs = build_messages({"prompt": "What crop?", "parcel": "P-1"}, SCHEMA, [png, pdf])
    assert msgs[0]["role"] == "system" and "JSON Schema" in msgs[0]["content"]
    parts = msgs[-1]["content"]
    assert parts[0]["type"] == "text" and "What crop?" in parts[0]["text"] and '"parcel": "P-1"' in parts[0]["text"]
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[2]["type"] == "file" and parts[2]["file"]["file_data"].startswith("data:application/pdf")
    assert sniff_mime(png) == "image/png" and sniff_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_upscale_small_images_for_groq():
    from PIL import Image

    from app.router.providers.litellm_provider import upscale_small_images
    msgs = build_messages({"prompt": "read"}, None, [_png(16), _png(64)])
    out = upscale_small_images(msgs, 32)
    urls = [p["image_url"]["url"] for p in out[-1]["content"] if p["type"] == "image_url"]
    sizes = [Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1]))).size for u in urls]
    assert sizes == [(32, 32), (64, 64)]


def test_public_api_contract():
    import inspect

    from app.router import RouterResult, call
    sig = inspect.signature(call)
    assert list(sig.parameters) == ["task", "payload", "schema", "privacy_tier", "images", "producer_vendor",
                                  "only_model", "exclude_models", "allow_fallback_on_defer"]
    assert sig.parameters["privacy_tier"].default == "PUBLIC"
    assert set(RouterResult.model_fields) == {"ok", "model_id", "text", "data", "latency_ms", "tokens_in",
                                              "tokens_out", "shadow_cost_usd", "route_log", "error",
                                              "error_kind"}


def test_unknown_task_raises():
    from app.router import RouterConfigError, get_router
    with pytest.raises(RouterConfigError):
        get_router().call("no_such_task", {})
