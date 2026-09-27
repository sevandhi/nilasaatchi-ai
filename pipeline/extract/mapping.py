"""Per-doc-type column mapping: VLM raw-cell tables → typed field dicts (D-010 step 3).

The VLM transcribes headers noisily ("விளத்தினம் ஹெக்." for விஸ்தீரணம் ஹெக்., "பிளாக் எண்" for
புல எண், "வெக்டர்" for ஹெக்டேர்), so each column is mapped by two votes:

1. **header keywords** — a Tamil + English dictionary per doc type (`DOC_PROFILES`), matched
   space-insensitively, with a fuzzy fallback (partial ratio ≥ 85) for OCR-damaged Tamil;
2. **cell content type** — every data cell is typed (ha triple `0.95.50` / chitta `0 - 53.50`,
   survey `173/1`, decimal, integer, money `1,35,496/-`, text); the column's majority type must be
   compatible with the header field, else content inference decides.

Output rows hold *raw* strings per field; `pipeline.extract.records` runs the normalisers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from pipeline.normalize.numbers import tamil_numerals_to_ascii

# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------
BASE_KEYWORDS: dict[str, list[str]] = {
    "serial": ["வ.எண்", "வ.எண", "வஎண்", "வரிசை எண்", "s.no", "sl.no", "sl no", "si. no", "si.no", "sno"],
    "survey_no": ["புல எண்", "புலஎண்", "புல எண்கள்", "புலங்கள்", "survey no", "survey number", "sur vey",
                  "s.f.no", "r.s.no", "survey"],
    "sub_div": ["உட்பிரிவு", "உட்பிரிவு எண்", "sub division", "sub-division", "subdivision", "sub div"],
    "extent_ha": ["ஹெக்டேர்", "ஹெக்டர்", "ஹெக்.", "ஹெக்", "hect", "hec.", "in hec", "(ha)", "hectare"],
    "extent_ac": ["ஏக்கர்", "ஏக்.", "ஏக்", "acre", "in acre", "ac."],
    "cents": ["சென்ட்", "செண்ட்", "cent"],
    "patta_no": ["பட்டா எண்", "பட்டா", "patta no", "patta"],
    "owner": ["நில உரிமையாளர்", "நிலஉரிமையாளர்", "உரிமையாளர்", "உரிமைதாரர்", "உடமைதாரர்", "தாரர்", "பெயர்",
              "name of the land owner", "land owner", "owner", "beneficiary", "payee", "name"],
    "classification": ["வகைப்பாடு", "வகை", "classification", "class", "nature of land"],
    "amount_rs": ["இழப்பீட்டுத் தொகை", "இழப்பீடு தொகை", "தொகை", "compensation", "amount", "(rs", "ரூ"],
    "plot_no": ["பிளாட் எண்", "மனை எண்", "plot no", "plot"],
    "trees": ["மரங்கள்", "மரம்", "trees"],
    "buildings": ["கட்டிடங்கள்", "கட்டிடம்", "buildings", "structures"],
    "north": ["வடக்கு", "north"], "east": ["கிழக்கு", "east"], "south": ["தெற்கு", "south"],
    "west": ["மேற்கு", "west"],
    "assessment_rs": ["தீர்வை", "assessment"],
    "remarks": ["குறிப்புகள்", "குறிப்பு", "remarks"],
    # extent label without a unit: resolved by content (triple → ha, decimal → acres)
    "extent_generic": ["விஸ்தீரணம்", "விஸ்தீர்ணம்", "விஸ்தீரனம்", "extent", "பரப்பு"],
}
# More specific fields first (a Form F header "Total compensation amount apportioned to the land
# owners" contains "owners"; a chitta "புன்செய் தீர்வை" must be assessment, not classification).
BASE_PRIORITY = ["amount_rs", "assessment_rs", "cents", "extent_ha", "extent_ac", "plot_no", "trees",
                 "buildings", "north", "east", "south", "west", "sub_div", "survey_no", "patta_no",
                 "classification", "remarks", "extent_generic", "owner", "serial"]

# content types each field accepts (see `cell_type`)
COMPAT: dict[str, set[str]] = {
    "serial": {"int"}, "survey_no": {"survey", "int"}, "sub_div": {"int", "subdiv", "survey"},
    "extent_ha": {"ha", "dec"}, "extent_ac": {"dec", "int"}, "cents": {"dec", "int"},
    "patta_no": {"int"}, "owner": {"text"}, "classification": {"text"},
    "amount_rs": {"money", "int", "dec", "bignum"}, "plot_no": {"int", "subdiv"}, "trees": {"text", "int", "dec"},
    "buildings": {"text", "int", "dec"}, "north": {"text", "survey", "int", "subdiv"},
    "east": {"text", "survey", "int", "subdiv"}, "south": {"text", "survey", "int", "subdiv"},
    "west": {"text", "survey", "int", "subdiv"}, "assessment_rs": {"dec", "int"},
    "remarks": {"text", "survey", "int", "dec", "money", "subdiv", "ha"},
    "extent_generic": {"ha", "dec"},
    "share": {"share", "text"},
}


@dataclass
class Profile:
    family: str
    extra_keywords: dict[str, list[str]] = field(default_factory=dict)
    drop_fields: tuple[str, ...] = ()
    fill_down: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()           # fields a good read of this type should produce
    hint: str = ""


_PARCEL_HINT = ("Tables list survey numbers (புல எண்), extents in hectares printed as H.AA.SS "
                "and/or acres, owners (நில உரிமையாளர்) and sometimes patta numbers or amounts.")
DOC_PROFILES: dict[str, Profile] = {
    "AWARD_7_2": Profile("parcel", fill_down=("owner", "patta_no", "classification"),
                         expected=("survey_no", "extent_ha", "owner"), hint=_PARCEL_HINT),
    "AWARD_7_3": Profile("parcel", extra_keywords={"extent_ha_total": ["மொத்த விஸ்தீரணம்", "total extent"]},
                         fill_down=("owner", "patta_no", "classification"),
                         expected=("survey_no", "extent_ha", "owner"),
                         hint=_PARCEL_HINT + " A 7(3) award may print both the total and the acquired extent."),
    "SEC32_NOTICE": Profile("parcel", expected=("survey_no", "extent_ha", "owner"),
                            hint="Newspaper s.3(2) notice (படிவம்-பி): columns serial, survey no, extent (H.AA.SS), "
                                 "owners and interested persons."),
    "SEC32_ERRATA": Profile("parcel", expected=("survey_no", "extent_ha", "owner"),
                            hint="Errata (திருத்திய அறிவிப்பு): usually two side-by-side tables, 'already published' "
                                 "(left) and 'to be read as' (right). Transcribe them as two separate tables in "
                                 "that order."),
    "SEC31_GAZETTE": Profile("parcel", expected=("survey_no", "extent_ha"), hint=_PARCEL_HINT),
    "LDR": Profile("parcel", drop_fields=("owner",), expected=("survey_no", "extent_ha"),
                   hint="Land Delivery Certificate: columns serial, survey no, sub-division, extent (Hec., Acre), "
                        "boundaries North/East/South/West, classification."),
    "POSSESSION_CERT": Profile("parcel", drop_fields=("owner",), expected=("survey_no", "extent_ha")),
    "CHITTA": Profile("parcel",
                      extra_keywords={"dry_extent": ["புன்செய்", "பஞ்சேய்", "புஞ்சை"],
                                      "wet_extent": ["நன்செய்", "நஞ்சேய்", "நஞ்சை"],
                                      "other_extent": ["மற்றவை"]},
                      expected=("survey_no", "sub_div", "extent_ha"),
                      hint="Chitta (e-services patta extract): survey number (புல எண்) and sub-division "
                           "(உட்பிரிவு) are separate columns; extents are printed as H - AA.SS under "
                           "புன்செய் / நன்செய் with the assessment (தீர்வை) beside them; keep the remarks text."),
    "FORM_E": Profile("parcel", expected=("survey_no", "owner"),
                      hint="Form E notice: the table may be handwritten (owner, plot number, extent in cents). "
                           "Transcribe only what you can read; use null otherwise."),
    "EXEMPTION_GO": Profile("parcel", expected=("survey_no", "extent_ha")),
    "EXEMPTION_PROPOSAL": Profile("parcel", expected=("survey_no", "extent_ha")),
    "CALCULATION_SHEET": Profile("parcel", expected=("survey_no",)),
    "GO": Profile("village_totals", expected=("village", "patta_ha"),
                  hint="Village-level extents table (hectares H.AA.SS and/or acres) with patta/dry, poramboke "
                       "and total columns."),
    "AS": Profile("village_totals", expected=("village", "total_ha"),
                  hint="Village-level extents table with Patta (Dry), Poramboke and Total, each in hectares "
                       "and acres."),
    "AS_PROPOSAL": Profile("village_totals", expected=("village", "total_ha")),
    "LPS": Profile("village_totals", expected=("village", "total_ha")),
    "FORM_F": Profile("owner_amount", expected=("owner", "amount_rs"),
                      hint="Form F apportionment: owner names and the compensation amount apportioned (Rs.)."),
    "DISBURSEMENT": Profile("owner_amount", expected=("amount_rs",)),
    "COURT_DEPOSIT": Profile("instrument", expected=("amount_rs",),
                             hint="Payment instrument (demand draft / deposit): transcribe the DD number, payee, "
                                  "amount in figures and words, and date boxes."),
    "BANK_INSTRUMENT": Profile("instrument", expected=("amount_rs",)),
    "DLPNC": Profile("rate", expected=("rate_per_acre",),
                     hint="Price-negotiation committee note: transcribe label/value tables as two-column tables."),
    "SLPNC": Profile("rate", expected=("rate_per_acre",)),
    "LAND_VALUE": Profile("rate"),
    "FUNDS": Profile("rate"),
}


def profile_for(doc_type: str | None) -> Profile:
    return DOC_PROFILES.get(doc_type or "", Profile("parcel", hint=_PARCEL_HINT))


# ---------------------------------------------------------------------------
# Cell typing
# ---------------------------------------------------------------------------
_ZW = re.compile(r"[​-‍﻿]")
_HA_RE = re.compile(r"^\d{1,4}\s*[.:]\s*\d{1,2}\s*[.:]\s*\d{1,2}$|^\d{1,3}\s*[-–]\s*\d{1,2}(?:\.\d{1,2})?$")
_SURVEY_RE = re.compile(r"^\d{1,4}\s*/\s*[0-9A-Za-z][0-9A-Za-z,\s]*(\(.*\))?$")
_DEC_RE = re.compile(r"^\d{1,5}\.\d{1,3}$")
_INT_RE = re.compile(r"^\d{1,5}\.?$")
_BIGNUM_RE = re.compile(r"^\d{6,9}(\.\d{1,2})?$")
_SUBDIV_RE = re.compile(r"^\d{1,3}[A-Za-z]{1,2}\d?$|^\d{1,3}(,\s*\d{1,3}[A-Za-z]?)+$")
_MONEY_RE = re.compile(r"(\d{1,3}(,\d{2})*,\d{3}(\.\d{2})?)|(/-)|₹|ரூ\.?\s*\d")
_EMPTY = {"", "-", "--", "---", "—", "–", "nil", "null", "none", "..."}
SHARE_RE = re.compile(r"\d+\s*[-–]?\s*(?:ல்|இல்)\s*\d+\s*பங்க|பங்கில்|\bshare\b", re.IGNORECASE)
TOTAL_RE = re.compile(r"மொத்தம்|மொத்த\s*$|^total|grand\s*total|கூடுதல்", re.IGNORECASE)
_UNIT_ONLY_RE = re.compile(r"^(in\s*)?(hect\.?|hec\.?|hectares?|acres?|ac\.?|ஹெக்டேர்|ஹெக்\.?|ஏக்கர்|ஏக்\.?|"
                           r"சென்ட்|ha\.?|ரூ\.?|rs\.?)$", re.IGNORECASE)


def norm(s) -> str:
    if s is None:
        return ""
    # the VLM sometimes emits an escaped "\\n" inside a cell instead of a newline
    return _ZW.sub("", unicodedata.normalize("NFC", str(s))).replace("\\n", "\n").strip()


def cell_type(raw) -> str:
    s = tamil_numerals_to_ascii(norm(raw))
    if s.lower() in _EMPTY:
        return "empty"
    if SHARE_RE.search(s):
        return "share"            # "6-ல் 1 பங்கில் (1/6)", "2-ல் 1 பங்கு": an apportionment share
    s1 = s.rstrip(".").strip()
    if _HA_RE.match(s1.replace(" ", "")) or _HA_RE.match(s1) or re.match(r"^0\d{2}\.\d{2}$", s1):
        return "ha"
    if _MONEY_RE.search(s) and re.search(r"\d", s):
        return "money"
    if _SURVEY_RE.match(s):
        return "survey"
    if _DEC_RE.match(s1):
        return "dec"
    if _BIGNUM_RE.match(s1):
        return "bignum"          # ungrouped amount "1740000.00" or a long reference/account number
    if _INT_RE.match(s):
        return "int"
    if _SUBDIV_RE.match(s1):
        return "subdiv"
    letters = len(re.findall(r"[A-Za-z஀-௿]", s))
    if letters >= 3:
        return "text"
    return "other"


def column_type(cells: list) -> tuple[str, float]:
    """Majority content type over non-empty cells, with its share."""
    types = [cell_type(c) for c in cells]
    types = [t for t in types if t != "empty"]
    if not types:
        return "empty", 0.0
    best = max(set(types), key=types.count)
    return best, types.count(best) / len(types)


# ---------------------------------------------------------------------------
# Header keyword matching
# ---------------------------------------------------------------------------
def _despace(s: str) -> str:
    return re.sub(r"[\s​-‍]+", "", s.lower())


def keyword_field(label: str, doc_type: str | None) -> tuple[str | None, str]:
    """(field, how) for a column label; how ∈ exact | fuzzy | none."""
    prof = profile_for(doc_type)
    kws = {**BASE_KEYWORDS, **prof.extra_keywords}
    order = list(prof.extra_keywords) + [f for f in BASE_PRIORITY if f not in prof.extra_keywords]
    order = [f for f in order if f not in prof.drop_fields]
    low = _despace(norm(label))
    if not low:
        return None, "none"
    for f in order:
        if any(_despace(k) in low for k in kws.get(f, [])):
            return f, "exact"
    best, best_sc = None, 0.0
    for f in order:
        for k in kws.get(f, []):
            kd = _despace(k)
            if len(kd) < 4 or not re.search(r"[஀-௿]", kd):
                continue
            sc = fuzz.partial_ratio(kd, low)
            if sc > best_sc:
                best, best_sc = f, sc
    if best_sc >= 85:
        return best, "fuzzy"
    return None, "none"


# ---------------------------------------------------------------------------
# Table preparation: sub-header rows, total rows
# ---------------------------------------------------------------------------
@dataclass
class PreparedTable:
    columns: list[str]
    rows: list[list[str | None]]          # data rows (padded to len(columns))
    row_index: list[int]                  # index of each data row in the VLM table (for bboxes)
    totals: list[tuple[int, list[str | None]]]
    n_vlm_rows: int


def _is_unit_subheader(row: list) -> bool:
    cells = [norm(c) for c in row if norm(c)]
    units = [c for c in cells if _UNIT_ONLY_RE.match(c)]
    nums = [c for c in cells if cell_type(c) in {"ha", "dec", "money", "survey"}]
    return len(units) >= 2 and not nums


_HA_AC_CELL = re.compile(r"^\s*(\d{1,4}\s*[.:]\s*\d{1,2}\s*[.:]\s*\d{1,2})\s*[/|]\s*(\d{1,4}\.\d{1,3})\s*$")


def _split_merged_ha_ac(rows: list[list], cols: list[str], width: int):
    """A column whose cells read "0.60.50 / 1.494" (VLM merged the Hec. and Acre sub-columns,
    golden g18) becomes two columns."""
    for ci in range(width - 1, -1, -1):
        cells = [norm(r[ci]) if ci < len(r) and r[ci] is not None else "" for r in rows]
        filled = [c for c in cells if c]
        hits = [c for c in filled if _HA_AC_CELL.match(c)]
        if not filled or len(hits) < max(1, int(0.6 * len(filled))):
            continue
        new_rows = []
        for r, c in zip(rows, cells):
            r = list(r) + [None] * (width - len(r))
            m = _HA_AC_CELL.match(c)
            a, b = (m.group(1), m.group(2)) if m else (c or None, None)
            new_rows.append(r[:ci] + [a, b] + r[ci + 1:])
        rows = new_rows
        cols = cols[:ci] + ["Hect.", "Acre"] + cols[ci + 1:]
        width += 1
    return rows, cols, width


def prepare_table(table: dict) -> PreparedTable:
    cols = [norm(c) for c in (table.get("columns") or [])]
    raw_rows = [list(r) if isinstance(r, list) else [r] for r in (table.get("rows") or [])]
    width = max([len(cols)] + [len(r) for r in raw_rows]) if (cols or raw_rows) else 0
    cols = cols + [""] * (width - len(cols))
    # a blank column label under a two-level header inherits the previous parent label
    for i in range(1, width):
        if not cols[i] and cols[i - 1] and not _UNIT_ONLY_RE.match(cols[i - 1].split("/")[-1].strip()):
            cols[i] = cols[i - 1].split(" / ")[0]
    raw_rows, cols, width = _split_merged_ha_ac(raw_rows, cols, width)
    rows: list[list] = []
    idx: list[int] = []
    totals: list[tuple[int, list]] = []
    carry: dict[int, str] = {}
    for ri, r in enumerate(raw_rows):
        r = [norm(c) if c is not None else None for c in r] + [None] * (width - len(r))
        if _is_unit_subheader(r):
            for ci, c in enumerate(r):
                if c and _UNIT_ONLY_RE.match(c):
                    cols[ci] = f"{cols[ci]} / {c}" if cols[ci] else c
                elif c:
                    carry[ci] = c           # serial / village printed on the sub-header line
            continue
        if carry:
            for ci, c in carry.items():
                if not r[ci]:
                    r[ci] = c
            carry = {}
        joined = " ".join(c for c in r if c)
        if not joined:
            continue
        if any(c and TOTAL_RE.search(c) for c in r):
            totals.append((ri, r))
            continue
        # a repeated header row inside the table
        if sum(1 for c, h in zip(r, cols) if c and h and _despace(c) == _despace(h)) >= 2:
            continue
        rows.append(r)
        idx.append(ri)
    return PreparedTable(cols, rows, idx, totals, len(raw_rows))


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------
@dataclass
class ColumnMap:
    fields: list[str | None]
    how: list[str]                 # keyword_exact | keyword_fuzzy | content | none
    types: list[str]


def _is_serial(cells: list) -> bool:
    vals = []
    for c in cells:
        s = tamil_numerals_to_ascii(norm(c)).rstrip(".")
        if not s:
            continue
        if not s.isdigit():
            return False
        vals.append(int(s))
    return len(vals) >= 1 and vals == sorted(vals) and vals[0] <= 40 and len(set(vals)) == len(vals)


def map_parcel_columns(pt: PreparedTable, doc_type: str | None) -> ColumnMap:
    prof = profile_for(doc_type)
    n = len(pt.columns)
    col_cells = [[r[i] for r in pt.rows] for i in range(n)]
    types = [column_type(c)[0] for c in col_cells]
    fields: list[str | None] = [None] * n
    how = ["none"] * n
    for i, label in enumerate(pt.columns):
        f, kind = keyword_field(label, doc_type)
        if f is None:
            continue
        compat = COMPAT.get(f, {"ha", "dec", "text", "int", "money", "survey", "subdiv"})
        if f in {"dry_extent", "wet_extent", "other_extent"}:
            compat = {"ha", "dec", "empty"}
        if types[i] in compat or types[i] == "empty":
            fields[i], how[i] = f, f"keyword_{kind}"
    for i, f in enumerate(fields):
        if f == "extent_generic":
            fields[i] = "extent_ha" if types[i] == "ha" else "extent_ac"
    # chitta: the புன்செய்/நன்செய் columns carry the extent; a decimal column to their right is தீர்வை
    for i, f in enumerate(fields):
        if f in {"dry_extent", "wet_extent", "other_extent"}:
            if types[i] == "ha":
                fields[i] = f          # kept; records.py turns it into extent_ha + classification
            elif types[i] == "dec" and i > 0 and fields[i - 1] in {"dry_extent", "wet_extent"}:
                fields[i], how[i] = "assessment_rs", "content"
            elif types[i] == "empty":
                fields[i] = None
    # one field per column: keep the first keyword hit, except two ha columns (7(3): total, acquired)
    seen: dict[str, int] = {}
    for i, f in enumerate(fields):
        if f is None:
            continue
        if f in seen:
            if f == "extent_ha":
                fields[seen[f]] = "extent_ha_total"
                seen[f] = i
            elif f in {"owner", "remarks", "trees", "buildings"}:
                pass
            else:
                fields[i], how[i] = None, "none"
        else:
            seen[f] = i
    for i in range(n):
        if types[i] == "share":
            fields[i], how[i] = "share", "content_share"
    _content_overrides(pt, fields, how, types, col_cells)
    used = {f for f in fields if f}
    # content inference for unmapped columns
    for i in range(n):
        if fields[i] is not None:
            continue
        t = types[i]
        if i == 0 and "serial" not in used and _is_serial(col_cells[i]):
            fields[i], how[i] = "serial", "content"
        elif t == "survey" and "survey_no" not in used:
            fields[i], how[i] = "survey_no", "content"
        elif t == "ha" and "extent_ha" not in used:
            fields[i], how[i] = "extent_ha", "content"
        elif t == "ha" and "extent_ha" in used:
            j = fields.index("extent_ha")
            if j < i:
                fields[j] = "extent_ha_total"
            fields[i], how[i] = "extent_ha", "content"
        elif t == "money" and "amount_rs" not in used:
            fields[i], how[i] = "amount_rs", "content"
        elif t == "dec" and i > 0 and fields[i - 1] == "extent_ha" and "extent_ac" not in used \
                and not _HECT_LABEL.search(pt.columns[i]):
            fields[i], how[i] = ("assessment_rs" if doc_type == "CHITTA" else "extent_ac"), "content"
        elif t == "text" and "owner" not in used and "owner" not in prof.drop_fields:
            avg = sum(len(norm(c)) for c in col_cells[i] if c) / max(1, sum(1 for c in col_cells[i] if c))
            if avg >= 8:
                fields[i], how[i] = "owner", "content"
        elif t in {"int", "subdiv"} and i > 0 and fields[i - 1] == "survey_no" and "sub_div" not in used \
                and types[i - 1] == "int":
            fields[i], how[i] = "sub_div", "content"
        used = {f for f in fields if f}
    resolve_amount_columns(pt, fields, types)
    return ColumnMap(fields, how, types)


_HECT_LABEL = re.compile(r"hect|hec\.|ஹெக்|ஹக்டே|வெக்டர்", re.IGNORECASE)
_ACRE_LABEL = re.compile(r"acre|ஏக்|ஒக்கர்|ac\.", re.IGNORECASE)
_HA_FIELDS = {"extent_ha", "extent_ha_total", "dry_extent", "wet_extent", "other_extent"}


def _content_overrides(pt: PreparedTable, fields: list, how: list, types: list, col_cells: list) -> None:
    """Cell content beats a (possibly invented) header label:
    - a column of H.AA.SS triples is a hectare extent, whatever its label says (continuation
      pages get English headers invented by the VLM: "Extent (Acres)" over triples); a second
      hectare column makes the first the total (7(3): total vs acquired);
    - a hectare-labelled column is never acres;
    - a strictly sequential small-integer "survey" column next to a real survey column is the serial."""
    n = len(fields)
    for i in range(n):
        if types[i] == "ha" and fields[i] not in _HA_FIELDS:
            fields[i], how[i] = "extent_ha", "content_override"
        elif fields[i] == "extent_ac" and _HECT_LABEL.search(pt.columns[i] or "") and \
                not _ACRE_LABEL.search(pt.columns[i] or ""):
            fields[i], how[i] = "extent_ha", "label_hectare"
    ha_cols = [i for i in range(n) if fields[i] == "extent_ha"]
    if len(ha_cols) >= 2:
        # keep the rightmost hectare column as the acquired extent, earlier ones are totals
        for i in ha_cols[:-1]:
            fields[i] = "extent_ha_total"
        for i in ha_cols[:-2]:
            fields[i], how[i] = None, "none"
    # survey values transcribed into another column (held-out: survey numbers in the serial column,
    # survey column left empty): the survey-shaped column becomes the survey column
    sj = fields.index("survey_no") if "survey_no" in fields else None
    sj_fill = (sum(1 for c in col_cells[sj] if norm(c)) / max(1, len(col_cells[sj]))) if sj is not None else 0.0
    if sj is None or sj_fill < 0.2:
        cand = [i for i in range(n) if i != sj and types[i] == "survey" and fields[i] in (None, "serial", "sub_div")]
        if cand:
            i = cand[0]
            if sj is not None:
                fields[sj], how[sj] = None, "content_override"
            fields[i], how[i] = "survey_no", "content_override"
    if "survey_no" in fields:
        si = fields.index("survey_no")
        others = [i for i in range(n) if i != si and types[i] == "survey" and fields[i] in (None, "sub_div")]
        header_serial = any(f == "serial" and h.startswith("keyword") for f, h in zip(fields, how))
        if _is_serial(col_cells[si]) and others and not header_serial:
            fields[others[0]], how[others[0]] = "survey_no", "content_override"
            fields[si], how[si] = ("serial" if "serial" not in fields else None), "content_override"


def map_village_columns(pt: PreparedTable) -> ColumnMap:
    """GO/AS/LPS: each numeric column → (category, unit): category ∈ wet|patta|poramboke|total,
    unit ∈ ha|ac. Headers first; unit from content (triple → ha, decimal → ac); an acre column
    right after a hectare column inherits its category; missing categories from arithmetic."""
    n = len(pt.columns)
    col_cells = [[r[i] for r in pt.rows] for i in range(n)]
    types = [column_type(c)[0] for c in col_cells]
    fields: list[str | None] = [None] * n
    how = ["none"] * n
    cat_kw = [("total", ["மொத்தம்", "மொத்த", "total"]), ("poramboke", ["புறம்போக்கு", "poramboke", "போக்கு"]),
              ("wet", ["நன்செய்", "wet"]), ("patta", ["பட்டா", "patta", "புன்செய்", "dry", "நில உரிமை"])]
    for i, label in enumerate(pt.columns):
        low = _despace(label)
        t = types[i]
        if i <= 1 and t == "int" and _is_serial(col_cells[i]):
            fields[i], how[i] = "serial", "content"
            continue
        if t == "text":
            if "village" not in fields:
                fields[i], how[i] = "village", "content"
            continue
        if t not in {"ha", "dec", "int"}:
            continue
        unit = "ha" if t == "ha" else "ac"
        if re.search(r"hect|ஹெக்", low):
            unit = "ha" if t != "dec" or "ha" == "ha" else unit
        if re.search(r"acre|ஏக்கர்", low) and t != "ha":
            unit = "ac"
        cat = next((c for c, kws in cat_kw if any(_despace(k) in low for k in kws)), None)
        if cat and f"{cat}_{unit}" not in fields:
            fields[i], how[i] = f"{cat}_{unit}", "keyword_exact"
        else:
            fields[i], how[i] = f"?_{unit}", "content"
    # acre column right after a hectare column inherits the category
    for i in range(1, n):
        if fields[i] == "?_ac" and fields[i - 1] and fields[i - 1].endswith("_ha") and not fields[i - 1].startswith("?"):
            cand = fields[i - 1][:-3] + "_ac"
            if cand not in fields:
                fields[i], how[i] = cand, "content"
    # arithmetic over ALL hectare columns (a + b = c per row is stronger evidence than a garbled
    # label): big part = patta, small part = poramboke, sum = total
    from pipeline.normalize.numbers import parse_extent_ha
    ha_cols = [i for i, f in enumerate(fields) if f and f.endswith("_ha") and not f.startswith("wet")]
    vals = {i: [0.0 if norm(r[i]) in {"0", "-", "--"} else parse_extent_ha(r[i]) for r in pt.rows] for i in ha_cols}
    triple = None
    for a in ha_cols:
        for b in ha_cols:
            for c in ha_cols:
                if triple or len({a, b, c}) < 3 or a > b:
                    continue
                ok = [va is not None and vb is not None and vc is not None and abs(va + vb - vc) < 0.002
                      for va, vb, vc in zip(vals[a], vals[b], vals[c])]
                if ok and sum(ok) >= max(1, len(ok) - 1) and any((vals[a][k] or 0) > 0 and (vals[b][k] or 0) > 0
                                                                  for k in range(len(ok))):
                    triple = (a, b, c)
    if triple:
        a, b, c = triple
        big, small = (a, b) if sum(v or 0 for v in vals[a]) >= sum(v or 0 for v in vals[b]) else (b, a)
        for i, f in ((big, "patta_ha"), (small, "poramboke_ha"), (c, "total_ha")):
            fields[i], how[i] = f, "content_arithmetic"
        for i in range(n):          # acre columns re-inherit below
            if fields[i] and fields[i].endswith("_ac") and how[i] == "content":
                fields[i] = "?_ac"
    unk = [i for i, f in enumerate(fields) if f == "?_ha"]
    if unk:
        vals = {i: [parse_extent_ha(r[i]) for r in pt.rows] for i in unk}
        known = {f for f in fields if f}
        assigned = False
        if not assigned:
            order = [c for c in ("patta", "poramboke", "total", "wet") if f"{c}_ha" not in known]
            if len(unk) == 2 and len(order) >= 2 and order[:2] == ["patta", "poramboke"]:
                a, b = unk
                sa, sb = sum(v or 0 for v in vals[a]), sum(v or 0 for v in vals[b])
                pair = ("patta", "poramboke") if sb < sa else ("patta", "total")
                for i, c in zip(unk, pair):
                    if f"{c}_ha" not in fields:
                        fields[i] = f"{c}_ha"
            else:
                for i, c in zip(unk, order):
                    fields[i] = f"{c}_ha"
        for i in unk:
            how[i] = "content"
    for i in range(1, n):
        if fields[i] == "?_ac" and fields[i - 1] and fields[i - 1].endswith("_ha") and not fields[i - 1].startswith("?"):
            cand = fields[i - 1][:-3] + "_ac"
            if cand not in fields:
                fields[i] = cand
    # a single remaining acre column is the total in acres
    rem = [i for i, f in enumerate(fields) if f == "?_ac"]
    if len(rem) == 1 and "total_ac" not in fields:
        fields[rem[0]] = "total_ac"
    fields = [None if (f and f.startswith("?")) else f for f in fields]
    return ColumnMap(fields, how, types)


_TOTAL_LABEL = re.compile(r"மொத்த|total", re.IGNORECASE)
_TREE_LABEL = re.compile(r"மரங்க|மரம்|கட்டமை|கட்டிட|tree|structure", re.IGNORECASE)
_LAND_LABEL = re.compile(r"நிலத்திற்கான|நிலம்|land", re.IGNORECASE)


def resolve_amount_columns(pt: PreparedTable, fields: list, types: list) -> None:
    """Payout / trees tables print several amount columns (land, trees+structures, total, value,
    solatium). amount_rs = the column labelled total (else the rightmost amount column); the others
    keep their meaning as land_amount_rs / tree_amount_rs / amount_other."""
    cand = [i for i in range(len(fields)) if (fields[i] == "amount_rs" or (fields[i] is None and
            types[i] in {"money", "bignum"})) and types[i] in {"money", "bignum", "int", "dec", "empty"}]
    cand = [i for i in cand if _TOTAL_LABEL.search(pt.columns[i] or "") or types[i] in {"money", "bignum"}
            or fields[i] == "amount_rs"]
    if len(cand) < 2:
        return
    tot = [i for i in cand if _TOTAL_LABEL.search(pt.columns[i] or "")]
    main = tot[-1] if tot else cand[-1]
    for i in cand:
        if i == main:
            fields[i] = "amount_rs"
        elif _LAND_LABEL.search(pt.columns[i] or "") and not _TREE_LABEL.search(pt.columns[i] or ""):
            fields[i] = "land_amount_rs"
        elif _TREE_LABEL.search(pt.columns[i] or ""):
            fields[i] = "tree_amount_rs"
        else:
            fields[i] = "amount_other"


def map_owner_amount_columns(pt: PreparedTable) -> ColumnMap:
    n = len(pt.columns)
    col_cells = [[r[i] for r in pt.rows] for i in range(n)]
    types = [column_type(c)[0] for c in col_cells]
    fields: list[str | None] = [None] * n
    how = ["none"] * n
    for i, label in enumerate(pt.columns):
        f, kind = keyword_field(label, "FORM_F")
        if f in {"amount_rs", "owner", "serial"} and (types[i] in COMPAT[f] or types[i] == "empty") and f not in fields:
            fields[i], how[i] = f, f"keyword_{kind}"
    for i in range(n):
        if fields[i]:
            continue
        if types[i] == "money" and "amount_rs" not in fields:
            fields[i], how[i] = "amount_rs", "content"
        elif types[i] == "text" and "owner" not in fields:
            fields[i], how[i] = "owner", "content"
        elif i == 0 and _is_serial(col_cells[i]) and "serial" not in fields:
            fields[i], how[i] = "serial", "content"
    resolve_amount_columns(pt, fields, types)
    return ColumnMap(fields, how, types)


def table_signature(pt: PreparedTable) -> str:
    """Content family of one table regardless of the page's doc type: parcel if it has a survey
    or hectare column, else owner_amount if text+money, else kv (label/value), else other."""
    n = len(pt.columns)
    types = [column_type([r[i] for r in pt.rows])[0] for i in range(n)]
    labels = " ".join(pt.columns).lower()
    if "survey" in types or ("ha" in types and "text" not in types[:1]):
        return "parcel"
    if "share" in types and ("text" in types) and any(t in types for t in ("money", "bignum", "int")):
        return "owner_amount"          # apportionment table: name / share / amount
    if re.search(r"வாரிசு|heir|உறவுமுறை|relation", labels, re.I):
        return "heirs"
    if ("money" in types or "bignum" in types) and "text" in types:
        return "owner_amount"          # content beats an invented "Survey Number" label (dev2 e07)
    if re.search(r"புல\s*எண்|survey", labels):
        return "parcel"
    if "ha" in types:
        return "village_or_parcel"
    if "money" in types and "text" in types:
        return "owner_amount"
    if n == 2 and types[0] == "text":
        return "kv"
    return "other"
