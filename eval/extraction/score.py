"""Golden-set scorer for P2 extraction (eval/golden/mini.jsonl), per-doc-type field mapping.

Fields scored:
- headers: village, unit_no, block_no, doc_no (canonical), doc_date (day precision only — the gold
  leaves blank-day dates null); headline = null-aware accuracy over all (page, field) pairs, plus
  recall on gold-non-null and precision on predicted-non-null;
- rows (aligned per page): survey (survey_no + sub_div jointly), extent_ha (±0.005), extent_ac
  (±0.005), cents, owner (rapidfuzz token_sort_ratio ≥ 90 on punctuation-stripped printed text),
  amount_rs (exact), classification (exact), patta_no; village-total rows: patta/poramboke/total ha;
- doc_type (with D-025 equivalences) and printed totals (info).

Metrics per field: tp / fp / fn → precision, recall (= accuracy on gold values), and row exact match.
Reported for LEGIBLE pages (gold needs_human = false) and NEEDS_HUMAN pages separately.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz

from pipeline.normalize.survey import canon_subdiv

HEADER_FIELDS = ("village", "unit_no", "block_no", "doc_no", "doc_date")
ROW_FIELDS = ("survey", "extent_ha", "extent_ac", "cents", "owner", "amount_rs", "classification", "patta_no")
VT_FIELDS = ("patta_ha", "poramboke_ha", "total_ha")
DOC_EQUIV = {("OTHER", "AS_PROPOSAL"), ("COURT_DEPOSIT", "BANK_INSTRUMENT"), ("DLPNC", "SLPNC"),
             ("LDR", "POSSESSION_CERT")}
EXT_TOL = 0.005


# ---------------------------------------------------------------------------
def _nfc(s) -> str:
    return re.sub(r"[​-‍﻿]", "", unicodedata.normalize("NFC", str(s or ""))).strip()


_REL_CANON = [(r"த\s*[/.]\s*ப(?:ெ)?\.?", " தபெ "), (r"க\s*[/.]\s*ப(?:ெ)?\.?", " கபெ "), (r"S\s*/\s*o\.?", " so "),
              (r"W\s*/\s*o\.?", " wo "), (r"D\s*/\s*o\.?", " do ")]


def owner_norm(s) -> str:
    """Punctuation-stripped printed text; relation markers canonicalised (த/ப ≡ த/பெ: a dropped
    vowel sign on the marker is not a name error)."""
    s = _nfc(s)
    for rx, rep in _REL_CANON:
        s = re.sub(rx, rep, s, flags=re.I)
    s = re.sub(r"(^|\s|;)\(?\d{1,2}[.)]\s*", " ", s)
    s = re.sub(r"[.,;:()\-/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def owner_match(g, p) -> bool:
    if not g or not p:
        return False
    a, b = owner_norm(g), owner_norm(p)
    # word spacing inside Tamil names is scan/OCR noise ("பெருமாள்பிள்ளை" vs "பெருமாள் பிள்ளை")
    return fuzz.token_sort_ratio(a, b) >= 90 or fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")) >= 90


def docno_canon(s) -> str:
    s = _nfc(s).replace("௮", "அ")
    s = re.sub(r"^(roc\.?\s*no\.?|lr\.?\s*no\.?|ந\.?\s*க\.?\s*(எண்)?\.?|&\.?\s*க\.?)\s*[:.]?", "", s, flags=re.I)
    s = re.sub(r"^(roc\.?\s*no\.?|lr\.?\s*no\.?|ந\.?\s*க\.?\s*(எண்)?\.?)\s*[:.]?", "", s, flags=re.I)
    return re.sub(r"[^0-9a-z஀-௿]", "", s.lower())


def docno_match(g, p) -> bool:
    if not g or not p:
        return False
    a, b = docno_canon(g), docno_canon(p)
    return a == b or (min(len(a), len(b)) >= 6 and (a.endswith(b) or b.endswith(a)))


def doc_type_match(g, p) -> bool:
    return g == p or (g, p) in DOC_EQUIV or (p, g) in DOC_EQUIV


def survey_eq(g: dict, p: dict) -> bool:
    return (str(g.get("survey_no") or "").strip().upper() == str(p.get("survey_no") or "").strip().upper()
            and canon_subdiv(g.get("sub_div")) == canon_subdiv(p.get("sub_div")) and bool(g.get("survey_no")))


def _num_eq(g, p, tol) -> bool:
    return g is not None and p is not None and abs(float(g) - float(p)) <= tol + 1e-9


def field_eq(f: str, g: dict, p: dict) -> bool:
    if f == "survey":
        return survey_eq(g, p)
    gv, pv = gold_val(f, g), pred_val(f, p)
    if f in {"extent_ha", "extent_ac", "cents", "patta_ha", "poramboke_ha", "total_ha"}:
        return _num_eq(gv, pv, EXT_TOL)
    if f == "owner":
        return owner_match(gv, pv)
    if f == "amount_rs":
        return gv is not None and pv is not None and int(gv) == int(pv)
    return gv is not None and str(gv).strip().upper() == str(pv or "").strip().upper()


def gold_val(f: str, g: dict):
    if f == "survey":
        return g.get("survey_no")
    if f == "total_ha":
        return g.get("extent_ha")
    return g.get(f)


def pred_val(f: str, p: dict):
    if f == "survey":
        return p.get("survey_no")
    if f == "owner":
        return p.get("owner_raw")
    return p.get(f)


# ---------------------------------------------------------------------------
def pred_rows(pred: dict) -> tuple[str, list[dict]]:
    recs = pred.get("records") or []
    vt = [r for r in recs if r.get("record_type") == "village_totals"]
    if vt:
        return "village_totals", vt
    ff = [r for r in recs if r.get("record_type") == "form_f"]
    pr = [r for r in recs if r.get("record_type") in {"parcel_row", "summary_item"}
          and r.get("table_role") != "published_as"]
    pi = [r for r in recs if r.get("record_type") == "payment_instrument"]
    if pr:
        return "parcel", pr + ff
    if ff:
        return "owner_amount", ff
    if pi:
        return "instrument", pi
    return "none", []


def align(gold: list[dict], pred: list[dict], kind: str) -> list[tuple[int | None, int | None]]:
    """Greedy alignment by pair score; unmatched rows pair with None."""
    scores = []
    for i, g in enumerate(gold):
        for j, p in enumerate(pred):
            if kind == "village_totals":
                s = 3 if g.get("village") and g.get("village") == p.get("village") else 0
                s += 1 if g.get("serial") and g.get("serial") == p.get("serial") else 0
            else:
                s = 3 if survey_eq(g, p) else (1 if g.get("survey_no") and g.get("survey_no") == p.get("survey_no")
                                               else 0)
                s += 1 if g.get("serial") and str(g.get("serial")).rstrip(".") == str(p.get("serial") or "") else 0
                s += 1 if _num_eq(g.get("extent_ha"), p.get("extent_ha"), EXT_TOL) else 0
                s += 1 if owner_match(g.get("owner"), p.get("owner_raw")) else 0
                s += 1 if g.get("amount_rs") and g.get("amount_rs") == p.get("amount_rs") else 0
                s += 0.5 if i == j else 0
            if s >= 1:
                scores.append((s, -abs(i - j), i, j))
    scores.sort(reverse=True)
    gi, pj, pairs = set(), set(), []
    for _, _, i, j in scores:
        if i in gi or j in pj:
            continue
        gi.add(i)
        pj.add(j)
        pairs.append((i, j))
    # instrument / owner-amount pages with one row each: pair by order
    if kind in {"owner_amount", "instrument"}:
        for i in range(len(gold)):
            if i not in gi and i < len(pred) and i not in pj:
                pairs.append((i, i))
                gi.add(i)
                pj.add(i)
    pairs += [(i, None) for i in range(len(gold)) if i not in gi]
    pairs += [(None, j) for j in range(len(pred)) if j not in pj]
    return pairs


class Tally:
    def __init__(self):
        self.c = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
        self.rows = {"exact": 0, "total": 0}

    def add(self, f, tp=0, fp=0, fn=0):
        d = self.c[f]
        d["tp"] += tp
        d["fp"] += fp
        d["fn"] += fn

    def merge(self, other: Tally):
        for f, d in other.c.items():
            self.add(f, **d)
        for k in self.rows:
            self.rows[k] += other.rows[k]

    def metrics(self) -> dict:
        out = {}
        for f, d in self.c.items():
            tp, fp, fn = d["tp"], d["fp"], d["fn"]
            out[f] = {"precision": round(tp / (tp + fp), 3) if tp + fp else None,
                      "recall": round(tp / (tp + fn), 3) if tp + fn else None, "n_gold": tp + fn, **d}
        return out


_CARRIED_NOTE = re.compile(r"((?:village|unit|block)(?:\s*[/,]\s*(?:village|unit|block))*)[^.;]{0,40}carried",
                           re.IGNORECASE)
_FIELD_OF = {"village": "village", "unit": "unit_no", "block": "block_no"}


def carried_gold_fields(gold: dict) -> set:
    """Gold header fields carried over from an earlier page (D-040): an explicit
    `header_source: {field: "carried"}` or a note like "village/unit/block carried from g04"."""
    hs = gold.get("header_source") or {}
    out = {f for f, v in hs.items() if v == "carried"}
    for m in _CARRIED_NOTE.finditer(gold.get("notes") or ""):
        out |= {_FIELD_OF[w.lower()] for w in re.findall(r"village|unit|block", m.group(1), re.I)}
    return out


def score_page(gold: dict, pred: dict | None, skip_uncertain: bool = False) -> dict:
    """Score one page. `skip_uncertain` drops gold fields listed in fields_uncertain."""
    t = Tally()
    pred = pred or {"header": {}, "records": []}
    unc = set(gold.get("fields_uncertain") or []) if skip_uncertain else set()
    detail: dict = {"page_id": gold["page_id"], "doc_type": gold["doc_type"], "pred_doc_type": pred.get("doc_type"),
                    "status": pred.get("status"), "fails": []}
    # doc type
    ok = doc_type_match(gold["doc_type"], pred.get("doc_type"))
    if "doc_type" not in unc:
        t.add("doc_type", tp=int(ok), fn=int(not ok), fp=int(not ok and bool(pred.get("doc_type"))))
    # headers (null-aware accuracy + P/R)
    gh, ph = gold.get("header") or {}, pred.get("header") or {}
    g_carried = carried_gold_fields(gold)
    for f in HEADER_FIELDS:
        if f"header.{f}" in unc:
            continue
        # D-040: a header carried over from an earlier page is excluded on both sides
        if f in g_carried or (ph.get(f) or {}).get("header_source") == "carried" or \
                (ph.get(f) or {}).get("source") == "carried":
            continue
        g = gh.get(f)
        hp = ph.get(f) or {}
        p = hp.get("value")
        if f == "doc_date" and hp.get("precision") not in (None, "day"):
            p = None
        eq = docno_match(g, p) if f == "doc_no" else (g is not None and str(g) == str(p))
        t.add("header_acc", tp=int(eq or (g is None and p is None)), fn=int(not (eq or (g is None and p is None))))
        if g is not None:
            t.add(f"header.{f}", tp=int(eq), fn=int(not eq), fp=int(p is not None and not eq))
            t.add("headers", tp=int(eq), fn=int(not eq), fp=int(p is not None and not eq))
        elif p is not None:
            t.add(f"header.{f}", fp=1)
            t.add("headers", fp=1)
        if not (eq or (g is None and p is None)):
            detail["fails"].append(f"header.{f}: gold={g!r} pred={p!r}")
    # rows
    grows = gold.get("rows") or []
    kind, prows = pred_rows(pred)
    is_vt = any(r.get("village") and (r.get("patta_ha") is not None or r.get("extent_ha") is not None)
                and not r.get("survey_no") for r in grows)
    gkind = "village_totals" if is_vt else ("owner_amount" if gold["doc_type"] == "FORM_F" else
                                            "instrument" if gold["doc_type"] == "COURT_DEPOSIT" else "parcel")
    fields = VT_FIELDS + ("extent_ac",) if gkind == "village_totals" else ROW_FIELDS
    if gkind == "village_totals":
        prows = [r for r in prows if r.get("record_type") == "village_totals"]
        prows = [{**r, "extent_ac": r.get("total_ac")} for r in prows]
    pairs = align(grows, prows, gkind)
    for i, j in pairs:
        g = grows[i] if i is not None else None
        p = prows[j] if j is not None else None
        if g is not None:
            t.rows["total"] += 1
        row_ok = True
        for f in fields:
            key = "survey_no" if f == "survey" else f
            if g is not None and any(u.startswith(f"rows[{i}].") and u.split(".", 1)[1] in {key, f, "owner" if f == "owner"
                                     else key} for u in unc):
                continue
            gv = gold_val(f, g) if g is not None else None
            pv = pred_val(f, p) if p is not None else None
            if gkind == "village_totals" and f == "total_ha" and g is not None and gv is None:
                continue
            if gv is not None:
                eq = p is not None and field_eq(f, g, p)
                t.add(f, tp=int(eq), fn=int(not eq), fp=int(pv is not None and not eq))
                if not eq:
                    row_ok = False
                    detail["fails"].append(f"row{i}.{f}: gold={gv!r} pred={pv!r}")
            elif pv is not None and f in {"survey", "extent_ha", "owner", "amount_rs"} and g is None:
                t.add(f, fp=1)
        if g is not None and row_ok and p is not None:
            t.rows["exact"] += 1
    # printed totals (info)
    gt, pt = gold.get("printed_totals") or {}, pred.get("printed_totals") or {}
    for f in ("extent_ha", "extent_ac", "amount_rs"):
        if gt.get(f) is not None:
            eq = pt.get(f) is not None and (abs(float(pt[f]) - float(gt[f])) <= EXT_TOL if f != "amount_rs"
                                            else int(pt[f]) == int(gt[f]))
            t.add("printed_totals", tp=int(eq), fn=int(not eq))
    detail["tally"] = t
    return detail


def summarise(details: list[dict]) -> dict:
    total = Tally()
    for d in details:
        total.merge(d["tally"])
    m = total.metrics()
    m["_row_exact_match"] = round(total.rows["exact"] / total.rows["total"], 3) if total.rows["total"] else None
    m["_rows"] = total.rows["total"]
    m["_pages"] = len(details)
    return m
