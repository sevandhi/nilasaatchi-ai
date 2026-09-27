import json

from spikes.ocr_bakeoff.score import (
    CandidateScore,
    _label_field_ok,
    _normalize_label_field,
    load_labels,
    numeric_cer,
    score_page,
)

GOLD = {
    "page_id": "g99",
    "doc_type": "FORM_F",
    "header": {"village": "Melathattaparai", "unit_no": "7", "block_no": "9", "doc_date": "2025-02-02", "doc_no": "A7/10"},
    "rows": [
        {"survey_no": "173", "sub_div": "1", "extent_ha": 0.9550, "extent_ac": 2.35,
         "owner": "Thiru Sivakumar C S/o Chidambarapandian", "amount_rs": 135496, "classification": None},
    ],
}


def test_score_page_all_correct():
    pred = {
        "header": {"village": "Melathattaparai", "unit_no": "7", "block_no": "9", "doc_date": "2025-02-02", "doc_no": "A7/10"},
        "rows": [
            {"survey_no": "173", "sub_div": "1", "extent_ha": 0.9551, "extent_ac": 2.35,
             "owner": "Thiru Sivakumar C, S/o Chidambarapandian", "amount_rs": 135496, "classification": None},
        ],
    }
    ps = score_page("g99", GOLD, pred)
    assert all(v is True for v in ps.header_scores.values())
    row_score = ps.row_field_scores[0]
    assert row_score["survey_no"] is True
    assert row_score["extent_ha"] is True  # within +-0.005 ha tolerance
    assert row_score["owner"] is True      # token_sort_ratio >= 90
    assert row_score["amount_rs"] is True
    assert row_score["classification"] is None  # gold has no value -> not scored


def test_score_page_wrong_values():
    pred = {
        "header": {"village": "Allikulam", "unit_no": "7", "block_no": "9", "doc_date": None, "doc_no": None},
        "rows": [
            {"survey_no": "999", "sub_div": "9", "extent_ha": 5.0, "extent_ac": None,
             "owner": "someone else entirely", "amount_rs": 1, "classification": None},
        ],
    }
    ps = score_page("g99", GOLD, pred)
    assert ps.header_scores["village"] is False
    assert ps.n_rows_matched == 0   # no survey/subdiv match and owner too dissimilar
    row_score = ps.row_field_scores[0]
    assert row_score["survey_no"] is False
    assert row_score["extent_ha"] is False


def test_score_page_missing_gold_returns_empty():
    ps = score_page("gxx", None, {"header": {}, "rows": []})
    assert ps.header_scores == {}
    assert ps.row_field_scores == []


def test_load_labels_missing_file(tmp_path):
    assert load_labels(tmp_path / "does_not_exist.jsonl") is None


def test_load_labels_present(tmp_path):
    p = tmp_path / "mini.jsonl"
    p.write_text(json.dumps(GOLD) + "\n", encoding="utf-8")
    labels = load_labels(p)
    assert labels is not None
    assert "g99" in labels
    assert labels["g99"]["doc_type"] == "FORM_F"


def test_numeric_cer_perfect_and_missing():
    pred_perfect = {"rows": [{"survey_no": "173", "sub_div": "1", "extent_ha": 0.9550, "extent_ac": 2.35, "amount_rs": 135496}]}
    assert numeric_cer(GOLD, pred_perfect) == 0.0
    assert numeric_cer(None, pred_perfect) is None
    pred_empty = {"rows": []}
    assert numeric_cer(GOLD, pred_empty) == 1.0


def test_normalize_label_field_strips_prefixes_and_punctuation():
    assert _normalize_label_field("Roc.No.A9/5/BLOCK-1/2024-1") == "A9/5/BLOCK-1/2024-1"
    assert _normalize_label_field("A9/5/BLOCK-1/2024-1") == "A9/5/BLOCK-1/2024-1"
    assert _normalize_label_field("Allikulam :") == "Allikulam"
    assert _normalize_label_field("ந.க.அ7/10/அலகு-7-பிளாக் 9/2024-(4)") == "அ7/10/அலகு-7-பிளாக் 9/2024-(4)"
    assert _normalize_label_field(None) is None


def test_label_field_ok_matches_either_convention():
    assert _label_field_ok("Roc.No.A9/5/BLOCK-1/2024-1", "A9/5/BLOCK-1/2024-1") is True
    assert _label_field_ok("Allikulam", "Allikulam :") is True
    assert _label_field_ok("Allikulam", "Keelathattaparai") is False
    assert _label_field_ok(None, "x") is None
    assert _label_field_ok("x", None) is False


def test_score_page_doc_no_matches_with_different_label_conventions():
    gold = {**GOLD, "header": {**GOLD["header"], "doc_no": "Roc.No.A9/5/BLOCK-1/2024-1"}}
    pred = {"header": {**GOLD["header"], "doc_no": "A9/5/BLOCK-1/2024-1"}, "rows": []}
    ps = score_page("g99", gold, pred)
    assert ps.header_scores["doc_no"] is True


def test_candidate_score_aggregation():
    cand = CandidateScore(candidate="a")
    cand.pages.append(score_page("g99", GOLD, {
        "header": GOLD["header"],
        "rows": [{"survey_no": "173", "sub_div": "1", "extent_ha": 0.9550, "extent_ac": 2.35,
                  "owner": GOLD["rows"][0]["owner"], "amount_rs": 135496, "classification": None}],
    }))
    cand.seconds_per_page = [1.5, 2.5]
    assert cand.overall_exact_rate() == 1.0
    assert cand.mean_seconds_per_page == 2.0
