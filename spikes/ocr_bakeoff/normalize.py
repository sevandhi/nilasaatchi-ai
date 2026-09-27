"""Field normalisers for the OCR bake-off, per `land-domain-knowledge` skill sections 5-6.

These are deliberately dependency-light (stdlib + rapidfuzz) so they can be promoted
to `pipeline/normalize/` in Phase 2 unchanged. Every function is pure and total: it
returns ``None`` (or ``[]``) on unparseable input instead of raising, because OCR text
is always noisy.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants (land-domain-knowledge §5, §9)
# ---------------------------------------------------------------------------

HA_TO_AC = 2.47105
CENT_TO_AC = 0.01
CENT_TO_M2 = 40.4686
HA_TO_M2 = 10_000.0

TAMIL_DIGITS = "௦௧௨௩௪௫௬௭௮௯"
TAMIL_TO_ASCII_DIGIT = {d: str(i) for i, d in enumerate(TAMIL_DIGITS)}

TAMIL_MONTHS = {
    "ஜனவரி": 1, "பிப்ரவரி": 2, "மார்ச்": 3, "ஏப்ரல்": 4, "மே": 5, "ஜூன்": 6,
    "ஜூலை": 7, "ஆகஸ்ட்": 8, "செப்டம்பர்": 9, "அக்டோபர்": 10,
    "நவம்பர்": 11, "டிசம்பர்": 12,
}

def _clean(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    return s.strip()


def tamil_numerals_to_ascii(s: str) -> str:
    """Map Tamil digit glyphs to ASCII digits. Only touches the digit characters;
    everything else (including Tamil letters that OCR may confuse with digits) is
    left untouched, per land-domain-knowledge §5 ("normalise only inside numeric
    fields")."""
    return "".join(TAMIL_TO_ASCII_DIGIT.get(ch, ch) for ch in _clean(s))


# ---------------------------------------------------------------------------
# Extent parsing: H.AA.SS triple notation and chitta "H - AA.SS"
# ---------------------------------------------------------------------------

_TRIPLE_RE = re.compile(r"^(\d+)\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})$")
_DASH_RE = re.compile(r"^(\d+)\s*-\s*(\d{1,2}(?:\.\d{1,2})?)$")
_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)?)$")


def parse_extent_ha(raw: str | None) -> float | None:
    """Parse a hectare extent in H.AA.SS triple notation (e.g. "0.95.50" -> 0.9550)
    or chitta "H - AA.SS" notation (e.g. "0 - 53.50" -> 0.5350).

    Formula: value = H + AA/100 + SS/10000 (AA = ares out of 100/ha, SS = m^2 out of
    10000/ha). See land-domain-knowledge §5.
    """
    s = tamil_numerals_to_ascii(raw or "").strip()
    if not s:
        return None
    s = s.replace(",", "").replace(" ", "")
    # re-insert single spaces around dash for the dash pattern (already stripped above)
    m = _TRIPLE_RE.match(s)
    if m:
        h_s, aa_s, ss_s = m.groups()
        h, aa = int(h_s), int(aa_s)
        # The 3rd group (centiares/sqm) is sometimes printed with the trailing zero
        # dropped, e.g. "185.96.5" for what the AS letter elsewhere prints as
        # "185.96.50" (land-domain-knowledge §11b). Right-pad (not left-pad) short
        # groups to 2 digits so "5" reads as 50 sqm, not 5 sqm.
        ss = int(ss_s.ljust(2, "0"))
        return round(h + aa / 100 + ss / 10_000, 4)
    m = _DASH_RE.match(s)
    if m:
        h = int(m.group(1))
        rest = float(m.group(2))
        return round(h + rest / 100, 4)
    m = _PLAIN_RE.match(s)
    if m:
        # A bare decimal is ambiguous (could already be ha, e.g. a "மொத்தம்" total
        # column reprinted without the triple-dot). Accept it as-is.
        return round(float(m.group(1)), 4)
    return None


