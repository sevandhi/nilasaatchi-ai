"""OCR text/cells -> the qa-evaluator's row schema.

Target schema (`eval/golden/mini.jsonl`, one object per page)::

    {"page_id", "doc_type",
     "header": {"village", "unit_no", "block_no", "doc_date", "doc_no"},
     "rows": [{"survey_no", "sub_div", "extent_ha", "extent_ac", "owner",
               "amount_rs", "classification"}],
     "notes"}

Two extraction paths feed the same schema:
  - `header_from_text`: regex over the full-page OCR text (works for every
    candidate, since headers are prose, not tables).
  - `rows_from_grid`: column-keyword matching against a detected table grid's
    header row, used by candidate (c). `rows_from_text` is the best-effort
    fallback used when no grid was found (candidates a/b on prose pages, or c
    falling back).

This module is intentionally promotable to `pipeline/extract/` unchanged in
Phase 2, per T0.5's brief.
"""
from __future__ import annotations

import re
from typing import Any

from spikes.ocr_bakeoff.grid import Cell
from spikes.ocr_bakeoff.normalize import (
    cents_to_ac,
    cents_to_ha,
    ha_to_ac,
    parse_date_iso,
    parse_extent_ac,
    parse_extent_ha,
    parse_money_inr,
    parse_survey_ref,
)

# ---------------------------------------------------------------------------
# Header extraction (land-domain-knowledge §7, §8)
# ---------------------------------------------------------------------------

