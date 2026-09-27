import builtins

import numpy as np

from spikes.ocr_bakeoff import hosted


def test_router_unavailable_is_handled_cleanly(monkeypatch):
    """Candidates d/e must report a clean, non-crashing skip -- never raise --
    when `app.router` cannot be imported (it may not exist yet, or may be
    mid-refactor in a parallel worktree)."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "app.router" or name.startswith("app.router."):
            raise ImportError("simulated: app.router not ready")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert hosted.router_available() is False
    result = hosted.run_mistral_table_read(b"fake-bytes", "FORM_F")
    assert result.ok is False
    assert result.error == "app.router not importable yet"
    result = hosted.run_cell_reread_band(b"fake-bytes", "GO")
    assert result.ok is False
    assert result.error == "app.router not importable yet"


def test_privacy_tier_public_vs_pii():
    assert hosted.privacy_tier_for("GO") == "PUBLIC"
    assert hosted.privacy_tier_for("AS") == "PUBLIC"
    assert hosted.privacy_tier_for("LPS") == "PUBLIC"
    assert hosted.privacy_tier_for("FORM_E") == "PII"
    assert hosted.privacy_tier_for("FORM_F") == "PII"


def test_downscale_long_side_shrinks_and_reencodes():
    arr = np.full((400, 3000, 3), 255, dtype=np.uint8)
    import cv2
    ok, buf = cv2.imencode(".png", arr)
    assert ok
    out = hosted.downscale_long_side(buf.tobytes(), max_side=1600)
    decoded = cv2.imdecode(np.frombuffer(out, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= 1600


def test_downscale_long_side_leaves_small_images_alone():
    arr = np.full((100, 200, 3), 255, dtype=np.uint8)
    out = hosted.downscale_long_side(arr, max_side=1600)
    import cv2
    decoded = cv2.imdecode(np.frombuffer(out, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (100, 200)


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_groq_pacer_allows_burst_under_budget():
    clock = _FakeClock()
    pacer = hosted.GroqPacer(otpm=1000, window_s=60, sleep_fn=clock.sleep, now_fn=clock.now)
    slept1 = pacer.wait_and_reserve(500)
    slept2 = pacer.wait_and_reserve(500)
    assert slept1 == 0.0
    assert slept2 == 0.0  # 500+500 == otpm, still allowed


def test_groq_pacer_waits_when_over_budget():
    clock = _FakeClock()
    pacer = hosted.GroqPacer(otpm=1000, window_s=60, sleep_fn=clock.sleep, now_fn=clock.now)
    pacer.wait_and_reserve(500)
    pacer.wait_and_reserve(500)
    slept = pacer.wait_and_reserve(500)  # 1500 > 1000 -> must wait for the window to clear
    assert slept > 0.0


def test_run_cell_reread_band_skips_cleanly_without_router(monkeypatch):
    monkeypatch.setattr(hosted, "router_available", lambda: False)
    result = hosted.run_cell_reread_band(b"fake", "FORM_F")
    assert result.ok is False
    assert result.error == "app.router not importable yet"
    assert result.rows == []


def test_run_cell_reread_band_paces_before_calling(monkeypatch):
    calls = []

    class FakeRouterResult:
        ok = True
        data = {"rows": [{"survey_no": "173", "sub_div": "1"}]}  # noqa: RUF012 -- test fixture, not a real class
        tokens_in = 1200
        tokens_out = 300
        shadow_cost_usd = 0.0002
        model_id = "groq-qwen-vl"
        error = None

    def fake_call(task, payload, *, schema=None, privacy_tier="PUBLIC", images=None, producer_vendor=None):
        calls.append((task, payload, schema, privacy_tier))
        return FakeRouterResult()

    monkeypatch.setattr(hosted, "router_available", lambda: True)
    import app.router as router_mod
    monkeypatch.setattr(router_mod, "call", fake_call, raising=False)

    class RecordingPacer:
        def __init__(self):
            self.reserved = []

        def wait_and_reserve(self, tokens):
            self.reserved.append(tokens)
            return 0.0

    pacer = RecordingPacer()
    result = hosted.run_cell_reread_band(b"fake-image-bytes", "FORM_E", pacer=pacer)
    assert result.ok is True
    assert result.rows == [{"survey_no": "173", "sub_div": "1"}]
    assert pacer.reserved == [hosted.CELL_REREAD_MAX_TOKENS]
    assert calls[0][0] == "cell_reread"
    assert calls[0][3] == "PII"
    assert calls[0][2] == hosted.CELL_REREAD_SCHEMA
