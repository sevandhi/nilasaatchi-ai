"""T1.2's "quick Tesseract tam+eng at 150 dpi, psm 4" fallback for pages with no usable text
layer. Reuses `TesseractDocker` (spikes/ocr_bakeoff, read-only) rather than reimplementing the
long-running-container pattern.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

_docker_ctx = None  # lazily-started, process-wide TesseractDocker context
_docker_lock = threading.Lock()  # classify runs the OCR fallback from a thread pool (T1.2)


def _get_docker():
    global _docker_ctx
    if _docker_ctx is not None:
        return _docker_ctx or None
    with _docker_lock:
        if _docker_ctx is not None:  # another thread won the race while we waited for the lock
            return _docker_ctx or None
        from spikes.ocr_bakeoff.tesseract_docker import TesseractDocker
        # A distinct container name: spikes/ocr_bakeoff may be running its own bake-off
        # concurrently under the default "nilasaatchi-ocr-bakeoff" name, and `docker run --name`
        # collides on duplicates (exit 125). Never touch a container we didn't start.
        ctx = TesseractDocker(REPO_ROOT, container="nilasaatchi-ocr-classify")
        try:
            ctx.start()
        except Exception as exc:  # noqa: BLE001 - docker/daemon failures must never crash classify
            logger.warning("docker unavailable for quick OCR fallback: %s", exc)
            _docker_ctx = False
            return None
        _docker_ctx = ctx
        return _docker_ctx


def quick_ocr_text(preview_path: str) -> str:
    """OCR one 150-dpi page preview (webp) with Tesseract tam+eng, psm 4. Returns "" (never
    raises) if docker or the image is unavailable."""
    try:
        docker = _get_docker()
    except Exception as exc:  # noqa: BLE001 - belt and braces: never crash the classify batch
        logger.warning("quick OCR unavailable: %s", exc)
        return ""
    if docker is None:
        return ""
    abs_path = REPO_ROOT / preview_path
    if not abs_path.exists():
        return ""
    # Content-addressed text cache: restarts never re-OCR a page (lead fix, 2026-09-27).
    import hashlib
    key = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:32]
    cache_file = REPO_ROOT / "data" / "quick_ocr_cache" / f"{key}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    try:
        result = docker.ocr_file(abs_path, lang="tam+eng", psm=4)
    except Exception as exc:  # noqa: BLE001 - OCR must never crash the classify pass
        logger.warning("quick OCR failed for %s: %s", preview_path, exc)
        return ""
    if result.returncode != 0:
        return ""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(result.text, encoding="utf-8")
    return result.text


def shutdown() -> None:
    global _docker_ctx
    if _docker_ctx and _docker_ctx is not False:
        _docker_ctx.stop()
    _docker_ctx = None