def parse_extent_ac(raw: str | None) -> float | None:
    """Parse an acre extent, which documents print as a plain decimal (e.g. "2.35")."""
    s = tamil_numerals_to_ascii(raw or "").strip().replace(",", "")
    if not s:
        return None
    m = re.match(r"^\d+(\.\d+)?$", s)
    if not m:
        return None
    return round(float(s), 4)


def ha_to_ac(ha: float) -> float:
    return round(ha * HA_TO_AC, 4)


def ac_to_ha(ac: float) -> float:
    return round(ac / HA_TO_AC, 4)


def cents_to_ac(cents: float) -> float:
    return round(cents * CENT_TO_AC, 4)


def cents_to_ha(cents: float) -> float:
    return round(cents * CENT_TO_M2 / HA_TO_M2, 4)


def extents_agree(ha: float | None, ac: float | None) -> bool | None:
    """Cross-check the printed ha and ac figures agree, per land-domain-knowledge §5:
    most documents convert with the exact factor 2.47105, but many use the rounded
    2.47 or truncate instead of rounding. Accept either factor within +-0.02 acre,
    or a scaled 0.1% tolerance for extents over 10 ha. Returns None if either side
    is missing."""
    if ha is None or ac is None:
        return None
    tol = max(0.02, ha * HA_TO_AC * 0.001) if ha > 10 else 0.02
    for factor in (HA_TO_AC, 2.47):
        if abs(ha * factor - ac) <= tol:
            return True
    return False


# ---------------------------------------------------------------------------
# Money (Indian grouping)
# ---------------------------------------------------------------------------

_MONEY_RE = re.compile(r"[₹Rs.INRரூ\s]*([0-9,]+(?:\.\d+)?)\s*/?-?")


def parse_money_inr(raw: str | None) -> int | None:
    """Parse Indian-grouped rupee amounts, e.g. "₹12,60,000/-" -> 1260000."""
    s = tamil_numerals_to_ascii(raw or "").strip()
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def compensation_matches(amount_rs: float, extent_ac: float, rate_per_ac: float, tol_frac: float = 0.02) -> bool:
    """amount ~= extent_acres * rate_per_acre, per land-domain-knowledge §5."""
    expected = extent_ac * rate_per_ac
    if expected == 0:
        return amount_rs == 0
    return abs(amount_rs - expected) / expected <= tol_frac


# ---------------------------------------------------------------------------
# Survey numbers (land-domain-knowledge §6)
# ---------------------------------------------------------------------------

_SURVEY_TOKEN_RE = re.compile(r"^(\d+)\s*/\s*([0-9A-Za-z]+)$")
_SURVEY_BARE_RE = re.compile(r"^(\d+)$")


@dataclass(frozen=True)
class SurveyRef:
    survey_no: str
    sub_div: str | None


_AND_ONLY_RE = re.compile(r"\s*(?:மற்றும்|&|\band\b)\s*", re.IGNORECASE)
_SURVEY_SLASH_RE = re.compile(r"^(\d+)\s*/\s*(.+)$")


def parse_survey_ref(raw: str | None) -> list[SurveyRef]:
    """Parse survey-number tokens per land-domain-knowledge §6/§11b:

    - "177/4A" -> one ref, survey_no=177 sub_div=4A.
    - "172/1 மற்றும் 172/2" (Tamil "and") -> two distinct refs.
    - "1 / 6,7,8,10A" -> **one** ref: survey_no=1, sub_div="6,7,8,10A" (a compound
      subdivision list is one parcel entry per §11b; do not split it).
    - a bare "6,7,8,10A" with no leading "N/" -> four distinct survey numbers with
      no subdivision (the comma here separates survey numbers, not subdivisions).
    """
    s = tamil_numerals_to_ascii(raw or "").strip()
    if not s:
        return []
    # Only a real conjunction ("and"/மற்றும்/&) separates distinct parcel entries.
    # A bare comma is ambiguous and handled per-part below.
    top_parts = [p.strip() for p in _AND_ONLY_RE.split(s) if p.strip()]
    refs: list[SurveyRef] = []
    for part in top_parts:
        m = _SURVEY_SLASH_RE.match(part)
        if m:
            refs.append(SurveyRef(m.group(1), m.group(2).strip()))
            continue
        # No "N/" prefix: a comma list here means distinct survey numbers.
        for tok in (t.strip() for t in part.split(",")):
            if not tok:
                continue
            m = _SURVEY_TOKEN_RE.match(tok)
            if m:
                refs.append(SurveyRef(m.group(1), m.group(2)))
            elif _SURVEY_BARE_RE.match(tok):
                refs.append(SurveyRef(tok, None))
            else:
                refs.append(SurveyRef(tok, None))
    return refs


