"""PaddleOCR PP-OCRv5 wrapper for the bake-off.

Known pitfall on this host (CPU-only paddlepaddle 3.3.1, no GPU): the default
PP-OCRv5_server_det detector combined with oneDNN raises
``NotImplementedError: ConvertPirAttribute2RuntimeAttribute ...`` inside the PIR
static-graph executor. The mobile detector avoids the crash and running with
``enable_mkldnn=False`` is required -- leaving mkldnn on crashes even with the
mobile detector. This is logged as an anomaly in the bake-off report.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_ENGINES: dict[tuple[str, str], Any] = {}

_REC_MODEL_BY_LANG = {
    "ta": "ta_PP-OCRv5_mobile_rec",
    "en": "en_PP-OCRv5_mobile_rec",
}


@dataclass
class PaddleResult:
    texts: list[str]
    scores: list[float]
    boxes: list[list[list[float]]]
    seconds: float
    joined_text: str = field(init=False)

    def __post_init__(self):
        self.joined_text = "\n".join(self.texts)


def _get_engine(lang: str):
    key = ("mobile_det", lang)
    if key in _ENGINES:
        return _ENGINES[key]
    from paddleocr import PaddleOCR

    rec_model = _REC_MODEL_BY_LANG.get(lang, _REC_MODEL_BY_LANG["en"])
    ocr = PaddleOCR(
        lang=lang,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=rec_model,
        enable_mkldnn=False,  # crashes on this host's paddlepaddle build otherwise
    )
    _ENGINES[key] = ocr
    return ocr


def ocr_image(image_path: Path | str, lang: str = "ta") -> PaddleResult:
    """Full-page OCR. `lang` selects the PP-OCRv5 recognition model ("ta" or "en");
    detection is always the mobile detector for this host (see module docstring)."""
    engine = _get_engine(lang)
    t0 = time.perf_counter()
    res = engine.predict(str(image_path))
    dt = time.perf_counter() - t0
    if not res:
        return PaddleResult([], [], [], dt)
    r0 = res[0]
    texts = list(r0.get("rec_texts") or [])
    scores = list(r0.get("rec_scores") or [])
    boxes_raw = r0.get("rec_polys")
    boxes = [np.asarray(b).tolist() for b in boxes_raw] if boxes_raw is not None else []
    return PaddleResult(texts, scores, boxes, dt)


def ocr_array(image: np.ndarray, lang: str = "ta") -> PaddleResult:
    """OCR a numpy array (BGR or RGB), used for cell crops in candidate (c)."""
    engine = _get_engine(lang)
    t0 = time.perf_counter()
    res = engine.predict(image)
    dt = time.perf_counter() - t0
    if not res:
        return PaddleResult([], [], [], dt)
    r0 = res[0]
    texts = list(r0.get("rec_texts") or [])
    scores = list(r0.get("rec_scores") or [])
    return PaddleResult(texts, scores, [], dt)
