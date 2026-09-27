"""Pseudonymisation gateway (Phase-0 stub).

What exists now:
  * `guard(model, tier)` — raises PrivacyViolation if a PII payload would reach a model whose
    resolved `trains_on_free_tier` is True (unknown => True). The router calls it right before
    every provider call (defence in depth; the eligibility filter applies the same rule).
  * `effective_tier(payload, declared)` — fail-closed tier resolution: unknown tier => PII, and a
    PSEUDO/PUBLIC payload that still carries raw PII markers is escalated to PII.
  * `Pseudonymiser` (P4): stable per-run tokens + local re-insert; reverse map in RunState.private.
"""
from __future__ import annotations

import re
from typing import Any

from .registry import ModelSpec
from .types import PRIVACY_TIERS, PrivacyViolation

TOKEN_RE = re.compile(r"⟨[A-Z]+_\d+⟩")
PII_KEYS = {"owner", "owner_name", "owners", "patta_no", "patta", "patta_number",
            "father_name", "husband_name", "guardian_name"}
# relation marker followed by a raw (non-token) name of >=3 letters (Latin or Tamil script)
_NAME = r"[A-Za-z஀-௿][A-Za-z஀-௿.]{2,}"
RELATION_RE = re.compile(
    r"(?:\bS/o|\bW/o|\bD/o|\bC/o|க/பெ|த/பெ|மகன்|மனைவி|மகள்|\bThiru\.?|\bTmt\.?|\bSelvi\.?)"
    r"(?:\s*[.:\-]\s*|\s+)(?!⟨)" + _NAME)


def is_training_tier(model: ModelSpec) -> bool:
    return model.trains_on_free_tier()


def guard(model: ModelSpec, privacy_tier: str) -> None:
    tier = normalise_tier(privacy_tier)
    if tier == "PII" and is_training_tier(model):
        raise PrivacyViolation(
            f"PII payload blocked: model {model.id} ({model.vendor}) may train on free-tier data")


def normalise_tier(tier: str | None) -> str:
    t = (tier or "").strip().upper()
    return t if t in PRIVACY_TIERS else "PII"      # unknown => most restrictive


def _walk_strings(obj: Any, key: str | None = None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, str(k))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v, key)
    elif isinstance(obj, str):
        yield key, obj


def detect_raw_pii(payload: dict) -> list[str]:
    """Cheap heuristic: PII-named fields holding non-token values, or relation markers + names."""
    hits: list[str] = []
    for key, s in _walk_strings(payload):
        if key and key.lower() in PII_KEYS and s.strip() and not TOKEN_RE.fullmatch(s.strip()):
            hits.append(f"field:{key}")
        if RELATION_RE.search(s):
            hits.append("relation_marker")
    return sorted(set(hits))


def effective_tier(payload: dict, declared: str | None) -> tuple[str, list[str]]:
    tier = normalise_tier(declared)
    notes: list[str] = []
    if tier != (declared or "").strip().upper():
        notes.append(f"unknown tier {declared!r} treated as PII")
    if tier != "PII":
        hits = detect_raw_pii(payload)
        if hits:
            notes.append(f"escalated {tier}->PII: {','.join(hits)}")
            tier = "PII"
    return tier, notes


# ================================================================================================
# P4: pseudonymise / re-insert (⟨OWNER_n⟩, ⟨PATTA_n⟩, ⟨AMT_n⟩). The reverse map lives only in
# RunState.private["pseudo"]; it is never serialised to events, the ledger or a provider.
# ================================================================================================
OWNER_KEYS = {"owner", "owner_name", "owners", "father_name", "husband_name", "guardian_name",
              "relation_name", "canonical_name", "variants", "raw_text", "name_variants", "owner_raw"}
PATTA_KEYS = {"patta_no", "patta", "patta_number"}
AMOUNT_KEYS = {"amount_rs", "amount", "compensation_rs", "owner_amount_rs"}   # per-owner / per-row amounts
PII_FACT_TYPES = {"owner": "OWNER", "patta_no": "PATTA", "amount_rs": "AMT"}
RUPEE_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*[0-9][0-9,]*(?:\.\d+)?(?:\s*/-)?")
_RELATION_CAPTURE = re.compile(
    r"(?P<marker>\bS/o|\bW/o|\bD/o|\bC/o|க/பெ|த/பெ|மகன்|மனைவி|மகள்|\bThiru\.?|\bTmt\.?|\bSelvi\.?)"
    r"(?P<sep>\s*[.:\-]\s*|\s+)(?!⟨)(?P<name>[A-Za-z஀-௿][A-Za-z஀-௿.]{2,}(?:\s+[A-Z஀-௿][A-Za-z஀-௿.]{1,})?)")
_TAMIL_NAME_BEFORE = re.compile(r"(?P<name>[஀-௿][஀-௿.]{2,})(?P<sep>\s+)(?=(?:த/பெ|க/பெ|மகன்|மனைவி|மகள்))")


import threading as _threading

