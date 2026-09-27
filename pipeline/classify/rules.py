"""Doc-type rule engine (T1.6): keyword/regex rules over the first two pages, in priority
order (most specific first). Folder label is a prior only (land-domain-knowledge §3) -- it can
nudge a close call but a strong content match always overrides it (the "Amount Disbursed"
folder is 1/17 true DISBURSEMENT in the golden set; the rest are awards or court deposits).

Each `Rule.match` returns a confidence in [0, 1] or None (no match). Rules are tried in order;
the caller (classify_document in .run) keeps the *highest*-confidence match across all rules,
not just the first, so a later rule with stronger evidence still wins over an earlier weak one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LETTER_SALUTATION_RE = re.compile(r"\b(Sir|Madam|Dear Sir|To,|From\s*:)\b", re.IGNORECASE)


@dataclass
class RuleResult:
    doc_type: str
    confidence: float
    evidence: dict = field(default_factory=dict)


@dataclass
class Rule:
    doc_type: str
    patterns: tuple[re.Pattern, ...]         # any() of these must match
    all_patterns: tuple[re.Pattern, ...] = ()  # all() of these must also match
    exclude: tuple[re.Pattern, ...] = ()       # none of these may match
    confidence: float = 0.85
    min_chars: int = 0
    max_chars: int | None = None
    extra_fields: dict | None = None           # e.g. {"payee_kind": "court"}
    heading_chars: int | None = None           # only search the first N chars (the doc's own
    #   title/heading), not the whole first-2-pages text -- for loose section-number patterns
    #   that would otherwise fire on a procedural recital deep in the body (e.g. an award
    #   reciting the s.3(2) notice history is not itself a s.3(2) notice)

    def match(self, text: str, char_count: int) -> RuleResult | None:
        if char_count < self.min_chars:
            return None
        if self.max_chars is not None and char_count > self.max_chars:
            return None
        window = text[: self.heading_chars] if self.heading_chars else text
        hits = [p.pattern for p in self.patterns if p.search(window)]
        if self.patterns and not hits:
            return None
        if self.all_patterns and not all(p.search(window) for p in self.all_patterns):
            return None
        if any(p.search(window) for p in self.exclude):
            return None
        evidence = {"rule": self.doc_type, "matched": hits[:3]}
        if self.extra_fields:
            evidence.update(self.extra_fields)
        return RuleResult(self.doc_type, self.confidence, evidence)


def _re(*pats: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in pats)


RULES: tuple[Rule, ...] = (
    # -- GO / AS / AS_PROPOSAL / LPS (stage GO_AS_LPS) ----------------------------------------
    # excluded: "Exemption" -- an exemption G.O. shares the same ABSTRACT/ORDER letterhead but
    # must be classified EXEMPTION_GO, not the generic scheme-sanction GO (checked below).
    Rule("GO", _re(r"GOVERNMENT\s+OF\s+TAMIL\s+NADU"), all_patterns=_re(r"\bABSTRACT\b", r"\bORDER\b"),
         exclude=(re.compile(r"Exemption", re.IGNORECASE),), confidence=0.9),
    Rule("GO", _re(r"G\.?O\.?\s*\(?Ms\.?\)?\s*No\.?\s*\d+"), all_patterns=_re(r"Industries|SIPCOT"),
         exclude=(re.compile(r"Exemption", re.IGNORECASE),), confidence=0.75),
    Rule("AS", _re(r"Administrative\s+Sanction"), all_patterns=_re(r"Lr\.?\s*No\.?\s*LA"),
         confidence=0.9),
    Rule("AS_PROPOSAL", _re(r"சமர்ப்பிக்கின்றேன்|சமர்ப்பிக்கிறேன்|பரிந்துரைக்கின்றேன்"),
         confidence=0.7),
    Rule("LPS", _re(r"Land\s+Plan\s+Schedule|நிலத்திட்ட\s*அட்டவணை"), confidence=0.85),

    # -- 3(1) gazette / 3(2) notice+errata (stage SEC_3_1 / SEC_3_2) --------------------------
    Rule("SEC31_GAZETTE",
         _re(r"GOVERNMENT\s+GAZETTE", r"தமிழ்நாடு\s+அரசு\s+வர்த்தமானி", r"PUBLISHED\s+BY\s+AUTHORITY"),
         confidence=0.9),
    Rule("SEC32_ERRATA", _re(r"\bErrata\b|Corrigendum|பிழை\s*திருத்தம்"), confidence=0.85),
    Rule("SEC32_NOTICE", _re(r"Form[\s-]*A\b", r"படிவம்[\s-]*(?:அ|ஏ)"), confidence=0.75),
    # loose bare "3(2)" -- only trusted in the document's own heading (first 400 chars): an
    # AWARD document routinely *recites* the earlier s.3(2) notice deep in its preamble, which
    # must not itself be read as a s.3(2) notice (found in T1.6 evaluation)
    Rule("SEC32_NOTICE", _re(r"பிரிவு\s*3\s*\(\s*2\s*\)", r"3\s*\(\s*2\s*\)"),
         confidence=0.62, heading_chars=400),

    # -- exemption (stage EXEMPTION) -----------------------------------------------------------
    # "Exemption" and the G.O. number must be near each other (same clause): a proposal letter
    # can legitimately *reference* an unrelated exemption letter several lines away from its own
    # G.O. citation (found in T1.6 evaluation: an SLPNC proposal citing "GOMs.No.100" and, in a
    # separate reference-list line, an unrelated "...exemption'2021" letter).
    Rule("EXEMPTION_GO",
         _re(r"Exemption.{0,100}G\.?O\.?\s*\(?Ms\.?\)?\s*No",
             r"G\.?O\.?\s*\(?Ms\.?\)?\s*No.{0,100}Exemption"),
         confidence=0.85),
    Rule("EXEMPTION_PROPOSAL",
         _re(r"Exemption"), all_patterns=_re(r"recommend|remarks|objection|பரிந்துரை|ஆட்சேபனை"),
         confidence=0.75),

    # -- price negotiation (stage PRICE_NEGOTIATION); SLPNC wins over DLPNC when both appear,
    # per the golden-set finding that "DLPNC" folder files are almost all SLPNC notes that only
    # *cite* an earlier DLPNC meeting ----------------------------------------------------------
    Rule("SLPNC", _re(r"State\s*Level\s*Price\s*Negotiation|\bSLPNC\b|மாநில.{0,10}விலை"),
         confidence=0.85, extra_fields={"committee_level": "state"}),
    Rule("DLPNC", _re(r"District\s*Level\s*Price\s*Negotiation|\bDLPNC\b|மாவட்ட.{0,10}விலை"),
         confidence=0.75, extra_fields={"committee_level": "district"}),
    Rule("LAND_VALUE", _re(r"Land\s*Value\s*Fixation|நில\s*விலை\s*நிர்ணய"), confidence=0.65),
    Rule("FUNDS", _re(r"Funds[\s-]*Allocated|நிதி\s*ஒதுக்கீடு|E-?Challan|Land\s+Acq\.?\s*Fund"),
         confidence=0.75),

    # -- possession notice / award (stage POSSESSION_NOTICE / AWARD) --------------------------
    Rule("FORM_E", _re(r"FORM[\s-]*E\b|படிவம்[\s-]*இ"), confidence=0.85),
    Rule("FORM_F", _re(r"FORM[\s-]*F\b|படிவம்[\s-]*எஃப்|Apportionment|விகிதாசார"), confidence=0.85),
    Rule("AWARD_7_3",
         _re(r"(?:AWARD|தீர்ப்பு|தீர்ப்பாணை).{0,40}7\s*\(\s*3\s*\)", r"section\s*7\s*\(\s*3\s*\)"),
         confidence=0.8),
    Rule("AWARD_7_2",
         _re(r"(?:AWARD|தீர்ப்பு|தீர்ப்பாணை).{0,40}7\s*\(\s*2\s*\)", r"section\s*7\s*\(\s*2\s*\)"),
         confidence=0.8),
    # loose bare "7(2)"/"7(3)" -- heading-zone only, same rationale as SEC32_NOTICE above, and
    # ranked a little above the equivalent loose SEC32_NOTICE match: a document's own filename
    # (e.g. "72Award4.pdf") is routinely echoed in its own page-1 heading.
    Rule("AWARD_7_3", _re(r"7\s*\(\s*3\s*\)"), confidence=0.63, heading_chars=400),
    Rule("AWARD_7_2", _re(r"7\s*\(\s*2\s*\)"), confidence=0.63, heading_chars=400),

    # -- payment (stage PAYMENT). BANK_INSTRUMENT = the scanned instrument itself (short,
    # mostly numeric/bank-code text, no letter prose); COURT_DEPOSIT = the covering letter to
    # the Judge; DISBURSEMENT = a treasury payment advice / bank-credit run (rare: 1/17 in the
    # golden "Amount Disbursed" sample) -------------------------------------------------------
    Rule("BANK_INSTRUMENT",
         _re(r"DEMAND\s*DRAFT|D\.?D\.?\s*No\.?\s*\d|\bCHEQUE\b|\bIOB\b|INDIAN\s+OVERSEAS\s+BANK"),
         exclude=(LETTER_SALUTATION_RE,), max_chars=400, confidence=0.75,
         extra_fields={"payee_kind": "court"}),
    Rule("COURT_DEPOSIT",
         _re(r"District\s*Judge|Principal\s*District\s*Judge|நீதிமன்ற"),
         all_patterns=_re(r"deposit|வைப்பு|court"), confidence=0.87,
         extra_fields={"payee_kind": "court"}),
    Rule("DISBURSEMENT",
         _re(r"Payment\s*Advice|\bNEFT\b|\bRTGS\b|Treasury.{0,20}(credit|payment)"),
         exclude=(re.compile(r"District\s*Judge|நீதிமன்ற", re.IGNORECASE),),
         confidence=0.7, extra_fields={"payee_kind": "owner"}),

    # -- possession / mutation ------------------------------------------------------------------
    Rule("LDR",
         _re(r"Land\s*Delivery\s*(Receipt|Certificate|Register)|\bLDR\b",
             r"Possession\s*(Certificate|Taken|Handed\s*Over)|கையளிக்கப்பட்டது|ஒப்படை"),
         confidence=0.85),
    Rule("CHITTA",
         _re(r"வட்டாட்சியர்\s*அலுவலக\s*இணைய\s*சேவை|நில\s*உரிமை\s*விபரங்கள்|பட்டா\s*எண்|Chitta\s*Extract"),
         confidence=0.85),

    # -- new D-025 classes -----------------------------------------------------------------------
    Rule("CALCULATION_SHEET",
         _re(r"Calculation\s+of\s+Compensation|Compensation\s+Calculation|கணக்கீடு.{0,15}இழப்பீடு"),
         confidence=0.7),
    Rule("CLASSIFICATION_ORDER",
         _re(r"classification|வகைப்பாடு"), all_patterns=_re(r"poramboke|புறம்போக்கு|reclassif"),
         confidence=0.65),
    Rule("CORRESPONDENCE", (LETTER_SALUTATION_RE,), all_patterns=_re(r"SIPCOT"), confidence=0.45),
)

AMBIGUOUS_THRESHOLD = 0.6  # below this, T1.6 method step 3 (classify_text LLM call) applies


def best_rule_match(text: str, char_count: int) -> list[RuleResult]:
    """All rule matches, sorted by confidence descending (ties keep rule order)."""
    hits: list[RuleResult] = []
    for rule in RULES:
        r = rule.match(text, char_count)
        if r is not None:
            hits.append(r)
    hits.sort(key=lambda r: -r.confidence)
    return hits
