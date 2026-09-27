"""Whole-page raw-cell table transcription through `app.router` (D-010, promoted from
spikes/ocr_bakeoff/fullpage_vlm.py).

- primary read: task `table_read_pii` (Bedrock Ministral 3 8B; router falls back to Groq itself),
  JPEG long side 1800 px, max_tokens 4096;
- second read (D-032): task `table_second_read_pii` (Bedrock 8B → Groq → review_queue) on a
  genuinely different view — the table region cropped from the grid detector's bbox and zoomed
  (long side 2000 px), with a prompt variant asking for a canonical column order
  (`prompts/extract_table_second.md`). No grid → the whole page at the higher zoom.

Cached by content hash of (image bytes, prompt text, schema, route, params): re-runs are free.
Privacy: whole pages go only to PII-eligible routes (Bedrock, Groq: `trains_on_free_tier: false`);
the router enforces this and fails closed.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path

from PIL import Image

from .cache import ContentCache, content_hash
from .mapping import profile_for

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "prompts" / "extract_table_page.md"
SECOND_PROMPT_PATH = REPO_ROOT / "prompts" / "extract_table_second.md"
SCHEMA_PATH = REPO_ROOT / "schemas" / "extraction" / "vlm_page.json"
CACHE_DIR = REPO_ROOT / "data" / "extract" / "cache" / "vlm"

PREFERRED_MODEL = "bedrock-ministral-8b"   # first model of table_read_pii / table_second_read_pii
QUOTA_RETRIES = 2
QUOTA_WAIT_S = 62

ROUTES = {
    "primary": {"task": "table_read_pii", "only_model": None, "long_side": 1800, "max_tokens": 4096},
    "second": {"task": "table_second_read_pii", "only_model": None, "long_side": 2000, "max_tokens": 4096,
               "crop": True, "prompt": "second"},
}


def load_schema() -> dict:
    s = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in s.items() if not k.startswith("$") and k not in {"title", "description"}}


def build_prompt(doc_type: str | None, variant: str = "primary") -> str:
    hint = profile_for(doc_type).hint
    text = (SECOND_PROMPT_PATH if variant == "second" else PROMPT_PATH).read_text(encoding="utf-8")
    return text.replace("{doc_hint}", f"Context: {hint}\n" if hint else "")


CROP_PAD = 0.03          # fraction of page added around the table bbox
MIN_CROP_AREA = 0.12     # a "table" smaller than this share of the page is not trusted


def table_crop_box(image_path: Path) -> tuple[tuple[int, int, int, int] | None, str]:
    """(left, top, right, bottom) of the ruled table region from the grid detector, padded."""
    import cv2

    from .grid import detect_table_grid
    img = cv2.imread(str(image_path))
    if img is None:
        return None, "none"
    h, w = img.shape[:2]
    g = detect_table_grid(img)
    if not g.bbox:
        return None, "none"
    x, y, bw, bh = g.bbox
    if bw * bh < MIN_CROP_AREA * w * h:
        return None, "grid_too_small"
    px, py = int(CROP_PAD * w), int(CROP_PAD * h)
    return (max(0, x - px), max(0, y - py), min(w, x + bw + px), min(h, y + bh + py)), \
        ("grid" if g.found else "grid_outline")


def jpeg_bytes(image_path: Path, long_side: int, box: tuple[int, int, int, int] | None = None) -> bytes:
    """JPEG of the page (or of `box`), scaled so the long side is `long_side` px — upscaled too,
    so a crop is a genuine zoom."""
    im = Image.open(image_path).convert("RGB")
    if box:
        im = im.crop(box)
    scale = long_side / max(im.size)
    if scale < 1:
        im.thumbnail((long_side, long_side))
    elif scale > 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def transcribe(image_path: Path, doc_type: str | None, privacy_tier: str, route: str = "primary",
               cache: ContentCache | None = None, allow_calls: bool = True, crop: bool | None = None) -> dict:
    """Return {ok, data, model_id, tokens_in, tokens_out, cost_usd, secs, error, from_cache, cache_key}."""
    spec = ROUTES[route]
    cache = cache or ContentCache(CACHE_DIR)
    use_crop = spec.get("crop") if crop is None else (crop and spec.get("crop"))
    box, crop_method = (table_crop_box(Path(image_path)) if use_crop else (None, "page"))
    img = jpeg_bytes(Path(image_path), spec["long_side"], box)
    prompt = build_prompt(doc_type, spec.get("prompt", "primary"))
    schema = load_schema()
    key = content_hash(img, prompt, json.dumps(schema, sort_keys=True), route, spec["task"],
                       str(spec["only_model"]), str(spec["max_tokens"]))
    hit = cache.get(key)
    # a read served by a fallback model (Groq while the AWS SSO session was expired: ~500-token
    # outputs, often truncated) is reused only in cache-only runs; live runs re-ask the router so
    # the first-choice model can replace it
    if hit is not None and hit.get("ok") and (hit.get("model_id") == PREFERRED_MODEL or not allow_calls):
        return {**hit, "from_cache": True, "cache_key": key, "crop_method": crop_method}
    if not allow_calls:
        return {"ok": False, "data": None, "error": "not cached and calls disabled", "from_cache": False,
                "cache_key": key, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "model_id": None}
    from app.router import call   # all hosted calls go through the router (task routing only)

    payload = {"instruction": prompt, "max_tokens": spec["max_tokens"], "step_id": f"extract-{key[:10]}"}
    t0 = time.time()
    for attempt in range(QUOTA_RETRIES + 1):
        r = call(spec["task"], payload, schema=schema, privacy_tier=privacy_tier, images=[img])
        # the router refuses a call that would exceed a per-minute quota (Groq 8k TPM); wait for
        # the window to roll over instead of dropping the read — never bypass the router's check
        if r.ok or "quota:tpm" not in (r.error or "") or attempt == QUOTA_RETRIES:
            break
        time.sleep(QUOTA_WAIT_S)
    if not r.ok and hit is not None and hit.get("ok"):
        # the first-choice model is still unavailable: keep the earlier fallback read
        return {**hit, "from_cache": True, "cache_key": key, "crop_method": crop_method,
                "upgrade_error": r.error}
    out = {"ok": bool(r.ok and r.data), "data": r.data, "model_id": r.model_id, "tokens_in": r.tokens_in,
           "tokens_out": r.tokens_out, "cost_usd": r.shadow_cost_usd, "secs": round(time.time() - t0, 1),
           "error": r.error, "route": route, "privacy_tier": privacy_tier, "crop_box": list(box) if box else None,
           "crop_method": crop_method}
    if out["ok"]:
        cache.set(key, out)
    return {**out, "from_cache": False, "cache_key": key}
