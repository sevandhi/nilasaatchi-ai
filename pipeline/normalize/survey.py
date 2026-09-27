"""Survey-number normalisers (land-domain-knowledge §6, §11b). Promoted from the bake-off.

- "177/4A" → (177, "4A");  "172/1 மற்றும் 172/2" → two refs;  "6,7,8,10A" → four surveys;
- "1 / 6,7,8,10A" → ONE ref with a compound sub-division (a single parcel entry, §11b);
- "171/1 (பகுதி)" → (171, "1", part=True);  Tamil numerals are mapped to ASCII.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import tamil_numerals_to_ascii


@dataclass(frozen=True)
class SurveyRef:
    survey_no: str
    sub_div: str | None
    part: bool = False


_AND_RE = re.compile(r"\s*(?:மற்றும்|&|\band\b)\s*", re.IGNORECASE)
_SLASH_RE = re.compile(r"^(\d+)\s*/\s*(.+)$")
_TOKEN_RE = re.compile(r"^(\d+)\s*/\s*([0-9A-Za-z]+)$")
_PART_RE = re.compile(r"\(?\s*(பகுதி|part)\s*\)?", re.IGNORECASE)


def canon_subdiv(sub: str | None) -> str | None:
    """Upper-case, drop spaces and trailing punctuation: " 4 a." → "4A"."""
    if sub is None:
        return None
    s = re.sub(r"\s+", "", tamil_numerals_to_ascii(sub)).strip(".,;:-").upper()
    return s or None


def parse_survey_ref(raw: str | None) -> list[SurveyRef]:
    s = tamil_numerals_to_ascii(raw)
    if not s:
        return []
    part = bool(_PART_RE.search(s))
    s = _PART_RE.sub("", s).strip().strip(".,;")
    s = re.sub(r"^(?:S\.?\s*No\.?|புல\s*எண்)\s*[:.]?\s*", "", s, flags=re.IGNORECASE)
    refs: list[SurveyRef] = []
    for piece in (p.strip() for p in _AND_RE.split(s) if p.strip()):
        m = _SLASH_RE.match(piece)
        if m:
            refs.append(SurveyRef(m.group(1), canon_subdiv(m.group(2)), part))
            continue
        for tok in (t.strip() for t in piece.split(",")):
            if not tok:
                continue
            m = _TOKEN_RE.match(tok)
            if m:
                refs.append(SurveyRef(m.group(1), canon_subdiv(m.group(2)), part))
            elif re.fullmatch(r"\d+[A-Za-z]?", tok):
                refs.append(SurveyRef(tok.upper(), None, part))
    return refs


def parse_survey_pair(survey_cell: str | None, subdiv_cell: str | None) -> SurveyRef | None:
    """Chitta/LDR style: survey and sub-division in separate columns ("172" | "1")."""
    refs = parse_survey_ref(survey_cell)
    sub = canon_subdiv(subdiv_cell)
    if sub in {"-", "--", "NIL"}:
        sub = None
    if refs:
        r = refs[0]
        return SurveyRef(r.survey_no, sub or r.sub_div, r.part)
    return None


_MERGED_EXTENT_RE = re.compile(r"^(\d{1,3})\.(\d{1,2})\.(\d{1,2})$")


def split_merged_survey_extent(raw: str | None) -> tuple[str, float] | None:
    """Recover "<sub_div><H.AA.SS>" run together: tail "20.86.00" of "173/20.86.00" →
    ("2", 0.86) (§11b). Pass only the text after the '/'."""
    s = tamil_numerals_to_ascii(raw).replace(" ", "")
    if not s:
        return None
    for n in (1, 2):
        sub, rest = s[:n], s[n:]
        m = _MERGED_EXTENT_RE.match(rest)
        if m:
            h, aa, ss = m.groups()
            return sub, round(int(h) + int(aa) / 100 + int(ss.ljust(2, "0")) / 10_000, 4)
    return None


def survey_key(village: str, ref: SurveyRef) -> str:
    """Canonical key `village|survey_no|sub_div` (§6)."""
    return f"{village}|{ref.survey_no.strip()}|{(ref.sub_div or '').strip().upper()}"


def kide_of(ref: SurveyRef) -> str:
    """FMB KIDE form: `survey/sub_div`, or `survey` when there is no sub-division."""
    return f"{ref.survey_no}/{ref.sub_div}" if ref.sub_div else ref.survey_no


def parcel_uid_of(village: str | None, ref: SurveyRef) -> str | None:
    """D-020 parcel key `<canonical village>|<KIDE>`; None without a village (never guess)."""
    if not village:
        return None
    return f"{village}|{kide_of(ref)}"
