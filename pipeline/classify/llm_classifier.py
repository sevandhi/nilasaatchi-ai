"""D-030: the doc-type/stage/scheme *decision* is an LLM call (`classify_text` / `classify_page`
via the router), not the keyword rules. Rules (`.rules.best_rule_match`) are kept only as
prompt *hints* ("rule_hints"); the LLM has the final say. Every call is cached by content hash
(`.llm_cache`), so a full run is safely resumable and prompt iteration only re-bills changed
inputs.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from ..catalog.textlayer import text_quality
from .llm_cache import content_hash, get_cache
from .llm_fallback import redact
from .rules import best_rule_match
from .taxonomy import DOC_TYPES, SCHEME_VALUES, STAGES

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "prompts" / "classify.md"
PROMPT_VERSION = "v4"  # bump on any prompt-text change to invalidate the cache

MIN_USEFUL_CHARS = 200          # D-030: below this, or a high garbage ratio -> vision fallback
IMAGE_LONG_SIDE = 1600          # D-030: page-1 image, JPEG, long side 1600

# Lead directive (post-incident, 2026-09-27): this pipeline was tuned and gated against Bedrock
# Ministral 3B/8B only. A Groq/local fallback answering instead is *not* an accepted result for
# this task -- it was never scored on the 100 labels -- so it must never be written as if it
# were a real classification. Only these two model ids are accepted; anything else (including a
# genuine successful call) is treated the same as a failure by the caller.
ACCEPTED_MODELS = {"bedrock-ministral-3b", "bedrock-ministral-8b"}
_SSO_ERROR_MARKERS = ("SSO session expired", "credentials:", "TokenRetrievalError", "ExpiredToken")


def _looks_like_sso_expired(error: str | None) -> bool:
    return bool(error) and any(m in error for m in _SSO_ERROR_MARKERS)


def _prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "page_from": {"type": "integer"},
        "page_to": {"type": "integer"},
        "doc_type": {"type": "string"},
        "scheme_relevance": {"type": "string"},
    },
    "required": ["page_from", "page_to", "doc_type", "scheme_relevance"],
}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "stage": {"type": ["string", "null"], "enum": [*STAGES, None]},
        "payee_kind": {"type": ["string", "null"]},
        "committee_level": {"type": ["string", "null"]},
        "scheme_relevance": {"type": "string", "enum": list(SCHEME_VALUES)},
        "segments": {"type": "array", "items": _SEGMENT_SCHEMA},
        "unit_no": {"type": ["string", "null"]},
        "block_no": {"type": ["string", "null"]},
        "village": {"type": ["string", "null"]},
        "doc_date": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["doc_type", "scheme_relevance", "confidence"],
}


def preflight_bedrock() -> tuple[bool, str]:
    """A single cheap real call, made once before a batch, to confirm Bedrock Ministral is
    actually answering `classify_text` (not Groq/local, not an SSO failure) -- so a whole run
    doesn't have to burn OCR + thousands of doomed calls to discover an expired SSO session
    (2026-09-27 incident). Never cached (fixed probe text, deliberately not a real document)."""
    try:
        from app.router import call
    except Exception as exc:  # noqa: BLE001
        return False, f"router unavailable: {exc}"
    probe_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    try:
        result = call("classify_text",
                       {"prompt": "Reply with exactly this JSON: {\"ok\": true}"},
                       schema=probe_schema, privacy_tier="PII")
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc} (sso_expired={_looks_like_sso_expired(str(exc))})"
    if not result.ok:
        return False, f"{result.error} (sso_expired={_looks_like_sso_expired(result.error)})"
    if result.model_id not in ACCEPTED_MODELS:
        return False, f"first response came from {result.model_id!r}, not Bedrock Ministral -- AWS SSO expired?"
    return True, result.model_id


def rule_hints(text: str, char_count: int, n: int = 3) -> list[dict]:
    """Cheap keyword-rule matches, for the prompt only -- never the decision (D-030)."""
    hits = best_rule_match(text, char_count)
    return [{"doc_type": h.doc_type, "confidence": h.confidence, "matched": h.evidence.get("matched")}
            for h in hits[:n]]


def page1_image_bytes(preview_path: str, long_side: int = IMAGE_LONG_SIDE) -> bytes | None:
    """The 150-dpi page-1 WebP preview, re-encoded as a JPEG with its long side resized to
    `long_side` (D-030). Returns None if the preview is missing (never raises)."""
    from PIL import Image

    abs_path = REPO_ROOT / preview_path
    if not abs_path.exists():
        return None
    try:
        img = Image.open(abs_path).convert("RGB")
        scale = long_side / max(img.size)
        if scale != 1.0:
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                              Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - a bad image must never crash the batch
        logger.warning("page1_image_bytes failed for %s: %s", preview_path, exc)
        return None


def needs_vision_fallback(text: str) -> bool:
    n_chars, _score, ok = text_quality(text)
    return (not ok) or n_chars < MIN_USEFUL_CHARS


def _build_prompt(class_text: str | None, folder_label: str | None, filename: str,
                   page_count: int, hints: list[dict], vision: bool) -> str:
    header = (
        f"\n\n## This document\nfolder_label: {folder_label!r}\nfilename: {filename!r}\n"
        f"page_count: {page_count}\nrule_hints: {hints}\n"
    )
    if vision:
        return _prompt_template() + header + "\n(page-1 image attached; the text layer/OCR was unreadable)\n"
    redacted = redact((class_text or "")[:6000])  # defense in depth; Bedrock is non-training (D-027)
    return _prompt_template() + header + f"\ntext (first {len(redacted)} chars):\n{redacted}\n"


def classify_document_llm(
    *, class_text: str, folder_label: str | None, filename: str, page_count: int,
    page1_preview_path: str | None = None, use_vision: bool | None = None,
) -> dict:
    """Returns the parsed LLM response dict (D-030 schema) plus `_meta` (model_id, from_cache,
    task, error). Never raises: a router/provider failure yields doc_type=None so the caller can
    fall back to OTHER / a review queue, exactly like the old rule-based OTHER default."""
    char_count = len(class_text.replace(" ", "").replace("\n", ""))
    hints = rule_hints(class_text, char_count)
    vision = needs_vision_fallback(class_text) if use_vision is None else use_vision

    image_bytes = None
    if vision and page1_preview_path:
        image_bytes = page1_image_bytes(page1_preview_path)
    if vision and image_bytes is None:
        vision = False  # no image available -- fall back to whatever text we have

    task = "classify_page" if vision else "classify_text"
    cache_input = image_bytes if vision else class_text
    key = content_hash(PROMPT_VERSION, task, cache_input or "")
    cache = get_cache()
    cached = cache.get(key)
    if cached is not None:
        cached = dict(cached)
        cached["_meta"] = {**cached.get("_meta", {}), "from_cache": True}
        return cached

    try:
        from app.router import call
    except Exception as exc:  # noqa: BLE001
        return {"doc_type": None, "_meta": {"error": f"router unavailable: {exc}", "task": task,
                                             "from_cache": False, "sso_expired": False}}

    prompt = _build_prompt(class_text if not vision else None, folder_label, filename, page_count,
                            hints, vision)
    payload = {"prompt": prompt}
    try:
        result = call(task, payload, schema=CLASSIFY_SCHEMA, privacy_tier="PII",
                       images=[image_bytes] if vision else None)
    except Exception as exc:  # noqa: BLE001 - a provider outage must never crash the batch
        return {"doc_type": None, "_meta": {"error": str(exc), "task": task, "from_cache": False,
                                             "sso_expired": _looks_like_sso_expired(str(exc))}}

    if not result.ok or not result.data:
        return {"doc_type": None, "_meta": {
            "error": result.error, "model_id": result.model_id, "task": task, "from_cache": False,
            "sso_expired": _looks_like_sso_expired(result.error),
        }}

    if result.model_id not in ACCEPTED_MODELS:
        # A real, schema-valid answer -- just not from the model this pipeline was tuned and
        # gated against (D-030 incident, 2026-09-27). Never cache or accept it: the caller must
        # leave the document's existing classification untouched and mark it pending for retry.
        return {"doc_type": None, "_meta": {
            "error": f"non-Bedrock fallback model answered: {result.model_id}", "task": task,
            "model_id": result.model_id, "fallback_model": True, "from_cache": False,
            "sso_expired": False,
        }}

    data = dict(result.data)
    data["_meta"] = {"model_id": result.model_id, "task": task, "from_cache": False,
                      "rule_hints": hints, "sso_expired": False}
    cache.set(key, data)
    return data