_TOKEN_LOCK = _threading.Lock()


class Pseudonymiser:
    """Stable per-run tokens for owner names, patta numbers and per-owner amounts.

    ``known_names`` (e.g. every ``owner.canonical_name`` / ``variants`` / ``relation_name`` in the local KG) are
    replaced wherever they occur in free text, in addition to schema fields and relation-marker patterns.
    """

    def __init__(self, known_names: Any = (), state: dict | None = None):
        st = state or {}
        self.forward: dict[str, str] = dict(st.get("forward", {}))     # "KIND|value" -> token
        self.reverse: dict[str, str] = dict(st.get("reverse", {}))     # token -> value
        self.counters: dict[str, int] = dict(st.get("counters", {}))
        names = {str(n).strip() for n in known_names if n and len(str(n).strip()) >= 3}
        names |= set(st.get("known_names", []))
        self.known_names = sorted(names, key=lambda s: (-len(s), s))
        self._name_re = self._compile(self.known_names)

    @staticmethod
    def _compile(names: list[str]):
        if not names:
            return None
        alts = []
        for n in names:
            e = re.escape(n)
            alts.append(rf"(?<![A-Za-z]){e}(?![A-Za-z])" if n.isascii() else e)
        return re.compile("|".join(alts), re.IGNORECASE)

    # -- state -------------------------------------------------------------------------------
    def to_state(self, include_names: bool = False) -> dict:
        d = {"forward": dict(self.forward), "reverse": dict(self.reverse), "counters": dict(self.counters)}
        if include_names:
            d["known_names"] = self.known_names
        return d

    def token(self, kind: str, value: Any) -> str:
        v = str(value).strip()
        key = f"{kind}|{v.casefold()}"
        with _TOKEN_LOCK:
            tok = self.forward.get(key)
            if tok is None:
                self.counters[kind] = self.counters.get(kind, 0) + 1
                tok = f"⟨{kind}_{self.counters[kind]}⟩"
                self.forward[key] = tok
                self.reverse[tok] = v
        return tok

    # -- pseudonymise ------------------------------------------------------------------------
    def text(self, s: str, *, amounts: bool = True) -> str:
        if not s:
            return s
        out = s
        if self._name_re is not None:
            canon = {n.casefold(): n for n in self.known_names}
            out = self._name_re.sub(lambda m: self.token("OWNER", canon.get(m.group(0).casefold(), m.group(0))), out)
        out = _RELATION_CAPTURE.sub(lambda m: f"{m.group('marker')}{m.group('sep')}{self.token('OWNER', m.group('name'))}", out)
        out = _TAMIL_NAME_BEFORE.sub(lambda m: f"{self.token('OWNER', m.group('name'))}{m.group('sep')}", out)
        if amounts:
            out = RUPEE_RE.sub(lambda m: self.token("AMT", m.group(0)), out)
        return out

    def value(self, obj: Any, key: str | None = None, *, amounts: bool = True) -> Any:
        k = (key or "").lower()
        if isinstance(obj, dict):
            ft = str(obj.get("fact_type") or "").lower()
            res = {}
            for kk, vv in obj.items():
                if ft in PII_FACT_TYPES and kk in ("value_text", "value_num", "value") and vv is not None:
                    if ft == "amount_rs" and not amounts:
                        res[kk] = vv
                    else:
                        res[kk] = self.token(PII_FACT_TYPES[ft], vv)
                else:
                    res[kk] = self.value(vv, str(kk), amounts=amounts)
            return res
        if isinstance(obj, (list, tuple)):
            return [self.value(v, key, amounts=amounts) for v in obj]
        if obj is None or isinstance(obj, bool):
            return obj
        if k in OWNER_KEYS:
            return self.token("OWNER", obj) if not _is_token(obj) else obj
        if k in PATTA_KEYS:
            return self.token("PATTA", obj) if not _is_token(obj) else obj
        if k in AMOUNT_KEYS and amounts and isinstance(obj, (int, float, str)):
            return self.token("AMT", obj) if not _is_token(obj) else obj
        if isinstance(obj, str):
            return self.text(obj, amounts=amounts)
        return obj

    def pseudonymise(self, payload: Any, *, amounts: bool = True) -> Any:
        return self.value(payload, amounts=amounts)

    # -- re-insert (local only) ------------------------------------------------------------------
    def reinsert(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return TOKEN_RE.sub(lambda m: self.reverse.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, dict):
            return {k: self.reinsert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.reinsert(v) for v in obj]
        return obj


def _is_token(v: Any) -> bool:
    return isinstance(v, str) and bool(TOKEN_RE.fullmatch(v.strip()))


def residual_names(payload: Any, names: Any) -> list[str]:
    """Owner names (len > 3) still present in a serialised payload — used by the PII audit and tests."""
    import json as _json
    s = _json.dumps(payload, ensure_ascii=False, default=str).casefold()
    return sorted({n for n in names if n and len(str(n)) > 3 and str(n).casefold() in s})
