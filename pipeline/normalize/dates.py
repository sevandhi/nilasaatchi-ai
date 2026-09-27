"""Date normaliser: numeric and Tamil-month dates → ISO with a precision flag (§5, §11b).

Blank-day dates (".02.2025") are common on Form E/F and DLPNC: they return the 1st of the month
with precision "month". Year-only ("    .2023") returns precision "year". Sanity range
2020-01-01 .. 2026-12-31 (§5); out-of-range dates return (None, None).
"""
from __future__ import annotations

import re

from .numbers import tamil_numerals_to_ascii

TAMIL_MONTHS = {
    "ஜனவரி": 1, "பிப்ரவரி": 2, "பிப்பிரவரி": 2, "மார்ச்": 3, "ஏப்ரல்": 4, "மே": 5, "ஜூன்": 6,
    "ஜூலை": 7, "ஆகஸ்ட்": 8, "ஆகஸ்டு": 8, "செப்டம்பர்": 9, "அக்டோபர்": 10, "நவம்பர்": 11,
    "டிசம்பர்": 12,
}
EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

MIN_YEAR, MAX_YEAR = 2020, 2026

_NUM_RE = re.compile(r"(?<!\d)(\d{1,2})?\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4}|\d{2})(?!\d)")
_YEAR_ONLY_RE = re.compile(r"^\s*[./\-]?\s*[./\-]?\s*(\d{4})\s*$")
_TA_RE = re.compile(r"(\d{1,2})?\s*[-,]?\s*(" + "|".join(sorted(TAMIL_MONTHS, key=len, reverse=True))
                    + r")\s*[,\-]?\s*(\d{4})")
_EN_RE = re.compile(r"(\d{1,2})?\s*(?:st|nd|rd|th)?\s*[-,]?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                    r"[a-z]*\.?\s*[,\-]?\s*(\d{4})", re.IGNORECASE)


def _year4(y: str) -> int:
    yi = int(y)
    return 2000 + yi if len(y) == 2 else yi


def _ok(y: int, m: int, d: int | None) -> bool:
    if not (MIN_YEAR <= y <= MAX_YEAR and 1 <= m <= 12):
        return False
    return d is None or 1 <= d <= 31


def _fmt(y: int, m: int, d: int | None) -> tuple[str, str]:
    return (f"{y:04d}-{m:02d}-{d:02d}", "day") if d else (f"{y:04d}-{m:02d}-01", "month")


def parse_date(raw: str | None) -> tuple[str | None, str | None]:
    """Return (iso, precision) for the first valid date in `raw`; precision ∈ day|month|year."""
    s = tamil_numerals_to_ascii(raw)
    if not s:
        return None, None
    for m in _NUM_RE.finditer(s):
        d, mo, y = m.groups()
        di = int(d) if d else None
        yi, mi = _year4(y), int(mo)
        if _ok(yi, mi, di):
            return _fmt(yi, mi, di)
    for rx, table in ((_TA_RE, TAMIL_MONTHS), (_EN_RE, EN_MONTHS)):
        for m in rx.finditer(s):
            d, mon, y = m.groups()
            mi = table[mon.lower()[:3]] if table is EN_MONTHS else table[mon]
            di = int(d) if d else None
            if _ok(int(y), mi, di):
                return _fmt(int(y), mi, di)
    m = _YEAR_ONLY_RE.match(s)
    if m and MIN_YEAR <= int(m.group(1)) <= MAX_YEAR:
        return f"{int(m.group(1)):04d}-01-01", "year"
    return None, None


def parse_date_iso(raw: str | None) -> str | None:
    """Day-precision ISO date only (bake-off API): blank-day and year-only dates → None."""
    iso, prec = parse_date(raw)
    return iso if prec == "day" else None


def find_dates(text: str | None) -> list[tuple[str, str, str]]:
    """All valid dates in free text as (iso, precision, raw_match), in reading order."""
    s = tamil_numerals_to_ascii(text)
    out: list[tuple[int, str, str, str]] = []
    for rx in (_NUM_RE, _TA_RE, _EN_RE):
        for m in rx.finditer(s):
            iso, prec = parse_date(m.group(0))
            if iso:
                out.append((m.start(), iso, prec, m.group(0).strip()))
    out.sort()
    return [(iso, prec, raw) for _, iso, prec, raw in out]