_MERGED_EXTENT_RE = re.compile(r"^(\d{1,3})\.(\d{1,2})\.(\d{1,2})$")


def split_merged_survey_extent(raw: str | None) -> tuple[str, float] | None:
    """Recover "<sub_div><extent>" when OCR/layout runs a table cell's subdivision
    and its H.AA.SS extent together with no separator, e.g. "173/20.86.00" meaning
    survey 173/2, extent 0.86.00 ha (land-domain-knowledge §11b). Returns
    (sub_div, extent_ha) or None if the tail after the slash does not decompose.
    Only call this on the text *after* the "/" (the sub_div+extent cell); the
    caller supplies the survey_no separately.
    """
    s = tamil_numerals_to_ascii(raw or "").strip().replace(" ", "")
    if not s:
        return None
    for sub_len in (1, 2):
        sub, rest = s[:sub_len], s[sub_len:]
        m = _MERGED_EXTENT_RE.match(rest)
        if m:
            h, aa, ss = m.groups()
            ha = round(int(h) + int(aa) / 100 + int(ss.ljust(2, "0")) / 10_000, 4)
            return sub, ha
    return None


def survey_key(village_id: str, ref: SurveyRef) -> str:
    """Canonical KIDE-style matching key: village_id | survey_no | sub_div."""
    sub = (ref.sub_div or "").strip().upper()
    return f"{village_id}|{ref.survey_no.strip()}|{sub}"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_NUMERIC_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")


def parse_date_iso(raw: str | None) -> str | None:
    """Parse dd.mm.yyyy / dd/mm/yyyy / dd-mm-yyyy into ISO yyyy-mm-dd, with a sanity
    range of 2020-01-01 to 2026-12-31 (land-domain-knowledge §5)."""
    s = tamil_numerals_to_ascii(raw or "").strip()
    if not s:
        return None
    m = _DATE_NUMERIC_RE.match(s)
    if not m:
        # try "dd <tamil month> yyyy"
        m2 = re.match(r"^(\d{1,2})\s+([^\s\d]+)\s+(\d{4})$", s)
        if m2 and m2.group(2) in TAMIL_MONTHS:
            day, month_name, year = m2.groups()
            month = TAMIL_MONTHS[month_name]
            day_i, year_i = int(day), int(year)
        else:
            return None
    else:
        day_i, month, year_i = (int(x) for x in m.groups())
    if not (1 <= day_i <= 31 and 1 <= month <= 12):
        return None
    if not (2020 <= year_i <= 2026):
        return None
    return f"{year_i:04d}-{month:02d}-{day_i:02d}"


# ---------------------------------------------------------------------------
# Text-quality metrics used by the scorer
# ---------------------------------------------------------------------------

def cer(reference: str, hypothesis: str) -> float | None:
    """Character error rate = Levenshtein(ref, hyp) / len(ref)."""
    ref = reference or ""
    hyp = hypothesis or ""
    if not ref:
        return None
    try:
        from rapidfuzz.distance import Levenshtein
        dist = Levenshtein.distance(ref, hyp)
    except ImportError:
        dist = _levenshtein(ref, hyp)
    return dist / len(ref)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]
