"""`make eval-classify` -> `python -m pipeline.classify.evaluate` (T1.6 exit gate).

Scores the classifier against eval/classify/labels.csv (qa-evaluator, 100 unique sha256).
Gate metric (D-025): **stage-level accuracy >= 90%**, scored on unique sha256. Fine-grained
doc_type accuracy and a confusion matrix are reported alongside, per the phase1 skill.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

from ..catalog.dbutil import get_conn
from .taxonomy import stage_of

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_CSV = REPO_ROOT / "eval" / "classify" / "labels.csv"


def load_labels(path: Path = LABELS_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_predictions(sha256s: list[str]) -> dict[str, dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT sha256, classified_type, stage, scheme_relevance, type_confidence "
            "FROM document WHERE sha256 = ANY(%s)",
            (sha256s,),
        ).fetchall()
    return {r[0]: {"classified_type": r[1], "stage": r[2], "scheme_relevance": r[3],
                   "type_confidence": r[4]} for r in rows}


def evaluate(labels_path: Path = LABELS_CSV) -> dict:
    if not labels_path.exists():
        return {"status": "SKIPPED", "reason": f"{labels_path} not found (qa-evaluator has not "
                                                 "delivered labels yet)"}
    labels = load_labels(labels_path)
    sha256s = [row["sha256"] for row in labels]
    preds = load_predictions(sha256s)

    n = 0
    stage_correct = 0
    type_correct = 0
    scheme_correct = 0
    missing: list[str] = []
    confusion: Counter = Counter()          # (true_type, pred_type) -> count
    stage_confusion: Counter = Counter()    # (true_stage, pred_stage) -> count
    mismatches: list[dict] = []

    for row in labels:
        sha = row["sha256"]
        pred = preds.get(sha)
        if pred is None:
            missing.append(sha)
            continue
        n += 1
        true_type = row["doc_type"]
        true_stage = stage_of(true_type)
        pred_type = pred["classified_type"]
        pred_stage = pred["stage"]

        confusion[(true_type, pred_type)] += 1
        stage_confusion[(true_stage, pred_stage)] += 1

        if pred_type == true_type:
            type_correct += 1
        if pred_stage == true_stage:
            stage_correct += 1
        else:
            mismatches.append({
                "sha256": sha, "path": row.get("path"), "folder_label": row.get("folder_label"),
                "true_type": true_type, "true_stage": true_stage,
                "pred_type": pred_type, "pred_stage": pred_stage,
                "confidence": pred["type_confidence"],
            })

        true_scheme = row.get("scheme_relevance")
        if true_scheme and pred["scheme_relevance"] == true_scheme:
            scheme_correct += 1

    stage_acc = stage_correct / n if n else 0.0
    type_acc = type_correct / n if n else 0.0
    scheme_acc = scheme_correct / n if n else 0.0

    return {
        "status": "OK",
        "n_scored": n,
        "n_missing_in_db": len(missing),
        "missing_sha256": missing[:10],
        "stage_accuracy": round(stage_acc, 4),
        "doc_type_accuracy": round(type_acc, 4),
        "scheme_relevance_accuracy": round(scheme_acc, 4),
        "gate_target": 0.90,
        "gate_pass": stage_acc >= 0.90,
        "confusion_type_top20": [
            {"true_type": t, "pred_type": p, "count": c} for (t, p), c in confusion.most_common(20)
        ],
        "confusion_stage": [
            {"true_stage": t, "pred_stage": p, "count": c} for (t, p), c in stage_confusion.most_common()
        ],
        "stage_mismatches": mismatches,
    }


def main() -> None:
    result = evaluate()
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") == "OK" and not result.get("gate_pass"):
        sys.exit(1)


if __name__ == "__main__":
    main()
