"""Land-classification normaliser → DRY | WET | PORAMBOKE | OTHER (phase2 skill, §8, §11b)."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

_TABLE: list[tuple[str, tuple[str, ...]]] = [
    ("PORAMBOKE", ("புறம்போக்கு", "புறம்போக்", "poramboke", "porambokku", "purambokku")),
    ("WET", ("நன்செய்", "நஞ்சை", "நஞ்செய்", "wet")),
    ("DRY", ("புன்செய்", "புஞ்சை", "புன்சை", "புஞ்செய்", "dry", "patta (dry)")),
    ("OTHER", ("வீட்டுமனை", "மனை", "house site", "மற்றவை", "others", "other")),
]
_FUZZY_MIN = 80


def normalise_classification(raw: str | None) -> str | None:
    s = unicodedata.normalize("NFC", raw or "").strip().lower()
    s = re.sub(r"[\s.:()\-]+", " ", s).strip()
    if not s or s in {"-", "--", "nil"}:
        return None
    for code, variants in _TABLE:
        if any(v in s for v in variants):
            return code
    compact = s.replace(" ", "")
    best, best_sc = None, 0.0
    for code, variants in _TABLE:
        for v in variants:
            if len(v) < 4:
                continue
            sc = fuzz.ratio(compact, v.replace(" ", ""))
            if sc > best_sc:
                best, best_sc = code, sc
    return best if best_sc >= _FUZZY_MIN else None
