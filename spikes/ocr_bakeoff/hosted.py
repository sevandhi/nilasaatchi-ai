"""Candidates (d) Mistral OCR and (e) Groq Qwen vision, called ONLY through the
router contract: ``from app.router import call``. Gated behind ``--hosted`` and
skipped cleanly if `app.router` is not importable yet (it is being built in
parallel by the agent-architect track, per T0.6).

Never call a provider SDK directly here -- that is the one rule this module
exists to enforce.

(d) Mistral OCR is dropped per `docs/decisions.md` D-018 (needs a billing method
even on the free plan). `run_mistral_table_read` is left in place, inert and
router-gated, in case that decision is ever revisited -- it is never invoked by
`run.py`.

(e) Groq Qwen vision (``groq-qwen-vl`` / ``qwen/qwen3.8-27b`` via the ``cell_reread``
task) is rate-limited per `config/models.yaml`: ``otpm: 1000`` (Groq output tokens
per minute) and the router clamps any request's ``max_tokens`` to ``otpm * 0.5``
= 500. `GroqPacer` below keeps our own call cadence under that ceiling so we do
not spend retries/quota hitting 429s the router would otherwise have to absorb.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

PUBLIC_DOC_TYPES = {"GO", "AS", "LPS"}

CELL_REREAD_MAX_TOKENS = 500  # matches the otpm=1000 * 0.5 clamp in app/router/core.py
CELL_REREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "serial": {"type": ["string", "null"]},
                    "survey_no": {"type": ["string", "null"]},
                    "sub_div": {"type": ["string", "null"]},
                    "extent_ha_raw": {"type": ["string", "null"]},
                    "extent_ac_raw": {"type": ["string", "null"]},
                    "cents_raw": {"type": ["string", "null"]},
                    "owner": {"type": ["string", "null"]},
                    "patta_no": {"type": ["string", "null"]},
                    "amount_raw": {"type": ["string", "null"]},
                    "classification_raw": {"type": ["string", "null"]},
                },
                "additionalProperties": False,
            },
        },
    },
    "required": ["rows"],
    "additionalProperties": False,
}
CELL_REREAD_SYSTEM = (
    "You are re-reading one crop of a scanned Tamil/English land-acquisition table for "
    "NilaSaatchi AI. Return every data row you can see as raw strings -- do not convert units, "
    "do not compute totals, do not translate or transliterate Tamil text (copy it exactly as "
    "written). If a cell is illegible, blurred, or you are not confident, return null for that "
    "field rather than guessing. The first row(s) of the image may be a repeated column header "
    "for your reference -- do not emit a row for the header itself.\n"
    "Field notes: survey_no and sub_div are two separate columns (e.g. survey 173, sub_div 1 for "
    "a printed '173/1') -- do not put the combined 'survey/sub_div' string into survey_no. "
    "extent_ha_raw is only the column headed ஹெக்டேர்/Hectare/Hec. (an 'H.AA.SS' triple like "
    "'0.95.50'); extent_ac_raw is only the column headed ஏக்கர்/Acre (a plain decimal like "
    "'2.35'); cents_raw is only the column headed சென்ட்/Cent. Never copy one column's value into "
    "another field."
)


def router_available() -> bool:
    try:
        from app.router import call  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 -- app.router may be mid-refactor; skip cleanly, never crash
        return False


def privacy_tier_for(doc_type_hint: str) -> str:
    return "PUBLIC" if doc_type_hint in PUBLIC_DOC_TYPES else "PII"


def downscale_long_side(image_bytes_or_array, max_side: int = 1600) -> bytes:
    """Downscale so the long side is <= max_side, re-encode as PNG. Accepts either
    raw encoded bytes (png/jpeg) or a BGR numpy array (from an in-memory crop)."""
    import cv2
    import numpy as np

    if isinstance(image_bytes_or_array, (bytes, bytearray)):
        arr = cv2.imdecode(np.frombuffer(image_bytes_or_array, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        arr = image_bytes_or_array
    h, w = arr.shape[:2]
    long_side = max(h, w)
    if long_side > max_side:
        scale = max_side / long_side
        arr = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise ValueError("failed to encode crop as PNG")
    return io.BytesIO(buf.tobytes()).getvalue()


@dataclass
class HostedResult:
    ok: bool
    text: str | None
    model_id: str | None
    latency_ms: float | None
    shadow_cost_usd: float | None
    error: str | None
    data: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0


def run_mistral_table_read(image_bytes: bytes, doc_type_hint: str) -> HostedResult:
    """Candidate (d): Mistral OCR on a table crop, via task 'table_read_pii'. Dropped
    per D-018; kept inert (never called by run.py)."""
    if not router_available():
        return HostedResult(False, None, None, None, None, "app.router not importable yet")
    from app.router import call

    t0 = time.perf_counter()
    result = call(
        "table_read_pii",
        {"instruction": "Read this land-acquisition table cell-by-cell."},
        privacy_tier=privacy_tier_for(doc_type_hint),
        images=[image_bytes],
    )
    dt_ms = (time.perf_counter() - t0) * 1000
    return HostedResult(
        ok=result.ok, text=getattr(result, "text", None), model_id=getattr(result, "model_id", None),
        latency_ms=getattr(result, "latency_ms", None) or dt_ms,
        shadow_cost_usd=getattr(result, "shadow_cost_usd", None), error=getattr(result, "error", None),
        tokens_in=getattr(result, "tokens_in", 0) or 0, tokens_out=getattr(result, "tokens_out", 0) or 0,
    )


class GroqPacer:
    """Client-side sliding-window pacer so we stay under Groq's ``otpm`` (output
    tokens/minute) budget ourselves, instead of relying on the router's quota
    check to reject calls (which would just burn an attempt as "skipped").
    Reserves ``max_tokens`` per call (the worst case), not actual usage, since we
    only know actual tokens_out after the call completes."""

    def __init__(self, otpm: int = 1000, window_s: float = 60.0, sleep_fn=time.sleep, now_fn=time.monotonic):
        self.otpm = otpm
        self.window_s = window_s
        self._sleep = sleep_fn
        self._now = now_fn
        self._events: list[tuple[float, int]] = []

    def wait_and_reserve(self, tokens: int) -> float:
        """Block until `tokens` more can be spent within the window; returns total
        seconds slept."""
        slept = 0.0
        while True:
            now = self._now()
            self._events = [(t, tok) for t, tok in self._events if now - t < self.window_s]
            used = sum(tok for _, tok in self._events)
            if used + tokens <= self.otpm:
                self._events.append((now, tokens))
                return slept
            oldest_t = self._events[0][0]
            wait_s = max(self.window_s - (now - oldest_t), 0.5)
            self._sleep(wait_s)
            slept += wait_s


@dataclass
class BandResult:
    ok: bool
    rows: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    shadow_cost_usd: float = 0.0
    model_id: str | None = None
    error: str | None = None


def run_cell_reread_band(image_bytes: bytes, doc_type_hint: str, pacer: GroqPacer | None = None) -> BandResult:
    """Candidate (e), one row-band crop: call the router's 'cell_reread' task with
    a strict JSON Schema and get back raw-string rows (normalised by the caller)."""
    if not router_available():
        return BandResult(False, error="app.router not importable yet")
    from app.router import call

    if pacer is not None:
        pacer.wait_and_reserve(CELL_REREAD_MAX_TOKENS)
    t0 = time.perf_counter()
    result = call(
        "cell_reread",
        {"system": CELL_REREAD_SYSTEM,
         "prompt": "Read every data row in this table crop and return it as JSON per the schema.",
         "max_tokens": CELL_REREAD_MAX_TOKENS},
        schema=CELL_REREAD_SCHEMA,
        privacy_tier=privacy_tier_for(doc_type_hint),
        images=[image_bytes],
    )
    dt = time.perf_counter() - t0
    if not result.ok:
        return BandResult(False, seconds=dt, tokens_in=getattr(result, "tokens_in", 0) or 0,
                          tokens_out=getattr(result, "tokens_out", 0) or 0, error=result.error,
                          model_id=result.model_id)
    rows = (result.data or {}).get("rows") or []
    return BandResult(
        True, rows=rows, seconds=dt, tokens_in=result.tokens_in or 0, tokens_out=result.tokens_out or 0,
        shadow_cost_usd=result.shadow_cost_usd or 0.0, model_id=result.model_id,
    )
