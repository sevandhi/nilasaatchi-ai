"""Numeric normalisers: Tamil numerals, extents (ha / ac / cents), money and number words.

Promoted from spikes/ocr_bakeoff/normalize.py (bake-off, D-010) and extended with the
golden-set findings in land-domain-knowledge §5 and §11b. Every function is pure and total:
unparseable input returns ``None`` instead of raising, because OCR/VLM text is always noisy.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Constants (land-domain-knowledge §5)
# ---------------------------------------------------------------------------
HA_TO_AC = 2.47105          # exact
HA_TO_AC_DOC = 2.47         # what most documents actually use (D-014)
CENT_TO_AC = 0.01
CENT_TO_M2 = 40.4686
HA_TO_M2 = 10_000.0

TAMIL_DIGITS = "௦௧௨௩௪௫௬௭௮௯"
_TAMIL_TO_ASCII = {d: str(i) for i, d in enumerate(TAMIL_DIGITS)}


def clean(s: str | None) -> str:
    """NFC-normalise and strip; ``None`` becomes ``""``."""
    if s is None:
        return ""
    return unicodedata.normalize("NFC", str(s)).strip()


def tamil_numerals_to_ascii(s: str | None) -> str:
    """Map Tamil digit glyphs ௦-௯ to ASCII. Only digit glyphs are touched (§5: normalise only
    inside numeric fields; OCR's ௧-for-க confusion must not leak into names)."""
    return "".join(_TAMIL_TO_ASCII.get(ch, ch) for ch in clean(s))


# ---------------------------------------------------------------------------
# Extents
# ---------------------------------------------------------------------------
_TRIPLE_RE = re.compile(r"^(\d+)\.(\d{1,2})\.(\d{1,2})$")
_DASH_RE = re.compile(r"^(\d+)-(\d{1,2}(?:\.\d{1,2})?)$")
_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)?)$")
_UNIT_WORDS_RE = re.compile(
    r"(ஹெக்டேர்|ஹெக்டர்|ஹெக்\.?|hectares?|hect\.?|hec\.?|ha\.?|ஏக்கர்|ஏக்\.?|acres?|ac\.?|\(|\))",
    re.IGNORECASE,
)


def _prep_extent(raw: str | None) -> str:
    s = tamil_numerals_to_ascii(raw)
    # take the first extent-looking token when a cell also carries an acre figure in brackets,
    # e.g. "11.31.00 ஹெக்டேர் (27.947 ஏக்கர்)"
    s = s.split("(")[0] if "(" in s and re.search(r"\d", s.split("(")[0]) else s
    s = _UNIT_WORDS_RE.sub(" ", s)
    s = s.replace(":", ".").replace(",", "").replace(" ", "")   # "0:10.50", "104.31 .00", ",1.02.00"
    s = s.replace("–", "-").replace("—", "-")
    s = s.strip(".-")
    return s


def parse_extent_ha(raw: str | None) -> float | None:
    """Hectare extent from ``H.AA.SS`` ("0.95.50" → 0.9550), chitta ``H - AA.SS``
    ("0 - 53.50" → 0.5350) or a bare decimal. value = H + AA/100 + SS/10000.

    A 1-digit third group is right-padded ("185.96.5" → 185.9650, §11b)."""
    s = _prep_extent(raw)
    if not s:
        return None
    m = _TRIPLE_RE.match(s)
    if m:
        h, aa, ss = m.groups()
        return round(int(h) + int(aa) / 100 + int(ss.ljust(2, "0")) / 10_000, 4)
    m = _DASH_RE.match(s)
    if m:
        return round(int(m.group(1)) + float(m.group(2)) / 100, 4)
    m = _DOT_DROPPED_RE.match(s)
    if m:                                   # "053.50" = 0.53.50 with the first dot dropped (VLM)
        return round(int(m.group(1)) / 100 + int(m.group(2)) / 10_000, 4)
    m = _PLAIN_RE.match(s)
    if m:
        return round(float(m.group(1)), 4)
    return None


_DOT_DROPPED_RE = re.compile(r"^0(\d{2})\.(\d{2})$")


def parse_extent_ac(raw: str | None) -> float | None:
    """Acre extent printed as a plain decimal ("2.35", "2.124 (ஏக்கர்)")."""
    s = tamil_numerals_to_ascii(raw)
    s = _UNIT_WORDS_RE.sub(" ", s).replace(",", "").replace(" ", "").strip(".")
    if not s or not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    return round(float(s), 4)


