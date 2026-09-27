"""Page-segmented classification (D-025 / land-domain-knowledge §3): split a document's pages
into runs that share the same scheme_relevance, and independently rule-classify each run's own
text (so an unrelated errata page or a different scheme's notice gets its own doc_type/stage,
not the whole document's)."""
from __future__ import annotations

from dataclasses import dataclass

from .rules import best_rule_match
from .scheme import aggregate_doc_scheme, classify_scheme
from .taxonomy import stage_of


@dataclass
class Segment:
    seg_no: int
    page_start: int
    page_end: int
    doc_type: str | None
    type_confidence: float | None
    type_evidence: dict
    scheme_relevance: str
    stage: str | None


def build_segments(page_texts: list[str]) -> list[Segment]:
    """One segment per maximal run of consecutive pages with the same per-page scheme label.
    A document with a single scheme throughout (the overwhelming majority) yields exactly one
    segment, so this adds no overhead for ordinary documents."""
    if not page_texts:
        return []
    page_schemes = [classify_scheme(t) for t in page_texts]

    runs: list[tuple[int, int]] = []  # 0-based [start, end) page index ranges
    start = 0
    for i in range(1, len(page_schemes) + 1):
        if i == len(page_schemes) or page_schemes[i] != page_schemes[start]:
            runs.append((start, i))
            start = i

    segments: list[Segment] = []
    for seg_no, (s, e) in enumerate(runs, start=1):
        seg_text = "\n".join(page_texts[s:e])
        char_count = len(seg_text.replace(" ", "").replace("\n", ""))
        hits = best_rule_match(seg_text, char_count)
        doc_type = hits[0].doc_type if hits else None
        confidence = hits[0].confidence if hits else None
        evidence = hits[0].evidence if hits else {}
        segments.append(Segment(
            seg_no=seg_no,
            page_start=s + 1,
            page_end=e,
            doc_type=doc_type,
            type_confidence=confidence,
            type_evidence=evidence,
            scheme_relevance=page_schemes[s],
            stage=stage_of(doc_type),
        ))
    return segments


def doc_level_scheme(segments: list[Segment]) -> str:
    return aggregate_doc_scheme([s.scheme_relevance for s in segments])
