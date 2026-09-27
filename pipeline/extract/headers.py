"""Header fields from text-layer / Tesseract lines, voted against the VLM header (D-010 step 2).

Tesseract reads Tamil prose and headers well (bake-off: dates 100%, units 75%); the VLM header
is a second vote. Every field keeps both votes, a `conflict` flag, the raw text and the OCR
line bbox as evidence. Per-doc-type rules decide whether a proceedings number printed on the
page is the page's own `doc_no` or a reference (`ref_doc_no`: the award behind a Form F, the
original notice behind an errata — golden README).
"""
from __future__ import annotations

import re
import unicodedata

from pipeline.normalize.dates import parse_date
from pipeline.normalize.numbers import parse_amount, tamil_numerals_to_ascii
from pipeline.normalize.village import VILLAGES, find_village_in_text, normalise_village

_ZW = re.compile(r"[​-‍﻿]")
ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8", "IX": "9",
         "X": "10"}

UNIT_RE = re.compile(r"(?:அலகு|Unit)\s*[-–:.]?\s*(\d{1,2}|X|IX|VIII|VII|VI|IV|V|III|II|I)\b", re.IGNORECASE)
BLOCK_RE = re.compile(r"(?:பிளாக்|தொகுதி|தொருதி|Block)\s*[-–:.]?\s*(\d{1,2})\b", re.IGNORECASE)
DATE_TOKEN = r"(\d{0,2}\s*[./\-:]\s*\d{1,2}\s*[./\-]\s*\d{2,4}|\s*[./]\s*\d{4})"
LABELLED_DATE_RE = re.compile(r"(?:நாள்|றாள்|Date|Dated|Dt\.?)\s*[:.;]?\s*" + DATE_TOKEN, re.IGNORECASE)
HANDOVER_RE = re.compile(r"(?:Date\s*of\s*Hand(?:l)?ing\s*over|ஒப்படைக்கப்பட்ட\s*நாள்|taking\s*over)\s*[:.]?\s*"
                         + DATE_TOKEN, re.IGNORECASE)
DOC_NO_RES = [
    ("treasury", re.compile(r"Treasury\s*Reference\s*(?:Number|No\.?)\s*[:|]?\s*(\d{10,20})", re.IGNORECASE)),
    ("go", re.compile(r"(G\.?\s*O\.?\s*\(?\s*Ms\.?\s*\)?\s*\.?\s*No\.?\s*\d+)", re.IGNORECASE)),
    ("go_ta", re.compile(r"(?:அரசாணை\s*)?\(?\s*நிலை\s*\)?\s*எண்\s*[.:]?\s*(\d{1,4})\b")),
    ("roc", re.compile(r"Roc\.?\s*No\.?\s*[:.]?\s*([A-Za-z]\d+\s*/\s*[\w\-]+(?:\s*/\s*[\w\-()]+)*)", re.IGNORECASE)),
    ("lr", re.compile(r"Lr\.?\s*No\.?\s*[:.]?\s*([A-Za-z][\w.\s]*?/\s*[\w]+\s*/\s*[\w]+\s*/\s*\d{4})", re.IGNORECASE)),
    ("nk", re.compile(r"((?:ந|&)\s*\.?\s*க\s*\.?\s*(?:எண்|என்|எனர்)?\s*[:.]?\s*[அ௮&]?\s*\d+\s*[/,]\s*[\w\-/()\s]*?\d{4}"
                      r"(?:\s*-\s*\(\d+\))?)")),
    ("award", re.compile(r"\b(A\s*\d+\s*/\s*\d+\s*/\s*[\w\-]+\s*/\s*\d{4}(?:\s*-\s*\(\d+\))?)", re.IGNORECASE)),
]
GAZETTE_RE = re.compile(r"(?:அரசிதழ்|Gazette)[^0-9]{0,40}?(?:எண்|எனர்|எளர்|No\.?|Issue\s*No\.?)\s*[.:]?\s*(\d{2,4})"
                        r"[^0-9]{0,30}?" + DATE_TOKEN, re.IGNORECASE)
