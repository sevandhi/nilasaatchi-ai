"""Score a candidate's parsed output against `eval/golden/mini.jsonl`, per T0.5:

field-level exact match / tolerance, CER on concatenated numeric fields, and
row alignment by best survey/serial match. Handles the labels file being
absent (qa-evaluator writes it in parallel) by returning a "no_labels" result
so `run.py` can still report raw OCR + timings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from spikes.ocr_bakeoff.normalize import cer

EXTENT_TOL_HA = 0.005
OWNER_MIN_RATIO = 90


@dataclass
class FieldScore:
    field: str
    n: int = 0
    n_match: int = 0

    @property
    def rate(self) -> float | None:
        return self.n_match / self.n if self.n else None


@dataclass
class PageScore:
    page_id: str
    header_scores: dict[str, bool | None] = field(default_factory=dict)
    row_field_scores: list[dict[str, bool | None]] = field(default_factory=list)
    n_gold_rows: int = 0
    n_pred_rows: int = 0
    n_rows_matched: int = 0


@dataclass
class CandidateScore:
    candidate: str
    pages: list[PageScore] = field(default_factory=list)
    seconds_per_page: list[float] = field(default_factory=list)
    quota_units_per_page: list[float] = field(default_factory=list)
    cer_values: list[float] = field(default_factory=list)

    def field_exact_rate(self, field_name: str) -> float | None:
        n = n_match = 0
        for p in self.pages:
            if field_name in p.header_scores:
                v = p.header_scores[field_name]
                if v is not None:
                    n += 1
                    n_match += int(v)
            for row_score in p.row_field_scores:
                if field_name in row_score:
                    v = row_score[field_name]
                    if v is not None:
                        n += 1
                        n_match += int(v)
        return n_match / n if n else None

    def overall_exact_rate(self) -> float | None:
        n = n_match = 0
        for p in self.pages:
            for v in p.header_scores.values():
                if v is not None:
                    n += 1
                    n_match += int(v)
            for row_score in p.row_field_scores:
                for v in row_score.values():
                    if v is not None:
                        n += 1
                        n_match += int(v)
        return n_match / n if n else None

    @property
    def mean_seconds_per_page(self) -> float | None:
        return sum(self.seconds_per_page) / len(self.seconds_per_page) if self.seconds_per_page else None

    @property
    def mean_cer(self) -> float | None:
        return sum(self.cer_values) / len(self.cer_values) if self.cer_values else None


def load_labels(labels_path: Path) -> dict[str, dict[str, Any]] | None:
    """Return {page_id: gold_record} or None if the labels file does not exist yet
    (the qa-evaluator writes it in parallel; T0.5 must degrade gracefully)."""
    if not labels_path.exists():
        return None
    labels: dict[str, dict[str, Any]] = {}
    with labels_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            labels[rec["page_id"]] = rec
    return labels


def _extent_ok(gold: float | None, pred: float | None) -> bool | None:
    if gold is None:
        return None
    if pred is None:
        return False
    return abs(gold - pred) <= EXTENT_TOL_HA


def _owner_ok(gold: str | None, pred: str | None) -> bool | None:
    if gold is None:
        return None
    if not pred:
        return False
    return fuzz.token_sort_ratio(gold, pred) >= OWNER_MIN_RATIO


def _exact_ok(gold: Any, pred: Any) -> bool | None:
    if gold is None:
        return None
    if pred is None:
        return False
    return str(gold).strip() == str(pred).strip()


# Label prefixes that some documents print and some parsers strip inconsistently,
# e.g. gold `doc_no="Roc.No.A9/5/BLOCK-1/2024-1"` vs a parser that captured only the
# value after the label. Comparing doc_no/village must not penalise either
# convention, so both sides are normalised the same way before comparison.
_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:Roc\.?\s*No\.?|Lr\.?\s*No\.?|G\.O\.\s*\(?Ms\)?\s*No\.?|ந\.க\.|நாள்\s*எண்)\s*[:.\-]?\s*",
    re.IGNORECASE,
)


def _normalize_label_field(raw: Any) -> str | None:
    """Strip a leading label prefix ("Roc.No.", "Lr.No.", "G.O.(Ms) No.", "ந.க.")
    and any trailing/leading colon or whitespace, so doc_no/village compare on the
    value only, regardless of which side kept the label."""
    if raw is None:
        return None
    s = str(raw).strip()
    s = _LABEL_PREFIX_RE.sub("", s)
    s = s.strip(" :\t")
    return s


def _label_field_ok(gold: Any, pred: Any) -> bool | None:
    if gold is None:
        return None
    if pred is None:
        return False
    return _normalize_label_field(gold) == _normalize_label_field(pred)


def _best_row_match(gold_row: dict[str, Any], pred_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Align by survey_no+sub_div first, falling back to the highest owner-name
    similarity, per T0.5 ("row alignment by serial/survey best match")."""
    gold_key = (str(gold_row.get("survey_no") or ""), str(gold_row.get("sub_div") or ""))
    for pred in pred_rows:
        pred_key = (str(pred.get("survey_no") or ""), str(pred.get("sub_div") or ""))
        if gold_key == pred_key and gold_key != ("", ""):
            return pred
    if gold_row.get("owner") and pred_rows:
        scored = [(fuzz.token_sort_ratio(gold_row["owner"], p.get("owner") or ""), p) for p in pred_rows]
        best_score, best_pred = max(scored, key=lambda t: t[0])
        if best_score >= 60:
            return best_pred
    return None