_VILLAGE_RE = re.compile(
    r"(?:கிராமம்|Village)\s*[:：]?\s*([^\n,;]+?)(?=\s{2,}|\s+(?:அலகு|unit|UNIT|$)|[\n,;]|$)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"(?:அலகு|UNIT)\s*[-:：]?\s*([0-9IVXivx]+)", re.IGNORECASE)
_BLOCK_RE = re.compile(r"(?:பிளாக்|BLOCK|Block)\s*[-:：]?\s*([0-9]+)", re.IGNORECASE)
_DATE_ANY_RE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{4}")
_DOC_NO_RE = re.compile(
    r"(?:Roc\.?\s*No\.?|ந\.க\.|Lr\.?No\.?|நாள்.{0,3}எண்)\s*[:：]?\s*([A-Za-z0-9./()\-]+)",
    re.IGNORECASE,
)

VILLAGE_ALIASES = {
    "அல்லிகுளம்": "Allikulam", "அல்லிக்குளம்": "Allikulam",
    "கீழத்தட்டப்பாறை": "Keelathattaparai", "கீழதட்டப்பாறை": "Keelathattaparai",
    "மேலத்தட்டப்பாறை": "Melathattaparai",
    "உமரிக்கோட்டை": "Umarikottai",
    "பேரூரணி": "Peroorani", "பேருரணி": "Peroorani",
    "இராமசாமிபுரம்": "Ramasamypuram",
    "தெற்கு சிலுக்கன்பட்டி": "South Silukanpatti",
}


def canonical_village(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    for tamil, canon in VILLAGE_ALIASES.items():
        if tamil in raw:
            return canon
    return raw or None


def header_from_text(text: str) -> dict[str, Any]:
    header: dict[str, Any] = {
        "village": None, "unit_no": None, "block_no": None, "doc_date": None, "doc_no": None,
    }
    m = _VILLAGE_RE.search(text)
    if m:
        header["village"] = canonical_village(m.group(1))
    m = _UNIT_RE.search(text)
    if m:
        header["unit_no"] = m.group(1).strip()
    m = _BLOCK_RE.search(text)
    if m:
        header["block_no"] = m.group(1).strip()
    dates = [parse_date_iso(d) for d in _DATE_ANY_RE.findall(text)]
    dates = [d for d in dates if d]
    if dates:
        header["doc_date"] = max(dates)  # documents usually lead with the latest/issue date
    m = _DOC_NO_RE.search(text)
    if m:
        header["doc_no"] = m.group(1).strip()
    return header


# ---------------------------------------------------------------------------
# Row/column classification for grid cells (candidate c)
# ---------------------------------------------------------------------------

COLUMN_KEYWORDS: dict[str, list[str]] = {
    "sl_no": ["வ.எண", "வஎண", "s.no", "sl.no", "sl no"],
    "survey_no": ["புலஎண", "புல எண", "survey", "புலணண"],
    "sub_div": ["உட்பிரிவு", "sub_div", "sub-division", "subdivision"],
    "classification": ["வகைப்பாடு", "classification", "class"],
    "extent_ha": ["ஹெக்டேர", "ஹெக்", "hec", "hect"],
    "extent_ac": ["ஏக்கர", "ஏக்", "acre", "ac."],
    "extent_cents": ["சென்ட", "cent"],
    "owner": ["நிலஉரிமையாளர", "நில உரிமையாளர", "உரிமையாளர", "land owner", "name of the land owner", "owner", "பெயர்"],
    "amount_rs": ["தொகை", "compensation", "amount", "(rs", "ரூ"],
    "patta_no": ["பட்டா", "patta"],
}


# More specific fields first: a Form F header like "Total compensation amount
# apportioned to the land owners (Rs.)" contains the substring "owners", so
# amount_rs/classification/extent_* must be tried before the generic "owner"
# keywords, or it would be misclassified.
_COLUMN_PRIORITY = [
    "amount_rs", "extent_cents", "extent_ha", "extent_ac", "classification",
    "sub_div", "survey_no", "patta_no", "owner", "sl_no",
]


def _despace(s: str) -> str:
    """Drop internal whitespace before keyword matching. OCR splits Tamil
    conjunct headers inconsistently (e.g. "புலஎண்" vs "புல எண்" for the same
    "survey no." header), so keyword membership must be space-insensitive."""
    return re.sub(r"\s+", "", s)


def classify_columns(header_texts: list[str]) -> list[str | None]:
    """Best-effort column -> field name mapping from a grid's header-row texts."""
    fields: list[str | None] = []
    for raw in header_texts:
        low = _despace((raw or "").strip().lower())
        matched = None
        for field in _COLUMN_PRIORITY:
            keywords = COLUMN_KEYWORDS[field]
            if any(_despace(kw.lower()) in low for kw in keywords):
                matched = field
                break
        fields.append(matched)
    return fields


def _row_dict_from_cells(field_by_col: list[str | None], row_texts: dict[int, str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "survey_no": None, "sub_div": None, "extent_ha": None, "extent_ac": None,
        "owner": None, "amount_rs": None, "classification": None,
    }
    raw_extent_cents = None
    raw_survey_field = None
    for col, field in enumerate(field_by_col):
        if field is None:
            continue
        text = row_texts.get(col, "")
        if not text:
            continue
        if field == "survey_no":
            raw_survey_field = text
        elif field == "sub_div" and row.get("sub_div") is None:
            row["sub_div"] = text.strip()
        elif field == "extent_ha":
            row["extent_ha"] = parse_extent_ha(text)
        elif field == "extent_ac":
            row["extent_ac"] = parse_extent_ac(text)
        elif field == "extent_cents":
            raw_extent_cents = text
        elif field == "owner":
            row["owner"] = text.strip()
        elif field == "amount_rs":
            row["amount_rs"] = parse_money_inr(text)
        elif field == "classification":
            row["classification"] = text.strip()

    if raw_survey_field:
        refs = parse_survey_ref(raw_survey_field)
        if refs:
            row["survey_no"] = refs[0].survey_no
            if row["sub_div"] is None:
                row["sub_div"] = refs[0].sub_div

    if raw_extent_cents is not None:
        cents = parse_extent_ac(raw_extent_cents)  # cents are printed as a plain number
        if cents is not None:
            row.setdefault("extent_cents", cents)
            if row["extent_ac"] is None:
                row["extent_ac"] = cents_to_ac(cents)
            if row["extent_ha"] is None:
                row["extent_ha"] = cents_to_ha(cents)

    if row["extent_ha"] is None and row["extent_ac"] is not None:
        row["extent_ha"] = None  # do not invent ha from ac; conversion direction is ha->ac only per spec
    return row


def rows_from_grid(cells: list[Cell], cell_text: dict[tuple[int, int], str]) -> list[dict[str, Any]]:
    """Build row dicts from a detected grid. Assumes row 0 is the header row and
    classifies columns from it; skips a trailing "மொத்தம்/Total" row."""
    if not cells:
        return []
    n_rows = max(c.row for c in cells) + 1
    n_cols = max(c.col for c in cells) + 1
    header_texts = [cell_text.get((0, c), "") for c in range(n_cols)]
    field_by_col = classify_columns(header_texts)
    rows: list[dict[str, Any]] = []
    for r in range(1, n_rows):
        row_texts = {c: cell_text.get((r, c), "") for c in range(n_cols)}
        joined = " ".join(row_texts.values()).strip().lower()
        if not joined:
            continue
        if "மொத்தம" in joined or "total" in joined:
            continue
        row = _row_dict_from_cells(field_by_col, row_texts)
        if any(v is not None for k, v in row.items() if k != "classification"):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Fallback: best-effort row extraction straight from unstructured OCR text
# (candidates a/b, or c when no grid was found)
# ---------------------------------------------------------------------------

_SURVEY_IN_TEXT_RE = re.compile(r"\b(\d{1,4}\s*/\s*[0-9A-Za-z]{1,3})\b")
_EXTENT_TRIPLE_IN_TEXT_RE = re.compile(r"\b(\d{1,3}\.\d{1,2}\.\d{1,2})\b")
_MONEY_IN_TEXT_RE = re.compile(r"(?:₹|Rs\.?|ரூ)[.\s]*([0-9][0-9,]*)\s*/?-?")


def rows_from_text(text: str) -> list[dict[str, Any]]:
    """A weak but dependency-free fallback: pair up survey-number tokens and
    H.AA.SS extents that appear on the same line. Owner/amount are left None
    unless a money token is on the same line -- prose OCR rarely lines these up
    reliably, which is exactly the gap candidate (c)/(d)/(e) exist to close."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        survey_m = _SURVEY_IN_TEXT_RE.search(line)
        extent_m = _EXTENT_TRIPLE_IN_TEXT_RE.search(line)
        money_m = _MONEY_IN_TEXT_RE.search(line)
        if not survey_m and not extent_m:
            continue
        refs = parse_survey_ref(survey_m.group(1)) if survey_m else []
        ha = parse_extent_ha(extent_m.group(1)) if extent_m else None
        row = {
            "survey_no": refs[0].survey_no if refs else None,
            "sub_div": refs[0].sub_div if refs else None,
            "extent_ha": ha,
            "extent_ac": ha_to_ac(ha) if ha is not None else None,
            "owner": None,
            "amount_rs": parse_money_inr(money_m.group(1)) if money_m else None,
            "classification": None,
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Candidate (e): hosted VLM (Groq Qwen vision) row-band JSON -> row schema
# ---------------------------------------------------------------------------

def rows_from_hosted_json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise the raw-string rows returned by `hosted.run_cell_reread_band`
    (schema: serial/survey_no/sub_div/extent_ha_raw/extent_ac_raw/cents_raw/
    owner/patta_no/amount_raw/classification_raw) into the common row schema.
    The model is instructed to return raw strings and never guess or convert
    units itself -- all unit/money parsing happens here, through the same
    normalisers every other candidate uses."""
    out: list[dict[str, Any]] = []
    for r in rows or []:
        extent_ha = parse_extent_ha(r.get("extent_ha_raw"))
        extent_ac = parse_extent_ac(r.get("extent_ac_raw"))
        cents = parse_extent_ac(r.get("cents_raw"))  # cents are a plain decimal count
        if cents is not None:
            if extent_ac is None:
                extent_ac = cents_to_ac(cents)
            if extent_ha is None:
                extent_ha = cents_to_ha(cents)
        survey_no = (r.get("survey_no") or "").strip() or None
        sub_div = (r.get("sub_div") or "").strip() or None
        if survey_no and sub_div is None and "/" in survey_no:
            # The model sometimes returns the whole "177/4A" token in survey_no
            # even though the schema has a separate sub_div field -- re-split it
            # rather than lose the subdivision (observed on g04's live smoke test).
            refs = parse_survey_ref(survey_no)
            if refs:
                survey_no, sub_div = refs[0].survey_no, refs[0].sub_div
        out.append({
            "survey_no": survey_no,
            "sub_div": sub_div,
            "extent_ha": extent_ha,
            "extent_ac": extent_ac,
            "owner": (r.get("owner") or "").strip() or None,
            "amount_rs": parse_money_inr(r.get("amount_raw")),
            "classification": (r.get("classification_raw") or "").strip() or None,
        })
    return out