RATE_RES = [
    re.compile(r"ஏக்கருக்கு\s*(?:ஒன்றுக்கு\s*)?ரூ\.?\s*([\d,]{5,})"),
    re.compile(r"ஏக்கர்\s*ஒன்றுக்கு\s*ரூ\.?\s*([\d,]{5,})"),
    re.compile(r"ஒரு\s*ஏக்கருக்கு\s*ரூ\.?\s*([\d,]{5,})"),
    re.compile(r"Rs\.?\s*([\d,]{5,})\s*/?-?\s*(?:per|/)\s*acre", re.IGNORECASE),
    re.compile(r"per\s*acre[^0-9]{0,20}Rs\.?\s*([\d,]{5,})", re.IGNORECASE),
]
# "<village> கிராமம்" or "Village : <village>"
LABELLED_VILLAGE_RE = re.compile(r"(?:கிராமம்|Village)\s*[:：]?\s*([^\n,.;|:]{3,40})", re.IGNORECASE)
# the scheme's own name contains "Allikulam": scrub it before looking for villages
SCHEME_NAME_RE = re.compile(r"(அல்லி(?:க்)?குளம்\s*(?:எண்ணெய்|திட்ட|தொழிற்)|Allikulam\s*(?:Oil|Scheme|scheme|SIPCOT|Industr)|"
                            r"LA/TUT/Allikulam|SIPCOT\s*Allikulam|ALLIKULAM\s*SCHEME)", re.IGNORECASE)
SUFFIX_VILLAGE_RE = re.compile(r"([஀-௿]{4,}(?:\s[஀-௿]{3,})?)\s*கிராமம்")
EN_VILLAGE_RE = re.compile(r"\b([A-Z][A-Za-z ]{4,30}?)\s+village", re.IGNORECASE)

REF_DOC_TYPES = {"FORM_F", "SEC32_ERRATA"}
TREASURY_RE = re.compile(r"Treasury\s*Reference|Payment\s*Advice|Beneficiary\s*Details", re.IGNORECASE)
TREASURY_DATE_RE = re.compile(r"Treasury\s*Reference\s*Date\s*[:|]?\s*(\d{1,2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{4})",
                              re.IGNORECASE)


def clean_text(s: str | None) -> str:
    return _ZW.sub("", unicodedata.normalize("NFC", s or ""))


def _hf(value, raw=None, source=None, bbox=None, precision=None, votes=None, conflict=False,
        confidence=0.0, method="ocr_line") -> dict:
    d = {"value": value, "raw": raw, "source": source, "header_source": "page", "conflict": conflict,
         "votes": votes or {}}
    if precision:
        d["precision"] = precision
    d["evidence"] = {"bbox": bbox or [0.0, 0.0, 1.0, 1.0], "bbox_method": method if bbox else "page",
                     "source_engine": source or "none", "confidence": round(confidence, 3),
                     "raw_text": raw}
    return d


def _unit(v: str | None) -> str | None:
    if not v:
        return None
    v = tamil_numerals_to_ascii(v).strip().upper()
    m = re.search(r"\b(\d{1,2})\b", v)
    if m:
        return str(int(m.group(1)))
    m = re.search(r"\b(X|IX|VIII|VII|VI|IV|V|III|II|I)\b", v)
    return ROMAN.get(m.group(1)) if m else None


def _canon_docno(s: str) -> str:
    s = tamil_numerals_to_ascii(s)
    s = re.sub(r"\s+", "", s).replace("௮", "அ")
    s = re.sub(r"^(?:&|ந)\.?க\.?(?:எண்|என்|எனர்)?[:.]?", "ந.க.", s)
    return s.strip(".,;:")