def parse_cents(raw: str | None) -> float | None:
    """Cents (சென்ட்) as a plain number ("4", "3.50", "3½")."""
    s = tamil_numerals_to_ascii(raw).replace("½", ".5").replace("¼", ".25").replace("¾", ".75")
    s = re.sub(r"(சென்ட்|செண்ட்|cents?)", "", s, flags=re.IGNORECASE).replace(" ", "")
    if s.startswith("."):
        s = "0" + s
    s = re.sub(r"^(\d+)\.\.", r"\1.", s)
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    return round(float(s), 4)


def ha_to_ac(ha: float, factor: float = HA_TO_AC) -> float:
    return round(ha * factor, 4)


def ac_to_ha(ac: float, factor: float = HA_TO_AC) -> float:
    return round(ac / factor, 4)


def cents_to_ac(cents: float) -> float:
    return round(cents * CENT_TO_AC, 4)


def cents_to_ha(cents: float) -> float:
    return round(cents * CENT_TO_M2 / HA_TO_M2, 4)


def extents_agree(ha: float | None, ac: float | None) -> bool | None:
    """ha↔ac agreement with **either** factor 2.47 or 2.47105 (§5, D-014): |ac − ha×f| ≤ 0.02,
    scaled to 0.1 % for extents over 10 ha. Truncation instead of rounding stays inside 0.02.
    ``None`` when either side is missing."""
    if ha is None or ac is None:
        return None
    tol = max(0.02, ha * HA_TO_AC * 0.001) if ha > 10 else 0.02
    return any(abs(ha * f - ac) <= tol + 1e-9 for f in (HA_TO_AC, HA_TO_AC_DOC))


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
_MONEY_TOKEN_RE = re.compile(r"\d[\d,.]*\d|\d")


def parse_amount(raw: str | None) -> int | None:
    """Indian-grouped rupees → integer rupees: "₹12,60,000/-" → 1260000, "***10,23,687.00"
    → 1023687, "ரூ.95,64,800/-" → 9564800. Paise are dropped. Falls back to number words
    (English or Tamil) when no digits are present."""
    s = tamil_numerals_to_ascii(raw)
    if not s:
        return None
    # OCR sometimes renders grouping commas as dots: "10.23.687.00" → treat all but a final
    # 2-digit group as grouping separators
    toks = _MONEY_TOKEN_RE.findall(s.replace(" ", ""))
    if not toks:
        return parse_number_words(s)
    tok = max(toks, key=len)
    if tok.count(".") > 1:
        parts = tok.split(".")
        if len(parts[-1]) == 2:
            tok = "".join(parts[:-1])
        else:
            tok = "".join(parts)
    tok = tok.replace(",", "")
    try:
        return int(float(tok))
    except ValueError:
        return None


def compensation_matches(amount_rs: float, extent_ac: float, rate_per_ac: float,
                         tol_rs: float = 1.0, tol_frac: float = 0.0) -> bool:
    """amount ≈ extent_acres × rate (±₹1 per row by default, §phase2)."""
    expected = extent_ac * rate_per_ac
    return abs(amount_rs - expected) <= max(tol_rs, expected * tol_frac)


# ---------------------------------------------------------------------------
# Number words (English + Tamil) — for Form F amount_words cross-checks
# ---------------------------------------------------------------------------
_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_EN_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
              "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000, "million": 1_000_000}

