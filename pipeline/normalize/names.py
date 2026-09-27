"""Owner-name normaliser (phase2 skill §Normalisers).

- strips honorifics (Thiru, Tmt, Selvi, Mr, திரு, திருமதி, செல்வி …) and list numbering;
- splits relations: "B, த/பெ. A" / "B S/o A" (person first) and "A மகன் B" / "A மனைவி B"
  (Tamil genitive order: relation name first, person second);
- builds a Latin matching key via a table-driven Tamil → ISO-15919 transliteration, folded to
  ASCII (`indic_transliteration` renders Tamil stops as Sanskrit aspirates, so it is not used).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

HONORIFICS = [
    "திருமதி", "திருமிகு", "செல்வி", "செல்வன்", "திரு", "Thirumathi", "Thiru", "Tmt", "Selvi",
    "Selvan", "Mrs", "Mr", "Ms", "Smt", "Sri", "Shri", "Kum", "M/s", "Dr",
]
# relation marker → (relation code, person_first?)
RELATIONS: list[tuple[str, str, bool]] = [
    (r"த\s*/\s*பெ", "father", True), (r"க\s*/\s*பெ", "husband", True), (r"ம\s*/\s*பெ", "mother", True),
    (r"S\s*/\s*o", "father", True), (r"D\s*/\s*o", "father", True), (r"W\s*/\s*o", "husband", True),
    (r"C\s*/\s*o", "care_of", True), (r"son\s+of", "father", True), (r"wife\s+of", "husband", True),
    (r"daughter\s+of", "father", True),
    (r"மகன்", "father", False), (r"மகள்", "father", False), (r"மனைவி", "husband", False),
]
_REL_RE = re.compile(r"\s*[,.]?\s*(" + "|".join(p for p, _, _ in RELATIONS) + r")\s*[.:,]?\s*",
                     re.IGNORECASE)
_HON_RE = re.compile(r"^\s*(?:" + "|".join(re.escape(h) for h in HONORIFICS) + r")\s*[.:]?\s*",
                     re.IGNORECASE)
_NUMBERING_RE = re.compile(r"^\s*\(?\d{1,2}[.)]\s*")
_HEIRS_RE = re.compile(r"(வாரிசுதாரர்கள்|வாரிசுகள்|legal\s+heirs?)", re.IGNORECASE)


@dataclass
class OwnerName:
    raw: str
    name: str
    relation: str | None = None
    relation_name: str | None = None
    honorific: str | None = None
    legal_heirs: bool = False
    key: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tamil → ISO-15919
# ---------------------------------------------------------------------------
_VOWELS = {"அ": "a", "ஆ": "ā", "இ": "i", "ஈ": "ī", "உ": "u", "ஊ": "ū", "எ": "e", "ஏ": "ē",
           "ஐ": "ai", "ஒ": "o", "ஓ": "ō", "ஔ": "au", "ஃ": "ḵ"}
_CONS = {"க": "k", "ங": "ṅ", "ச": "c", "ஞ": "ñ", "ட": "ṭ", "ண": "ṇ", "த": "t", "ந": "n",
         "ப": "p", "ம": "m", "ய": "y", "ர": "r", "ல": "l", "வ": "v", "ழ": "ḻ", "ள": "ḷ",
         "ற": "ṟ", "ன": "ṉ", "ஜ": "j", "ஷ": "ṣ", "ஸ": "s", "ஹ": "h", "ஶ": "ś"}
_SIGNS = {"ா": "ā", "ி": "i", "ீ": "ī", "ு": "u", "ூ": "ū", "ெ": "e", "ே": "ē", "ை": "ai",
          "ொ": "o", "ோ": "ō", "ௌ": "au"}
_PULLI = "்"


def tamil_to_iso(text: str) -> str:
    s = unicodedata.normalize("NFC", text or "")
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in _CONS:
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt == _PULLI:
                out.append(_CONS[ch])
                i += 2
                continue
            if nxt in _SIGNS:
                out.append(_CONS[ch] + _SIGNS[nxt])
                i += 2
                continue
            out.append(_CONS[ch] + "a")
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in _SIGNS or ch == _PULLI:
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def latin_key(text: str | None) -> str:
    """ASCII matching key: transliterate, strip diacritics, lower-case, keep [a-z0-9 ], collapse
    doubled letters and common Tamil/English spelling variants (th→t, dh→d, zh/ḻ→l, ee→i)."""
    s = tamil_to_iso(text or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    for a, b in (("th", "t"), ("dh", "d"), ("zh", "l"), ("sh", "s"), ("ee", "i"), ("oo", "u"),
                 ("aa", "a"), ("ph", "p"), ("w", "v"), ("y", "i")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Owner parsing
# ---------------------------------------------------------------------------
def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,.;:-")


def normalise_name(raw: str | None) -> OwnerName | None:
    s = unicodedata.normalize("NFC", raw or "").strip()
    if not s:
        return None
    s = _NUMBERING_RE.sub("", s)
    heirs = bool(_HEIRS_RE.search(s))
    s = _HEIRS_RE.sub("", s)
    honorific = None
    m = _HON_RE.match(s)
    if m:
        honorific = m.group(0).strip(" .:")
        s = s[m.end():]
    relation = relation_name = None
    m = _REL_RE.search(s)
    if m:
        marker = m.group(1)
        code, person_first = next((c, pf) for p, c, pf in RELATIONS if re.fullmatch(p, marker, re.I))
        left, right = _strip(s[:m.start()]), _strip(s[m.end():])
        if person_first or not left:
            name, relation_name = left, right
        else:
            name, relation_name = right, left
        relation = code
        name = _HON_RE.sub("", name)
    else:
        name = s
    name = _strip(name)
    relation_name = _strip(_HON_RE.sub("", relation_name)) if relation_name else None
    return OwnerName(raw=raw or "", name=name, relation=relation, relation_name=relation_name or None,
                     honorific=honorific, legal_heirs=heirs, key=latin_key(name))


_SPLIT_NUMBERED_RE = re.compile(r"(?:^|\s|\n|;)\(?(\d{1,2})[.)]\s*(?=\D)")


_CONTINUATION_RE = re.compile(r"^(?:[தக]\s*[/.]\s*பெ|[தக]\s*/\s*ப\b|மகன்|மனைவி|மகள்|S\s*/\s*o|W\s*/\s*o|D\s*/\s*o|"
                              r"C\s*/\s*o|மற்றும்|and\b|\()", re.IGNORECASE)


def split_owners(raw: str | None) -> list[str]:
    """Split a multi-owner cell ("1. A த/பெ B\\n2. C க/பெ D", or "A ; C") into owner strings.
    Numbering is removed; a single owner returns a one-element list."""
    s = unicodedata.normalize("NFC", raw or "").strip()
    if not s:
        return []
    if ";" in s:
        parts = s.split(";")
    elif len(_SPLIT_NUMBERED_RE.findall(s)) >= 2:
        parts = _SPLIT_NUMBERED_RE.split(s)
        parts = [p for i, p in enumerate(parts) if not (i % 2 == 1 and p.isdigit())]
    elif "\n" in s:
        # a line that starts with a relation marker (or "மற்றும் N நபர்") continues the owner above:
        # "<name>\nத/பெ. <father>" is one owner, not two
        parts = []
        for line in s.split("\n"):
            if parts and _CONTINUATION_RE.match(line.strip()):
                parts[-1] = f"{parts[-1]} {line.strip()}"
            else:
                parts.append(line)
    else:
        parts = [s]
    out = [_strip(_NUMBERING_RE.sub("", p)) for p in parts]
    return [p for p in out if p and not p.isdigit()]


def owner_display(raw: str | None) -> str | None:
    """Printed-script owner string with numbering removed and owners joined by ' ; ' (the
    golden-set convention)."""
    parts = split_owners(raw)
    return " ; ".join(parts) if parts else None