class Lines:
    """Text lines with optional page-fraction bboxes (Tesseract or text layer)."""

    def __init__(self, lines: list[dict] | None, text: str | None = None):
        if lines:
            self.lines = [{"text": clean_text(ln["text"]), "bbox": ln.get("bbox")} for ln in lines]
        else:
            self.lines = [{"text": clean_text(t), "bbox": None} for t in (text or "").split("\n")]
        self.text = "\n".join(ln["text"] for ln in self.lines)

    def find(self, rx: re.Pattern, start_frac: float = 0.0, end_frac: float = 1.0):
        n = len(self.lines)
        lo, hi = int(n * start_frac), max(1, int(round(n * end_frac)))
        for ln in self.lines[lo:hi]:
            m = rx.search(ln["text"])
            if m:
                return m, ln
        return None, None

    def find_all(self, rx: re.Pattern):
        for ln in self.lines:
            for m in rx.finditer(ln["text"]):
                yield m, ln


MULTI = "__multiple__"


def _village_votes(lines: Lines) -> tuple[str | None, str | None, list | None]:
    """Labelled village ("Village : X", "கிராமம் : X", "கிராமம் X") wins; otherwise the villages
    named with a கிராமம்/village suffix. A page naming two or more different scheme villages
    without a label (GO lists, SLPNC notes, boundary cells) gets no village — never guess."""
    scrubbed = [{"text": SCHEME_NAME_RE.sub(" ", ln["text"]), "bbox": ln["bbox"]} for ln in lines.lines]
    for ln in scrubbed:
        for m in LABELLED_VILLAGE_RE.finditer(ln["text"]):
            cap = re.split(r"\s(?:அலகு|பிளாக்|தொகுதி|வட்டம்|Unit|Block|Taluk)(?=[\s:：.\-]|$)", m.group(1), flags=re.I)[0].strip()
            if re.search(r"\band\b|மற்றும்|&", cap, re.I) and len(
                    {normalise_village(x) for x in re.split(r"\band\b|மற்றும்|&", cap, flags=re.I)} - {None}) >= 2:
                return MULTI, m.group(0), ln["bbox"]   # "Village: A and B" — no single village
            v = normalise_village(cap) or find_village_in_text(cap[:25])
            if v:
                return v, m.group(0), ln["bbox"]
    found: list[tuple[str, str, list | None]] = []
    for ln in scrubbed:
        for rx in (SUFFIX_VILLAGE_RE, EN_VILLAGE_RE):
            for m in rx.finditer(ln["text"]):
                v = normalise_village(m.group(1)) or find_village_in_text(m.group(1))
                if v:
                    found.append((v, m.group(0), ln["bbox"]))
    alltext = "\n".join(ln["text"] for ln in scrubbed)
    mentioned = {v for v in VILLAGES if find_village_in_text(v) and _mentions(alltext, v)}
    if not found:
        return None, None, None
    if len({f[0] for f in found} | mentioned) > 1:
        return MULTI, found[0][1], found[0][2]
    return found[0]


def _mentions(text: str, village: str) -> bool:
    from pipeline.normalize.village import _alias_table, fold
    ft = fold(text)
    return any(len(a) >= 5 and a in ft for a, c in _alias_table().items() if c == village)


def carry_headers(header: dict, previous: dict | None) -> dict:
    """Continuation pages (award p3 after p2) often print no village/unit/block: carry them from
    the previous page of the same document, marked source=carried."""
    if not previous:
        return header
    for f in ("village", "unit_no", "block_no"):
        cur, prev = header.get(f) or {}, previous.get(f) or {}
        if cur.get("value") is None and prev.get("value") is not None:
            # D-040: kept in the output, tagged, and excluded from header scoring
            header[f] = {**prev, "source": "carried", "header_source": "carried", "conflict": False}
    return header