# Tamil stems, longest first. Cores end in pulli where sandhi joins the next word, e.g.
# முப்பத்|தைந்|தாயிரத்|து: after a pulli-final core, a following த+vowel-sign is read as the
# independent vowel (தை→ஐ, தா→ஆ, து→உ …), which undoes the sandhi.
_TA_STEMS: dict[str, tuple[int, str]] = {}
for _stems, _val, _kind in [
    (("ஒன்று", "ஒன்", "ஒரு", "ஓர்"), 1, "u"), (("இரண்டு", "இரண்", "இரு"), 2, "u"),
    (("மூன்று", "மூன்"), 3, "u"), (("நான்கு", "நான்", "நாலு"), 4, "u"), (("ஐந்து", "ஐந்"), 5, "u"),
    (("ஆறு", "ஆற்"), 6, "u"), (("ஏழு",), 7, "u"), (("எட்டு", "எட்"), 8, "u"),
    (("ஒன்பது", "ஒன்பத்"), 9, "u"), (("பத்து", "பத்"), 10, "u"), (("பதினொன்று", "பதினொன்"), 11, "u"),
    (("பன்னிரண்டு", "பன்னிரண்"), 12, "u"), (("பதின்மூன்று", "பதின்மூன்"), 13, "u"),
    (("பதினான்கு", "பதினான்"), 14, "u"), (("பதினைந்து", "பதினைந்"), 15, "u"),
    (("பதினாறு",), 16, "u"), (("பதினேழு",), 17, "u"), (("பதினெட்டு", "பதினெட்"), 18, "u"),
    (("பத்தொன்பது", "பத்தொன்பத்"), 19, "u"),
    (("இருபது", "இருபத்"), 20, "u"), (("முப்பது", "முப்பத்"), 30, "u"),
    (("நாற்பது", "நாற்பத்"), 40, "u"), (("ஐம்பது", "ஐம்பத்"), 50, "u"),
    (("அறுபது", "அறுபத்"), 60, "u"), (("எழுபது", "எழுபத்"), 70, "u"),
    (("எண்பது", "எண்பத்"), 80, "u"), (("தொண்ணூறு", "தொண்ணூற்"), 90, "u"),
    (("நூறு", "நூற்"), 100, "u"), (("இருநூறு", "இருநூற்"), 200, "u"),
    (("முந்நூறு", "முந்நூற்", "முன்னூறு", "முன்னூற்"), 300, "u"), (("நானூறு", "நானூற்"), 400, "u"),
    (("ஐநூறு", "ஐநூற்", "ஐந்நூறு", "ஐந்நூற்"), 500, "u"), (("அறுநூறு", "அறுநூற்"), 600, "u"),
    (("எழுநூறு", "எழுநூற்"), 700, "u"), (("எண்ணூறு", "எண்ணூற்"), 800, "u"),
    (("தொள்ளாயிரம்", "தொள்ளாயிரத்"), 900, "u"),
    (("ஆயிரம்", "ஆயிரத்"), 1_000, "s"),
    (("லட்சம்", "லட்சத்", "இலட்சம்", "இலட்சத்", "லெட்சம்", "லெட்சத்"), 100_000, "s"),
    (("கோடி",), 10_000_000, "s"),
]:
    for _s in _stems:
        _TA_STEMS[_s] = (_val, _kind)
        if _s.endswith("ு"):            # ஏழு+ஆயிரம் = ஏழாயிரம்: core without the final u
            _TA_STEMS.setdefault(_s[:-1], (_val, _kind))
_TA_ORDER = sorted(_TA_STEMS, key=len, reverse=True)
_TA_SANDHI = {"ா": "ஆ", "ி": "இ", "ீ": "ஈ", "ு": "உ", "ூ": "ஊ", "ெ": "எ", "ே": "ஏ", "ை": "ஐ",
              "ொ": "ஒ", "ோ": "ஓ"}
def _combine(tokens: list[tuple[int, str]]) -> int | None:
    """Indian-system accumulate: units add to `current`; a scale multiplies `current` (or 1)."""
    total, current = 0, 0
    if not tokens:
        return None
    for value, kind in tokens:
        if kind == "s":
            if value == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * value
                current = 0
        else:
            current += value
    return total + current


def parse_number_words(raw: str | None) -> int | None:
    """"One Lakh Thirty Five Thousand Four Hundred Ninety Six" → 135496;
    "ஒரு லட்சத்து முப்பத்தைந்தாயிரத்து நானூற்று தொண்ணூற்று ஆறு" → 135496. ``None`` if no
    number word is recognised."""
    s = clean(raw)
    if not s:
        return None
    low = re.sub(r"[^a-z\s]", " ", s.lower())
    en_tokens: list[tuple[int, str]] = []
    for w in low.split():
        if w in _EN_UNITS:
            en_tokens.append((_EN_UNITS[w], "u"))
        elif w in _EN_SCALES:
            en_tokens.append((_EN_SCALES[w], "s"))
    if en_tokens:
        return _combine(en_tokens)
    ta = re.sub(r"[^஀-௿]", "", s)
    ta = re.sub(r"(ரூபாய்|ரூபாய|மட்டும்|ரூ)", "", ta)
    toks: list[tuple[int, str]] = []
    i = 0
    while i < len(ta):
        for stem in _TA_ORDER:
            if ta.startswith(stem, i):
                toks.append(_TA_STEMS[stem])
                i += len(stem)
                if stem.endswith("்") and i + 1 < len(ta) and ta[i] == "த" and ta[i + 1] in _TA_SANDHI:
                    ta = ta[:i] + _TA_SANDHI[ta[i + 1]] + ta[i + 2:]
                elif i < len(ta) and ta[i] in _TA_SANDHI:     # bare-consonant core + vowel sign
                    ta = ta[:i] + _TA_SANDHI[ta[i]] + ta[i + 1:]
                break
        else:
            i += 1
    return _combine(toks) if toks else None