def score_page(page_id: str, gold: dict[str, Any] | None, pred: dict[str, Any], raw_text: str = "") -> PageScore:
    ps = PageScore(page_id=page_id)
    if gold is None:
        return ps
    gh, ph = gold.get("header", {}) or {}, pred.get("header", {}) or {}
    for f in ("unit_no", "block_no", "doc_date"):
        ps.header_scores[f] = _exact_ok(gh.get(f), ph.get(f))
    for f in ("village", "doc_no"):
        ps.header_scores[f] = _label_field_ok(gh.get(f), ph.get(f))

    gold_rows = gold.get("rows", []) or []
    pred_rows = pred.get("rows", []) or []
    ps.n_gold_rows, ps.n_pred_rows = len(gold_rows), len(pred_rows)
    for gr in gold_rows:
        match = _best_row_match(gr, pred_rows)
        if match is not None:
            ps.n_rows_matched += 1
        row_score = {
            "survey_no": _exact_ok(gr.get("survey_no"), match.get("survey_no") if match else None),
            "sub_div": _exact_ok(gr.get("sub_div"), match.get("sub_div") if match else None),
            "extent_ha": _extent_ok(gr.get("extent_ha"), match.get("extent_ha") if match else None),
            "owner": _owner_ok(gr.get("owner"), match.get("owner") if match else None),
            "amount_rs": _exact_ok(gr.get("amount_rs"), match.get("amount_rs") if match else None),
            "classification": _exact_ok(gr.get("classification"), match.get("classification") if match else None),
        }
        ps.row_field_scores.append(row_score)
    return ps


def numeric_cer(gold: dict[str, Any] | None, pred: dict[str, Any]) -> float | None:
    """CER on concatenated numeric fields (extents, amounts, survey numbers)."""
    if gold is None:
        return None

    def numeric_blob(rec: dict[str, Any]) -> str:
        parts: list[str] = []
        for row in rec.get("rows", []) or []:
            for f in ("survey_no", "sub_div", "extent_ha", "extent_ac", "amount_rs"):
                v = row.get(f)
                if v is not None:
                    parts.append(str(v))
        return "".join(parts)

    ref = numeric_blob(gold)
    hyp = numeric_blob(pred)
    if not ref:
        return None
    return cer(ref, hyp)