def extract_headers(lines: Lines, vlm_header: dict | None, doc_type: str | None,
                    text_source: str = "tesseract") -> dict:
    """Return {field: header_field_dict}. `text_source` is `text_layer` or `tesseract`."""
    vh = {k: (clean_text(v) if isinstance(v, str) else v) for k, v in (vlm_header or {}).items()}
    out: dict[str, dict] = {}
    txt = lines.text

    # village -------------------------------------------------------------------------------
    tv, traw, tbox = _village_votes(lines)
    multi = tv == MULTI
    tv = None if multi else tv
    vv = normalise_village(vh.get("village")) if vh.get("village") else None
    if vv is None and vh.get("village"):
        vv = find_village_in_text(vh.get("village"))
    # the page itself names several villages: no single village, whatever the VLM guessed
    value = None if multi else (tv or vv)
    out["village"] = _hf(value, traw or vh.get("village"), text_source if tv else ("vlm" if vv else None), tbox,
                         votes={text_source: tv, "vlm": vv}, conflict=bool(tv and vv and tv != vv),
                         confidence=0.9 if tv and (vv in (None, tv)) else 0.6 if value else 0.0)

    # unit / block --------------------------------------------------------------------------
    for fname, rx, vkey in (("unit_no", UNIT_RE, "unit_no"), ("block_no", BLOCK_RE, "block_no")):
        m, ln = lines.find(rx)
        tval = _unit(m.group(1)) if m else None
        vval = _unit(vh.get(vkey)) if vh.get(vkey) and re.search(
            r"(அலகு|unit|பிளாக்|block|தொகுதி|^\s*[\dIVX]+\s*$)", vh.get(vkey) or "", re.I) else None
        value = tval or vval
        out[fname] = _hf(value, m.group(0) if m else vh.get(vkey), text_source if tval else ("vlm" if vval else None),
                         ln["bbox"] if m else None, votes={text_source: tval, "vlm": vval},
                         conflict=bool(tval and vval and tval != vval),
                         confidence=0.9 if tval and vval in (None, tval) else 0.6 if value else 0.0)

    # document number / reference number ----------------------------------------------------
    docno = docraw = None
    docbox = None
    kind_found = None
    best = None
    for kind, rx in DOC_NO_RES:
        for li, ln in enumerate(lines.lines):
            m = rx.search(ln["text"])
            if m:
                if best is None or li < best[0]:
                    best = (li, kind, m, ln)
                break
    # award body pages cite G.O.s and proceedings in prose: only a number in the first lines is
    # the page's own (golden: every award body page has doc_no = null)
    if best and doc_type in {"AWARD_7_2", "AWARD_7_3"} and best[0] > 3:
        best = None
    if best:
        _, kind_found, m, ln = best
        docno = f"G.O.(Ms) No.{m.group(1)}" if kind_found == "go_ta" else \
            (m.group(1) if kind_found == "treasury" else _canon_docno(m.group(1)))
        docraw, docbox = m.group(0), ln["bbox"]
    vdoc = _canon_docno(vh["doc_no"]) if vh.get("doc_no") and re.search(r"\d+\s*/", vh["doc_no"]) else None
    is_ref = doc_type in REF_DOC_TYPES or (kind_found == "award" and doc_type not in {"FORM_E", "LDR"})
    if doc_type in {"AWARD_7_2", "AWARD_7_3"} and kind_found in {"award", None}:
        is_ref = True     # award body pages cite the proceedings only as references
    target = "ref_doc_no" if is_ref else "doc_no"
    other = "doc_no" if is_ref else "ref_doc_no"
    value = docno or (vdoc if not is_ref or doc_type in REF_DOC_TYPES else None)
    out[target] = _hf(value, docraw or vh.get("doc_no"), text_source if docno else ("vlm" if value else None), docbox,
                      votes={text_source: docno, "vlm": vdoc}, confidence=0.85 if docno else 0.5 if value else 0.0)
    out[other] = _hf(None)

    # dates ---------------------------------------------------------------------------------
    date_val = prec = draw = None
    dbox = None
    # the page's own date sits next to its number or in the first lines of the page
    if docraw:
        for ln in lines.lines:
            if docraw in ln["text"]:
                m = LABELLED_DATE_RE.search(ln["text"][ln["text"].find(docraw):]) or re.search(
                    DATE_TOKEN, ln["text"][ln["text"].find(docraw) + len(docraw):])
                if m:
                    date_val, prec = parse_date(m.group(1))
                    draw, dbox = m.group(0), ln["bbox"]
                break
    treasury = bool(TREASURY_RE.search(txt))
    if treasury and date_val is None:
        m, ln = lines.find(TREASURY_DATE_RE)
        if m is None:
            m = TREASURY_DATE_RE.search(txt.replace("\n", " "))
            ln = None
        if m:
            date_val, prec = parse_date(m.group(1))
            draw, dbox = m.group(0), (ln or {}).get("bbox")
    if date_val is None:
        m, ln = lines.find(LABELLED_DATE_RE, 0.0, 0.25 if len(lines.lines) > 12 else 0.5)
        if m:
            date_val, prec = parse_date(m.group(1))
            draw, dbox = m.group(0), ln["bbox"]
            if date_val is None:
                # a blank-day date (".02.2025") parses with month precision
                date_val, prec = parse_date(m.group(1).strip(" :"))
    hm, hln = lines.find(HANDOVER_RE)
    handover = parse_date(hm.group(1)) if hm else (None, None)
    if date_val is None and handover[0]:
        date_val, prec, draw, dbox = handover[0], handover[1], hm.group(0), hln["bbox"]
    vd, vprec = parse_date(vh.get("doc_date")) if vh.get("doc_date") else (None, None)
    dtarget = "ref_doc_date" if is_ref else "doc_date"
    dvalue, dprec, dsrc = (date_val, prec, text_source) if date_val else (vd, vprec, "vlm" if vd else None)
    if is_ref and doc_type in {"AWARD_7_2", "AWARD_7_3"}:
        dvalue = dprec = dsrc = None
    out[dtarget] = _hf(dvalue, draw or vh.get("doc_date"), dsrc, dbox, precision=dprec,
                       votes={text_source: date_val, "vlm": vd}, conflict=bool(date_val and vd and date_val != vd),
                       confidence=0.9 if date_val and prec == "day" else 0.5 if dvalue else 0.0)
    out["doc_date" if is_ref else "ref_doc_date"] = out.get("doc_date" if is_ref else "ref_doc_date") or _hf(None)
    out["handover_date"] = _hf(handover[0], hm.group(0) if hm else None, text_source if hm else None,
                               hln["bbox"] if hm else None, precision=handover[1],
                               confidence=0.9 if handover[0] else 0.0)

    if treasury:
        # treasury payment advices print no village / unit / block (a "Unit" in the drawing
        # officer's address is not the page's unit)
        for f in ("village", "unit_no", "block_no"):
            out[f] = _hf(None, votes=out[f].get("votes"))
    # gazette, rate ---------------------------------------------------------------------------
    gm, gln = lines.find(GAZETTE_RE)
    if gm is None:
        gm = GAZETTE_RE.search(txt.replace("\n", " "))
        gln = None
    gdate = parse_date(gm.group(2)) if gm else (None, None)
    out["gazette_no"] = _hf(gm.group(1) if gm else None, gm.group(0) if gm else None, text_source if gm else None,
                            gln["bbox"] if gln else None, confidence=0.7 if gm else 0.0)
    out["gazette_date"] = _hf(gdate[0], gm.group(0) if gm else None, text_source if gm else None,
                              gln["bbox"] if gln else None, precision=gdate[1], confidence=0.7 if gdate[0] else 0.0)
    rate = rraw = None
    rbox = None
    for rx in RATE_RES:
        m, ln = lines.find(rx)
        if m is None:
            m = rx.search(txt.replace("\n", " "))
            ln = None
        if m:
            rate, rraw, rbox = parse_amount(m.group(1)), m.group(0), (ln or {}).get("bbox")
            break
    out["rate_per_acre"] = _hf(str(rate) if rate else None, rraw, text_source if rate else None, rbox,
                               confidence=0.8 if rate else 0.0)
    return out
