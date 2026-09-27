"""Page-level doc-type resolution and doc-type → mapping family.

The P1 classifier works per document from its first pages; award bodies, Form F and Form E pages
are often mis-typed (on the golden pages 7(2)/7(3) awards came back SEC32_NOTICE, Form F
SEC31_GAZETTE, Form E LDR). Extraction therefore re-resolves the type **per page** from the
page's own title/text cues, and only falls back to the document's classified_type / folder prior.
"""
from __future__ import annotations

import re
import unicodedata

FAMILY_OF: dict[str, str] = {
    "AWARD_7_2": "parcel", "AWARD_7_3": "parcel", "SEC32_NOTICE": "parcel", "SEC32_ERRATA": "parcel",
    "SEC31_GAZETTE": "parcel", "LDR": "parcel", "POSSESSION_CERT": "parcel", "CHITTA": "parcel",
    "FORM_E": "parcel", "EXEMPTION_GO": "parcel", "EXEMPTION_PROPOSAL": "parcel",
    "CALCULATION_SHEET": "parcel", "CLASSIFICATION_ORDER": "parcel",
    "GO": "village_totals", "AS": "village_totals", "AS_PROPOSAL": "village_totals", "LPS": "village_totals",
    "FORM_F": "owner_amount", "DISBURSEMENT": "owner_amount",
    "COURT_DEPOSIT": "instrument", "BANK_INSTRUMENT": "instrument",
    "DLPNC": "rate", "SLPNC": "rate", "LAND_VALUE": "rate", "FUNDS": "rate",
}
PUBLIC_TYPES = {"GO", "AS", "AS_PROPOSAL", "LPS", "SEC31_GAZETTE"}

# (doc_type, patterns) — checked in order against title + first part of page text.
_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("CHITTA", (r"நில\s*உரிமை\s*விபரங்கள்", r"eservices", r"சிட்டா", r"Zonal\s*Deputy\s*Tahsildar",
                r"இணைய\s*சேவை")),
    ("LDR", (r"LAND\s*DELIVERY", r"DELIVERY\s*CERTIFICATE", r"Handing\s*over", r"Handling\s*Over",
             r"நில\s*ஒப்படைப்பு", r"ஒப்படைப்புச்\s*சான்று", r"POSSESSION\s*CERTIFICATE")),
    ("FORM_F", (r"FORM\s*[-–]?\s*F\b", r"apportion", r"படிவம்\s*[-–]?\s*(?:F|எப்|எஃப்)")),
    ("FORM_E", (r"படிவம்\s*[-–]?\s*(?:E|இ)(?![஀-௿A-Za-z])", r"FORM\s*[-–]?\s*E\b", r"30\s*நாட்களுக்குள்",
                r"சுவாதீனம்\s*ஒப்படை")),
    ("SEC32_ERRATA", (r"திருத்திய\s*அறிவிப்பு", r"ERRATA", r"என\s*படிக்கப்பட", r"என்று\s*படிக்க",
                      r"ஏற்கனவே\s*வெளியிடப்பட்ட")),
    ("SLPNC", (r"STATE\s*LEVEL\s*PRIVATE\s*NEGOTIATION", r"மாநில\s*அளவிலான")),
    ("DLPNC", (r"DISTRICT\s*LEVEL\s*PRIVATE\s*NEGOTIATION", r"PRICE\s*NEGOTIATION",
               r"LAND\s*VALUE")),
    ("COURT_DEPOSIT", (r"DISTRICT\s*JUDGE",)),
    ("BANK_INSTRUMENT", (r"DEMAND\s*DRAFT", r"On\s*Demand", r"\bDD\s*No")),
    ("AS_PROPOSAL", (r"முன்மொழிவு", r"சமர்ப்பிக்கிறேன்")),
    ("AS", (r"Administrative\s*Sanction", r"நிர்வாக\s*அனுமதி")),
    ("GO", (r"G\.?\s*O\.?\s*\(?\s*Ms\s*\.?\)?\s*No", r"அரசாணை")),
    ("AWARD_7_3", (r"7\s*\(\s*3\s*\)",)),
    ("AWARD_7_2", (r"7\s*\(\s*2\s*\)", r"ஒப்புதல்\s*தீர்ப்பு", r"இழப்பீடு\s*நிர்ணய", r"இழப்பீட்டுத்\s*தொகை")),
    ("SEC32_NOTICE", (r"படிவம்\s*[-–]?\s*(?:பி|B)(?![஀-௿A-Za-z])", r"FORM\s*[-–]?\s*B\b", r"3\s*\(\s*2\s*\)")),
]
_CUE_RE = [(t, [re.compile(p, re.IGNORECASE) for p in pats]) for t, pats in _CUES]
TEXT_HEAD_CHARS = 700
# body-text cues (first TEXT_HEAD_CHARS of OCR text): only headings that are not cited in prose
_TEXT_ONLY: dict[str, tuple[str, ...]] = {
    "CHITTA": _CUES[0][1],
    "LDR": (r"LAND\s*DELIVERY", r"DELIVERY\s*CERTIFICATE", r"Date\s*of\s*Hand(?:l)?ing\s*over"),
    "FORM_F": (r"FORM\s*[-–]?\s*F\b", r"apportioned\s*to\s*the\s*land\s*owners"),
    "FORM_E": (r"படிவம்\s*[-–]?\s*(?:E|இ)(?![஀-௿A-Za-z])", r"FORM\s*[-–]?\s*E\b"),
    "SEC32_ERRATA": (r"திருத்திய\s*அறிவிப்பு", r"\bERRATA\b"),
    "SLPNC": (r"NOTES\s*FOR\s*APPROVAL.{0,80}STATE\s*LEVEL",),
    "DLPNC": (r"^.{0,200}DISTRICT\s*LEVEL\s*PRIVATE\s*NEGOTIATION",),
    "COURT_DEPOSIT": (r"DISTRICT\s*JUDGE",),
    "BANK_INSTRUMENT": (r"DEMAND\s*DRAFT", r"On\s*Demand"),
    "AS": (r"Sub\s*:.{0,120}Administrative\s*Sanction",),
    "SEC32_NOTICE": (r"படிவம்\s*[-–]?\s*(?:பி|B)(?![஀-௿A-Za-z])",),
}
_TEXT_CUE_RE = {t: [re.compile(p, re.IGNORECASE | re.S) for p in pats] for t, pats in _TEXT_ONLY.items()}


