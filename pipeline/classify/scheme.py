"""Scheme relevance detector (T1.6). Applied per page, so a multi-notification gazette can be
segmented (land-domain-knowledge §3: `3(1) Published/527_Gazette_Block_1.pdf` mixes an
unrelated Chennai errata, an Allikulam s.3(1) notice and a Tirunelveli Solar Power Plant notice
in one file).
"""
from __future__ import annotations

import re

from .taxonomy import SCHEME_ALLIKULAM, SCHEME_OTHER, SCHEME_UNKNOWN
from .villages import VILLAGE_RE

# Allikulam signals: the scheme's own names (both "SIPCOT Allikulam Scheme" and "Formation of
# Oil Refinery Project by SIPCOT", land-domain-knowledge §3 correction), G.O. No.100, a unit
# number with Thoothukudi/Thoothukkudi, or any of the 7 village aliases (VILLAGE_RE).
ALLIKULAM_RE = re.compile(
    r"Allikulam|அல்லிக?குளம்"
    r"|Oil\s+Refinery\s+Project\s+by\s+SIPCOT"
    r"|G\.?O\.?\s*\(?Ms\)?\s*No\.?\s*100\b"
    r"|Thoothuk[k]?udi",
    re.IGNORECASE,
)

# other_scheme signals: any other named SIPCOT / government scheme -- so far only the
# Tirunelveli solar plant has been observed, but the pattern is written to generalise.
OTHER_SCHEME_RE = re.compile(
    r"Solar\s+Power\s+Plant|Tirunelveli|Manur\b|Chittarchatram",
    re.IGNORECASE,
)


def classify_scheme(text: str) -> str:
    """Return SCHEME_ALLIKULAM / SCHEME_OTHER / SCHEME_UNKNOWN for one page (or segment) of
    text. Allikulam takes precedence when both appear on the same page (rare, at a notification
    boundary), because the page still evidences the Allikulam scheme somewhere on it."""
    has_allikulam = bool(ALLIKULAM_RE.search(text)) or bool(VILLAGE_RE.search(text))
    has_other = bool(OTHER_SCHEME_RE.search(text))
    if has_allikulam:
        return SCHEME_ALLIKULAM
    if has_other:
        return SCHEME_OTHER
    return SCHEME_UNKNOWN


def aggregate_doc_scheme(segment_schemes: list[str]) -> str:
    """Document-level scheme_relevance from its segments' schemes. Allikulam wins if present
    anywhere in the file (the file is in scope), matching the qa golden-set finding that the
    mixed 527 gazette is labelled `allikulam` at doc level even though one of its three
    notifications is a different scheme entirely."""
    if any(s == SCHEME_ALLIKULAM for s in segment_schemes):
        return SCHEME_ALLIKULAM
    if any(s == SCHEME_OTHER for s in segment_schemes):
        return SCHEME_OTHER
    return SCHEME_UNKNOWN
