import csv
import json

from pipeline.classify import evaluate as evaluate_module


def test_evaluate_skips_gracefully_when_labels_missing(tmp_path):
    result = evaluate_module.evaluate(tmp_path / "does_not_exist.csv")
    assert result["status"] == "SKIPPED"


def test_evaluate_result_is_json_serialisable(tmp_path, monkeypatch):
    """Regression: Counter((true, pred)) confusion keys are tuples, which json.dumps rejects
    unless converted to a list of records first."""
    labels_path = tmp_path / "labels.csv"
    with open(labels_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sha256", "path", "folder_label", "doc_type",
                                           "scheme_relevance", "confidence", "note"])
        w.writeheader()
        w.writerow({"sha256": "abc123", "path": "x.pdf", "folder_label": "Form E",
                    "doc_type": "FORM_E", "scheme_relevance": "allikulam",
                    "confidence": "high", "note": ""})

    monkeypatch.setattr(evaluate_module, "load_predictions", lambda sha256s: {
        "abc123": {"classified_type": "FORM_F", "stage": "AWARD",
                   "scheme_relevance": "allikulam", "type_confidence": 0.5},
    })

    result = evaluate_module.evaluate(labels_path)
    assert result["status"] == "OK"
    assert result["n_scored"] == 1
    # must not raise
    dumped = json.dumps(result, default=str)
    assert "confusion_type_top20" in dumped


def test_evaluate_computes_stage_and_type_accuracy(tmp_path, monkeypatch):
    labels_path = tmp_path / "labels.csv"
    rows = [
        {"sha256": "s1", "doc_type": "FORM_E", "scheme_relevance": "allikulam"},
        {"sha256": "s2", "doc_type": "AWARD_7_2", "scheme_relevance": "allikulam"},
    ]
    with open(labels_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sha256", "path", "folder_label", "doc_type",
                                           "scheme_relevance", "confidence", "note"])
        w.writeheader()
        for r in rows:
            w.writerow({"path": "", "folder_label": "", "confidence": "", "note": "", **r})

    preds = {
        "s1": {"classified_type": "FORM_E", "stage": "POSSESSION_NOTICE",
               "scheme_relevance": "allikulam", "type_confidence": 0.9},
        "s2": {"classified_type": "SEC32_NOTICE", "stage": "SEC_3_2",
               "scheme_relevance": "allikulam", "type_confidence": 0.6},
    }
    monkeypatch.setattr(evaluate_module, "load_predictions", lambda sha256s: preds)

    result = evaluate_module.evaluate(labels_path)
    assert result["n_scored"] == 2
    assert result["doc_type_accuracy"] == 0.5
    assert result["stage_accuracy"] == 0.5  # s2's stage (AWARD) != predicted (SEC_3_2)
    assert len(result["stage_mismatches"]) == 1
    assert result["stage_mismatches"][0]["sha256"] == "s2"
