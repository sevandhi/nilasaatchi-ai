"""T1.2 header pre-parse: unit/block/village/doc_no/date regexes over the first page(s) of
text (land-domain-knowledge §5, phase1-data-foundation T1.2). Cheap and local; P2 refines it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .villages import find_village

UNIT_RE = re.compile(r"(?:அலகு|Unit)\s*[-:]?\s*(\d+)", re.IGNORECASE)
BLOCK_RE = re.compile(r"(?:பிளாக்|Block|தொகுதி)\s*[-:]?\s*(\d+)", re.IGNORECASE)

TAMIL_MONTHS = {
    "ஜனவரி": 1, "பிப்ரவரி": 2, "மார்ச்": 3, "ஏப்ரல்": 4, "மே": 5, "ஜூன்": 6,
    "ஜூலை": 7, "ஆகஸ்ட்": 8, "செப்டம்பர்": 9, "அக்டோபர்": 10, "நவம்பர்": 11, "டிசம்பர்": 12,
}
TAMIL_MONTH_RE = re.compile("|".join(TAMIL_MONTHS))

# dd.mm.yyyy or dd/mm/yyyy, where the day is *optional* (blank-day dates like ".02.2025" are
# common -- land-domain-knowledge §11b). A stray space before the separator ("104.31 .00") is
# tolerated for extents but dates are stricter: we only allow it around the day.
NUMERIC_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})?\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})(?!\d)")
TAMIL_DATE_RE = re.compile(r"(\d{1,2})?\s*,?\s*(" + "|".join(TAMIL_MONTHS) + r")\s*[,-]?\s*(\d{4})")

DOC_NO_PATTERNS = [
    re.compile(r"Lr\.?\s*No\.?\s*([A-Za-z0-9/.\-]+)", re.IGNORECASE),
    re.compile(r"G\.?O\.?\s*\(?Ms\.?\)?\s*No\.?\s*(\d+)", re.IGNORECASE),
    re.compile(r"Gazette\s*No\.?\s*(\d+)", re.IGNORECASE),
    re.compile(r"Na\.?\s*Ka\.?\s*([A-Za-z0-9/.\-]+)", re.IGNORECASE),
    re.compile(r"\b(A\d+/[A-Za-z0-9/\-]+/\d{4})\b"),  # award/Form-E batch id, e.g. A7/10/Unit-7-Block-9/2024
]

TAMIL_RANGE = re.compile(r"[஀-௿]")
LATIN_RANGE = re.compile(r"[A-Za-z]")


@dataclass
class Headers:
    unit_no: str | None = None
    block_no: str | None = None
    village: str | None = None
    doc_no: str | None = None
    doc_date: str | None = None          # ISO yyyy-mm-dd, or yyyy-mm-01 when day is blank
    date_precision: str | None = None    # day | month
    lang: str | None = None              # ta | en | ta+en


def _detect_lang(text: str) -> str | None:
    if not text.strip():
        return None
    tamil = len(TAMIL_RANGE.findall(text))
    latin = len(LATIN_RANGE.findall(text))
    if tamil == 0 and latin == 0:
        return None
    if tamil > 0 and latin > 0 and min(tamil, latin) / max(tamil, latin) > 0.15:
        return "ta+en"
    return "ta" if tamil >= latin else "en"


def _valid_ymd(year_i: int, month_i: int, day_i: int | None) -> bool:
    if not (2015 <= year_i <= 2027 and 1 <= month_i <= 12):
        return False
    return not (day_i is not None and not 1 <= day_i <= 31)


def _first_date(text: str) -> tuple[str | None, str | None]:
    # NB: try every match, not just the first -- OCR garbage can produce an out-of-range day
    # (e.g. digits running together, land-domain-knowledge §11b) that must be skipped rather
    # than raising a DB date-overflow error.
    for m in NUMERIC_DATE_RE.finditer(text):
        day, month, year = m.groups()
        month_i, year_i = int(month), int(year)
        day_i = int(day) if day else None
        if not _valid_ymd(year_i, month_i, day_i):
            continue
        if day_i:
            return f"{year_i:04d}-{month_i:02d}-{day_i:02d}", "day"
        return f"{year_i:04d}-{month_i:02d}-01", "month"
    for m in TAMIL_DATE_RE.finditer(text):
        day, month_name, year = m.groups()
        month_i = TAMIL_MONTHS[month_name]
        year_i = int(year)
        day_i = int(day) if day else None
        if not _valid_ymd(year_i, month_i, day_i):
            continue
        if day_i:
            return f"{year_i:04d}-{month_i:02d}-{day_i:02d}", "day"
        return f"{year_i:04d}-{month_i:02d}-01", "month"
    return None, None


ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def normalize_llm_date(value: str | None) -> str | None:
    """The D-030 LLM classifier is asked for an ISO date but, being an LLM, may still answer
    with a dd.mm.yyyy string (the documents' own native format) or something unparsable. Never
    pass a malformed value to the `document.doc_date` column (regression: "15.03.2021" read as
    month=15 raised a Postgres DatetimeFieldOverflow)."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if m := ISO_DATE_RE.match(value):
        year_i, month_i, day_i = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year_i:04d}-{month_i:02d}-{day_i:02d}" if _valid_ymd(year_i, month_i, day_i) else None
    iso, _precision = _first_date(value)
    return iso


def _first_doc_no(text: str) -> str | None:
    for pat in DOC_NO_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def parse_headers(text: str) -> Headers:
    h = Headers()
    if m := UNIT_RE.search(text):
        h.unit_no = m.group(1)
    if m := BLOCK_RE.search(text):
        h.block_no = m.group(1)
    h.village = find_village(text)
    h.doc_no = _first_doc_no(text)
    h.doc_date, h.date_precision = _first_date(text)
    h.lang = _detect_lang(text)
    return h
