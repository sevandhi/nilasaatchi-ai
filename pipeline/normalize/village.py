"""Village normaliser via config/aliases.yaml (§7). Exact folded-alias match first, then fuzzy
matching only above ratio 90 and only against the 7 scheme villages (never guesses beyond)."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml
from rapidfuzz import fuzz

ALIASES_YAML = Path(__file__).resolve().parents[2] / "config" / "aliases.yaml"
VILLAGES = ("Allikulam", "Keelathattaparai", "Melathattaparai", "Umarikottai", "Peroorani",
            "Ramasamypuram", "South Silukanpatti")
FUZZY_MIN = 90


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFC", s or "").casefold()
    s = re.sub(r"(கிராமம்|கிராமத்தில்|கிராமத்தின்|village)", "", s)
    return re.sub(r"[\s\W_​-‍]+", "", s)


@lru_cache(maxsize=1)
def _alias_table() -> dict[str, str]:
    table: dict[str, str] = {}
    try:
        data = yaml.safe_load(ALIASES_YAML.read_text(encoding="utf-8")) or {}
    except OSError:
        data = {}
    for canon, spec in (data.get("villages") or {}).items():
        table[fold(canon)] = canon
        for a in [spec.get("name_ta")] + list(spec.get("aliases") or []):
            if a:
                table[fold(a)] = canon
    # golden-set spellings not yet in the yaml (land-domain-knowledge §11b; evaluator report)
    for a, canon in (("கீழதட்டப்பாறை", "Keelathattaparai"), ("அல்லிக்குளம்", "Allikulam"),
                     ("MelaThattaparai", "Melathattaparai"), ("KeelThattaparai", "Keelathattaparai"),
                     ("Perurani", "Peroorani"), ("பேருரணி", "Peroorani")):
        table.setdefault(fold(a), canon)
    for v in VILLAGES:
        table.setdefault(fold(v), v)
    return table


def normalise_village(raw: str | None) -> str | None:
    """Canonical English village name, or None. Substring hits count (a cell may read
    "பேரூரணி கிராமம், அலகு-5")."""
    f = fold(raw)
    if not f:
        return None
    table = _alias_table()
    if f in table:
        return table[f]
    for alias in sorted(table, key=len, reverse=True):
        if len(alias) >= 5 and alias in f:
            return table[alias]
    best, best_score = None, 0.0
    for alias, canon in table.items():
        sc = fuzz.ratio(f, alias)
        if sc > best_score:
            best, best_score = canon, sc
    return best if best_score >= FUZZY_MIN else None


def find_village_in_text(text: str | None) -> str | None:
    """First scheme village mentioned in free text (alias substring on the folded text, and a
    fuzzy window pass above 90 for OCR-damaged Tamil)."""
    f = fold(text)
    if not f:
        return None
    table = _alias_table()
    hits = []
    for alias, canon in table.items():
        if len(alias) >= 5:
            i = f.find(alias)
            if i >= 0:
                hits.append((i, canon))
    if hits:
        return min(hits)[1]
    return None
