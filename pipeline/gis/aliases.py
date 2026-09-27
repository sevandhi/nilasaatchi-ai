"""Village / district name normalisation and parcel identity (D-020).

Canonical names and variants live in ``config/aliases.yaml``. Lookup is exact on a *folded*
key, so case, spacing, punctuation and zero-width-joiner differences need no entry.
Fuzzy matching is deliberately NOT done here: the matcher (P2) owns fuzzy logic and must
keep every candidate with a score.
"""
from __future__ import annotations

import pathlib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
ALIASES_PATH = ROOT / "config" / "aliases.yaml"

_ZW = dict.fromkeys((0x200B, 0x200C, 0x200D, 0xFEFF), None)  # zero-width chars
_SUFFIXES = ("village", "கிராமம்")
_PUNCT = re.compile(r"[\s\.\,\-_/'\"()\[\]:;]+")


def fold(name: str) -> str:
    """Folded comparison key: NFC, no zero-width chars, case-folded, no spaces/punctuation."""
    s = unicodedata.normalize("NFC", str(name)).translate(_ZW).casefold().strip()
    for suf in _SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)]
    return _PUNCT.sub("", s)


@dataclass(frozen=True)
class VillageAlias:
    name: str
    name_ta: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AliasTable:
    villages: tuple[VillageAlias, ...]
    districts: dict[str, tuple[str, ...]]
    _village_keys: dict[str, str]
    _district_keys: dict[str, str]

    def canonical_village(self, name: str | None) -> str | None:
        if name is None:
            return None
        return self._village_keys.get(fold(name))

    def canonical_district(self, name: str | None) -> str | None:
        if name is None:
            return None
        return self._district_keys.get(fold(name))


def build_table(cfg: dict) -> AliasTable:
    villages: list[VillageAlias] = []
    vkeys: dict[str, str] = {}
    for canon, spec in (cfg.get("villages") or {}).items():
        spec = spec or {}
        aliases = tuple(spec.get("aliases") or ())
        villages.append(VillageAlias(canon, spec.get("name_ta"), aliases))
        for variant in (canon, spec.get("name_ta"), *aliases):
            if not variant:
                continue
            k = fold(variant)
            if vkeys.get(k, canon) != canon:
                raise ValueError(f"alias {variant!r} maps to both {vkeys[k]!r} and {canon!r}")
            vkeys[k] = canon
    districts: dict[str, tuple[str, ...]] = {}
    dkeys: dict[str, str] = {}
    for canon, spec in (cfg.get("districts") or {}).items():
        aliases = tuple((spec or {}).get("aliases") or ())
        districts[canon] = aliases
        for variant in (canon, *aliases):
            dkeys[fold(variant)] = canon
    return AliasTable(tuple(villages), districts, vkeys, dkeys)


@lru_cache(maxsize=4)
def load_aliases(path: str | pathlib.Path = ALIASES_PATH) -> AliasTable:
    with open(path, encoding="utf-8") as fh:
        return build_table(yaml.safe_load(fh))


def canonical_village(name: str | None) -> str | None:
    return load_aliases().canonical_village(name)


# ------------------------------------------------------------------ parcel identity
_KIDE_WS = re.compile(r"\s+")


def normalise_kide(kide: str | int) -> str:
    """Trim, remove internal whitespace, upper-case subdivision letters ('177/4a ' -> '177/4A')."""
    s = _KIDE_WS.sub("", str(kide)).upper()
    if not s:
        raise ValueError("empty KIDE")
    return s


def split_kide(kide: str) -> tuple[str, str | None]:
    """'177/4A' -> ('177', '4A'); '168' -> ('168', None); '16/3-4-5' -> ('16', '3-4-5')."""
    k = normalise_kide(kide)
    survey, _, sub = k.partition("/")
    return survey, (sub or None)


def parcel_uid(village: str, kide: str | int) -> str:
    """D-020 key '<canonical village>|<KIDE>'. Raises on an unknown village."""
    canon = canonical_village(village)
    if canon is None:
        raise ValueError(f"unknown village {village!r} (add it to config/aliases.yaml with evidence)")
    return f"{canon}|{normalise_kide(kide)}"
