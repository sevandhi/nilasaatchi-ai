"""T5.1 parcel matcher — pure logic (no DB). land-domain-knowledge §6, D-020.

Resolution inside ONE village (never cross-village), in order:
  1. exact KIDE                       (id score 1.00)
  2. normalised survey/subdivision    (0.95; case, spaces, Tamil numerals/letters, 4-A == 4A)
  3. parent survey (subdivision missing):
       one FMB parcel under the survey  -> candidate (0.90)
       several                          -> survey_level link to all of them (0.85)
     subdivision prefix ("1" -> 1A/1B)  -> candidates (0.80)
  4. OCR-confusion variants (3<->8, 1<->7, 0<->6, 5<->6, A<->4): 0.65 for one swap, 0.45 for two
Tier 4 runs only when tiers 1-3 produce nothing. Adjustments: block agreement +0.05 / disagreement
-0.10; extent agreement (<=10 %) +0.05 / extent far above parcel area -0.10; village taken from the
document header only, or row/header disagreement, -0.05. Accept when score >= TAU and the margin to
the runner-up >= DELTA; otherwise ``ambiguous``. The top 3 candidates are always kept with reasons.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field, replace
from itertools import combinations

import yaml

from pipeline.normalize.numbers import tamil_numerals_to_ascii

THRESHOLDS_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"


def load_thresholds() -> dict:
    try:
        return yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


_M = load_thresholds().get("match", {})
TAU = float(_M.get("tau", 0.80))
DELTA = float(_M.get("delta", 0.10))
RERESOLVE_PENALTY = float(_M.get("reresolve_penalty", 0.05))
SCHEME_NAMES = frozenset(_M.get("scheme_village_names", ["Allikulam"]))
TOP_K = 3

ID_EXACT, ID_NORM, ID_PARENT_UNIQUE, ID_SURVEY_LEVEL, ID_PREFIX = 1.00, 0.95, 0.90, 0.85, 0.80
ID_OCR1, ID_OCR2 = 0.65, 0.45
BLOCK_AGREE, BLOCK_DISAGREE = 0.05, -0.10
EXTENT_AGREE, EXTENT_FAR = 0.05, -0.10
HEADER_ONLY, HEADER_CONFLICT = -0.05, -0.05

# Tamil subdivision letters (அ ஆ இ ஈ உ ஊ) -> A..F
_TA_LETTERS = {"அ": "A", "ஆ": "B", "இ": "C", "ஈ": "D", "உ": "E", "ஊ": "F"}
_OCR_PAIRS = [("3", "8"), ("1", "7"), ("0", "6"), ("5", "6"), ("A", "4")]
_OCR_MAP: dict[str, list[str]] = {}
for _a, _b in _OCR_PAIRS:
    _OCR_MAP.setdefault(_a, []).append(_b)
    _OCR_MAP.setdefault(_b, []).append(_a)

_PAREN = re.compile(r"\(.*?\)")
_NON_KEY = re.compile(r"[^0-9A-Z/]")


def norm_token(s: str | None) -> str:
    """Normalised survey/subdivision token: Tamil digits+letters -> ASCII, upper, no spaces,
    punctuation or parentheticals ("4-A" -> "4A", "1C(PLOT)" -> "1C")."""
    if s is None:
        return ""
    t = tamil_numerals_to_ascii(str(s))
    t = "".join(_TA_LETTERS.get(ch, ch) for ch in t)
    t = _PAREN.sub("", t).upper()
    t = _NON_KEY.sub("", t).strip("/")
    return re.sub(r"(^|/)0+(?=\d)", r"\1", t)   # "00394" -> "394"


def parse_refs(survey_no: str | None, sub_div: str | None) -> list[tuple[str, str]]:
    """Expand a document survey reference into (survey, subdiv) pairs; subdiv '' when missing.
    "26" + "1E,42/4C" -> [(26,1E),(42,4C)]; bare list items inherit the running survey
    ("1 / 6,7,8,10A" -> 1/6, 1/7, 1/8, 1/10A)."""
    s0 = norm_token(survey_no)
    raw_sd = tamil_numerals_to_ascii(sub_div or "")
    raw_sd = re.sub(r"மற்றும்|&|\band\b", ",", raw_sd)
    if not s0 and not raw_sd.strip():
        return []
    if "/" in s0:  # survey field itself carries "173/1"
        s0, _, extra = s0.partition("/")
        raw_sd = extra + ("," + raw_sd if raw_sd.strip() else "")
    items = [p for p in re.split(r"[,;]", raw_sd) if p.strip()]
    if not items:
        return [(s0, "")] if s0 else []
    out: list[tuple[str, str]] = []
    cur = s0
    for it in items:
        tok = norm_token(it)
        if not tok:
            continue
        if "/" in tok:
            cur, _, sd = tok.partition("/")
            out.append((cur, sd))
        else:
            out.append((cur, tok))
    seen, uniq = set(), []
    for r in out:
        if r[0] and r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def ocr_variants(tok: str, max_swaps: int = 2) -> dict[str, list[str]]:
    """Variants of ``tok`` reachable by <= max_swaps OCR confusions -> list of swap reasons."""
    pos = [i for i, ch in enumerate(tok) if ch in _OCR_MAP]
    out: dict[str, list[str]] = {}
    for k in range(1, max_swaps + 1):
        for idxs in combinations(pos, k):
            variants = [(tok, [])]
            for i in idxs:
                variants = [(v[:i] + alt + v[i + 1:], r + [f"{v[i]}->{alt}"])
                            for v, r in variants for alt in _OCR_MAP[v[i]]]
            for v, r in variants:
                out.setdefault(v, r)
    return out


@dataclass(frozen=True)
class Parcel:
    parcel_uid: str
    kide: str
    survey: str       # normalised, derived from KIDE (sub_div column is Excel-mangled for 16/3-4-5)
    subdiv: str       # normalised, '' when KIDE has no subdivision
    block_id: int | None = None
    unit_id: int | None = None
    area_ha: float | None = None


class VillageIndex:
    def __init__(self, parcels: list[Parcel]):
        self.by_kide = {p.kide: p for p in parcels}
        self.by_key: dict[tuple[str, str], list[Parcel]] = {}
        self.by_survey: dict[str, list[Parcel]] = {}
        for p in parcels:
            self.by_key.setdefault((p.survey, p.subdiv), []).append(p)
            self.by_survey.setdefault(p.survey, []).append(p)


def make_parcel(parcel_uid: str, kide: str, block_id=None, unit_id=None, area_ha=None) -> Parcel:
    s, _, sd = kide.partition("/")
    return Parcel(parcel_uid, kide, norm_token(s), norm_token(sd), block_id, unit_id,
                  float(area_ha) if area_ha is not None else None)


@dataclass
class Context:
    block_no: int | None = None
    unit_no: int | None = None
    extent_ha: float | None = None
    village_from_header: bool = False
    header_conflict: bool = False
    header_is_scheme: bool = False   # header village is the scheme name (D-052)
    reresolved: bool = False         # village re-resolved from unit+block+survey (D-052)


@dataclass
class Candidate:
    parcel_uid: str
    score: float
    reasons: list[str]
    tier: str

    def as_json(self) -> dict:
        return {"parcel_uid": self.parcel_uid, "score": round(self.score, 3),
                "tier": self.tier, "reasons": self.reasons}


@dataclass
class RefResult:
    ref: str
    status: str                     # accepted | survey_level | ambiguous | no_candidate
    links: list[tuple[str, str, float]] = field(default_factory=list)  # (uid, kind, score)
    candidates: list[Candidate] = field(default_factory=list)


def _adjust(p: Parcel, base: float, reasons: list[str], ctx: Context, area: float | None = None) -> float:
    s = base
    if ctx.block_no is not None and p.block_id is not None:
        same = ctx.block_no == p.block_id and (ctx.unit_no is None or p.unit_id is None or ctx.unit_no == p.unit_id)
        s += BLOCK_AGREE if same else BLOCK_DISAGREE
        reasons.append("block_agree" if same else f"block_disagree(doc {ctx.unit_no}/{ctx.block_no} vs fmb {p.unit_id}/{p.block_id})")
    a = area if area is not None else p.area_ha
    if ctx.extent_ha and a:
        rel = abs(ctx.extent_ha - a) / a
        if rel <= 0.10:
            s += EXTENT_AGREE
            reasons.append(f"extent_agree({ctx.extent_ha:.4f} vs {a:.4f} ha)")
        elif ctx.extent_ha > 1.5 * a + 0.05:
            s += EXTENT_FAR
            reasons.append(f"extent_far({ctx.extent_ha:.4f} vs {a:.4f} ha)")
    if ctx.village_from_header:
        s += HEADER_ONLY
        reasons.append("village_from_header")
    if ctx.header_conflict:
        s += HEADER_CONFLICT
        reasons.append("row_village_differs_from_header")
    if ctx.reresolved:
        s -= RERESOLVE_PENALTY
        reasons.append("village_reresolved_from_unit_block")
    return max(0.0, min(1.0, s))


def match_ref(idx: VillageIndex, survey: str, subdiv: str, ctx: Context,
              raw_kide: str | None = None) -> RefResult:
    ref = f"{survey}/{subdiv}" if subdiv else survey
    cands: dict[str, Candidate] = {}

    def add(p: Parcel, base: float, tier: str, reasons: list[str]):
        r = list(reasons)
        sc = _adjust(p, base, r, ctx)
        if p.parcel_uid not in cands or cands[p.parcel_uid].score < sc:
            cands[p.parcel_uid] = Candidate(p.parcel_uid, sc, r, tier)

    # 1 exact KIDE
    if raw_kide and raw_kide in idx.by_kide:
        add(idx.by_kide[raw_kide], ID_EXACT, "exact", ["exact_kide"])
    # 2 normalised
    if not cands:
        for p in idx.by_key.get((survey, subdiv), []):
            add(p, ID_NORM, "normalised", ["normalised_key"])
    # 3 parent survey / subdivision prefix
    if not cands and not subdiv:
        under = idx.by_survey.get(survey, [])
        if len(under) == 1:
            add(under[0], ID_PARENT_UNIQUE, "parent_survey", ["parent_survey_single_parcel"])
        elif len(under) > 1:
            area = sum(p.area_ha or 0 for p in under) or None
            reasons: list[str] = [f"survey_level({len(under)} subdivisions)"]
            sc = _adjust(Parcel("", "", survey, "", None, None, area), ID_SURVEY_LEVEL, reasons,
                         replace(ctx, block_no=None, unit_no=None))
            res = RefResult(ref, "survey_level" if sc >= TAU else "ambiguous")
            res.candidates = [Candidate(p.parcel_uid, sc, reasons, "survey_level") for p in under][:TOP_K]
            if res.status == "survey_level":
                res.links = [(p.parcel_uid, "survey_level", sc) for p in under]
            return res
    if not cands and subdiv:
        for p in idx.by_survey.get(survey, []):
            if p.subdiv.startswith(subdiv) and p.subdiv != subdiv:
                add(p, ID_PREFIX, "subdiv_prefix", [f"subdiv_prefix({subdiv}->{p.subdiv})"])
    # 4 OCR confusion
    if not cands:
        sv = {survey: []} | ocr_variants(survey)
        dv = {subdiv: []} | (ocr_variants(subdiv) if subdiv else {})
        for s2, r1 in sv.items():
            if s2 not in idx.by_survey:
                continue
            for d2, r2 in dv.items():
                swaps = r1 + r2
                if not swaps or len(swaps) > 2:
                    continue
                for p in idx.by_key.get((s2, d2), []):
                    add(p, ID_OCR1 if len(swaps) == 1 else ID_OCR2, "ocr",
                        [f"ocr_variant({'; '.join(swaps)})"])
    ranked = sorted(cands.values(), key=lambda c: (-c.score, c.parcel_uid))
    res = RefResult(ref, "no_candidate", candidates=ranked[:TOP_K])
    if not ranked:
        return res
    top = ranked[0]
    margin = top.score - (ranked[1].score if len(ranked) > 1 else 0.0)
    if top.score >= TAU and margin >= DELTA:
        res.status = "accepted"
        res.links = [(top.parcel_uid, "parcel", top.score)]
    else:
        res.status = "ambiguous"
        top.reasons.append(f"below_threshold(score {top.score:.2f}, margin {margin:.2f})")
    return res


@dataclass
class MatchOutcome:
    status: str             # accepted | survey_level | list | list_partial | ambiguous | no_candidate | no_ref | no_village
    parcel_uid: str | None
    score: float | None
    links: list[tuple[str, str, float]]
    candidates: list[dict]
    village: str | None = None      # village actually used (after any D-052 re-resolution)


def reresolve_village(index: dict[str, VillageIndex], village: str | None,
                      refs: list[tuple[str, str]], ctx: Context) -> str | None:
    """D-052: the one other village whose FMB has a parcel with this survey ref in the document's
    unit+block; None unless exactly one village qualifies."""
    if ctx.block_no is None:
        return None
    hits = set()
    for v, idx in index.items():
        if v == village:
            continue
        for s, d in refs:
            ps = idx.by_key.get((s, d), []) if d else idx.by_survey.get(s, [])
            if any(p.block_id == ctx.block_no and (ctx.unit_no is None or p.unit_id == ctx.unit_no) for p in ps):
                hits.add(v)
    return hits.pop() if len(hits) == 1 else None


def match_record(index: dict[str, VillageIndex], village: str | None, survey_no: str | None,
                 sub_div: str | None, ctx: Context) -> MatchOutcome:
    refs = parse_refs(survey_no, sub_div)
    if not refs:
        return MatchOutcome("no_ref", None, None, [], [], village)
    known = bool(village) and village in index
    out = _match_in_village(index, village, refs, ctx) if known else None
    may_reresolve = (not known or village in SCHEME_NAMES or ctx.header_is_scheme or ctx.village_from_header)
    if (out is None or out.status == "no_candidate") and may_reresolve:
        v2 = reresolve_village(index, village if known else None, refs, ctx)
        if v2:
            out2 = _match_in_village(index, v2, refs, replace(ctx, reresolved=True))
            if out2.status != "no_candidate":
                out2.candidates.append({"reresolved_from": village, "reason": "unit+block+survey unique (D-052)"})
                return out2
    if out is None:
        return MatchOutcome("no_village", None, None, [], [{"refs": [f"{s}/{d}" if d else s for s, d in refs]}], village)
    return out


def _match_in_village(index: dict[str, VillageIndex], village: str, refs: list[tuple[str, str]],
                      ctx: Context) -> MatchOutcome:
    idx = index[village]
    results = []
    for s, d in refs:
        raw = f"{s}/{d}" if d else s
        results.append(match_ref(idx, s, d, ctx, raw_kide=raw))
    hints = _other_village_hints(index, village, results, refs, ctx)
    if len(results) == 1:
        r = results[0]
        if hints:
            return MatchOutcome(r.status, None, None, [], [{"other_village_hint": hints}], village)
        top = r.candidates[0].score if r.candidates else None
        uid = r.links[0][0] if r.status == "accepted" else None
        return MatchOutcome(r.status, uid, top, r.links, [c.as_json() for c in r.candidates], village)
    # survey list: each member resolved independently
    links, cj = [], []
    for r in results:
        for uid, kind, sc in r.links:
            links.append((uid, "list_member" if kind == "parcel" else kind, sc))
        cj.append({"ref": r.ref, "status": r.status, "candidates": [c.as_json() for c in r.candidates]})
    if hints:
        cj.append({"other_village_hint": hints})
    ok = [r for r in results if r.status in ("accepted", "survey_level")]
    if len(ok) == len(results):
        status = "list"
    elif ok:
        status = "list_partial"     # resolved members keep their links; the rest go to review
    else:
        status = "ambiguous" if any(r.candidates for r in results) else "no_candidate"
    score = min((l[2] for l in links), default=None)
    return MatchOutcome(status, None, score, links, cj, village)


def _other_village_hints(index: dict[str, VillageIndex], village: str, results: list[RefResult],
                         refs: list[tuple[str, str]], ctx: Context) -> list[dict]:
    """For refs with no candidate in the stated village: where else does the exact key exist?
    Review hint only — never a match (no cross-village matching, §6). Block agreement is shown
    because scheme-labelled ("Allikulam") documents carry rows from other villages."""
    out = []
    for r, (s, d) in zip(results, refs):
        if r.status != "no_candidate":
            continue
        for v, idx in index.items():
            if v == village:
                continue
            for p in idx.by_key.get((s, d), []):
                blk = None
                if ctx.block_no is not None and p.block_id is not None:
                    blk = ctx.block_no == p.block_id and (ctx.unit_no is None or ctx.unit_no == p.unit_id)
                out.append({"ref": r.ref, "parcel_uid": p.parcel_uid, "block_agree": blk})
    return out[:TOP_K * 2]