def _clean(s: str | None) -> str:
    return re.sub(r"[​-‍]", "", unicodedata.normalize("NFC", s or ""))


def resolve_doc_type(title: str | None, page_text: str | None, columns: list[str] | None,
                     classified_type: str | None, folder_prior: str | None = None) -> tuple[str | None, str]:
    """Return (doc_type, source) where source ∈ title | text | columns | classifier | folder."""
    t = _clean(title)
    head = _clean(page_text)[:TEXT_HEAD_CHARS]
    for src, blob in (("title", t), ("text", head)):
        if not blob:
            continue
        for dt, rxs in _CUE_RE:
            # the 3(2)/7(2) section numbers, "G.O. No." and committee names are cited in almost
            # every document's prose (award preambles, D-030); from body text only trust the
            # unambiguous cues in _TEXT_ONLY
            if src == "text":
                rxs = _TEXT_CUE_RE.get(dt, [])
            if any(rx.search(blob) for rx in rxs):
                return dt, src
    cols = _clean(" | ".join(c or "" for c in (columns or [])))
    if cols:
        if re.search(r"(உட்பிரிவு|உத்திரிவு).*(புன்செய்|பஞ்சேய்|நன்செய்|நஞ்சேய்)", cols):
            return "CHITTA", "columns"
        if re.search(r"apportion|compensation\s*amount", cols, re.I) and re.search(r"owner", cols, re.I):
            return "FORM_F", "columns"
        if re.search(r"North|East|South|West|வடக்கு|கிழக்கு", cols, re.I):
            return "LDR", "columns"
        if re.search(r"பிளாட்|plot|செண்ட்|சென்ட்|cent", cols, re.I):
            return "FORM_E", "columns"
    if classified_type:
        return classified_type, "classifier"
    if folder_prior:
        return folder_prior, "folder"
    return None, "none"


def family_of(doc_type: str | None) -> str:
    return FAMILY_OF.get(doc_type or "", "parcel")


def privacy_tier_of(doc_type: str | None) -> str:
    """GO/AS/LPS village totals are PUBLIC; everything else with owners/amounts is PII (D-005)."""
    return "PUBLIC" if doc_type in PUBLIC_TYPES else "PII"
