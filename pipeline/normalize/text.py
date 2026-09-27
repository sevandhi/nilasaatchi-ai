"""Text-quality helpers (promoted from the bake-off scorer)."""
from __future__ import annotations

from rapidfuzz.distance import Levenshtein


def cer(reference: str | None, hypothesis: str | None) -> float | None:
    """Character error rate = Levenshtein(ref, hyp) / len(ref); None for an empty reference."""
    ref, hyp = reference or "", hypothesis or ""
    if not ref:
        return None
    return Levenshtein.distance(ref, hyp) / len(ref)
