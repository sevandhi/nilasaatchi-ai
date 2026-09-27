import pytest

from spikes.ocr_bakeoff.grid import Cell
from spikes.ocr_bakeoff.parse_fields import (
    classify_columns,
    header_from_text,
    rows_from_grid,
    rows_from_hosted_json,
    rows_from_text,
)

HEADER_TEXT_CASES = [
    (
        "வட்டம் : தூத்துக்குடி  கிராமம் : கீழத்தட்டப்பாறை அலகு: 7  பிளாக்: 9\nநாள் : 02.2025",
        {"village": "Keelathattaparai", "unit_no": "7", "block_no": "9"},
    ),
    (
        "DISTRICT THOOTHUKUDI\nUNIT - IV\nBLOCK - 2\nDate of Handling Over : 21.03.2025",
        {"unit_no": "IV", "block_no": "2", "doc_date": "2025-03-21"},
    ),
]


@pytest.mark.parametrize("text,expected_subset", HEADER_TEXT_CASES)
def test_header_from_text(text, expected_subset):
    header = header_from_text(text)
    for k, v in expected_subset.items():
        assert header[k] == v


def test_classify_columns_tamil_form_e():
    header = ["வ.எண", "புலஎண", "வகைப்பாடு", "பிளாட் எண்", "விஸ்தீரணம் (சென்ட்)", "நிலஉரிமையாளர்"]
    fields = classify_columns(header)
    assert fields[1] == "survey_no"
    assert fields[2] == "classification"
    assert fields[4] == "extent_cents"
    assert fields[5] == "owner"


def test_classify_columns_tolerates_split_conjuncts():
    # Real bug found running the g19 chitta page: PaddleOCR reads the survey-no
    # header as "புல எண்" (with a space) while our keyword is "புலஎண" (no
    # space); column classification must not depend on OCR's exact spacing.
    fields = classify_columns(["புல எண்", "உட்பிரிவு"])
    assert fields[0] == "survey_no"
    assert fields[1] == "sub_div"


def test_classify_columns_english_form_f():
    header = ["S.No.", "Name of the land Owners", "Total compensation amount apportioned to the land owners (Rs.)"]
    fields = classify_columns(header)
    assert fields[1] == "owner"
    assert fields[2] == "amount_rs"


def _cells_grid(n_rows, n_cols):
    return [Cell(row=r, col=c, x=c * 100, y=r * 50, w=100, h=50) for r in range(n_rows) for c in range(n_cols)]


def test_rows_from_grid_ldc_style():
    # Mirrors the g18 LDR table: SL NO | SURVEY NO | SUB DIVISION | Hec. | Acre | ...
    cells = _cells_grid(3, 5)
    cell_text = {
        (0, 0): "SL NO", (0, 1): "SURVEY NO", (0, 2): "SUB DIVISION", (0, 3): "Hec.", (0, 4): "Acre",
        (1, 0): "1", (1, 1): "1", (1, 2): "1", (1, 3): "0.60.50", (1, 4): "1.494",
        (2, 0): "2", (2, 1): "1", (2, 2): "2", (2, 3): "0.60.00", (2, 4): "1.482",
    }
    rows = rows_from_grid(cells, cell_text)
    assert len(rows) == 2
    assert rows[0]["extent_ha"] == pytest.approx(0.605)
    assert rows[0]["extent_ac"] == pytest.approx(1.494)


def test_rows_from_grid_skips_total_row():
    cells = _cells_grid(3, 2)
    cell_text = {
        (0, 0): "புலஎண", (0, 1): "விஸ்தீரணம் ஹெக்.",
        (1, 0): "173/1", (1, 1): "0.95.50",
        (2, 0): "மொத்தம்", (2, 1): "0.95.50",
    }
    rows = rows_from_grid(cells, cell_text)
    assert len(rows) == 1
    assert rows[0]["survey_no"] == "173"
    assert rows[0]["sub_div"] == "1"


def test_rows_from_hosted_json_normalises_raw_strings():
    raw_rows = [
        {"serial": "1", "survey_no": "173", "sub_div": "1", "extent_ha_raw": "0.95.50",
         "extent_ac_raw": "2.35", "cents_raw": None, "owner": "திரு முத்துவேல்", "patta_no": "1241",
         "amount_raw": "11,75,000/-", "classification_raw": None},
        {"serial": "2", "survey_no": "376", "sub_div": None, "extent_ha_raw": None,
         "extent_ac_raw": None, "cents_raw": "4", "owner": None, "patta_no": None,
         "amount_raw": None, "classification_raw": "வீட்டுமனை"},
    ]
    rows = rows_from_hosted_json(raw_rows)
    assert rows[0]["extent_ha"] == pytest.approx(0.955)
    assert rows[0]["extent_ac"] == pytest.approx(2.35)
    assert rows[0]["amount_rs"] == 1175000
    assert rows[0]["owner"] == "திரு முத்துவேல்"
    assert rows[1]["survey_no"] == "376"
    assert rows[1]["extent_ac"] == pytest.approx(0.04)   # 4 cents -> 0.04 acre
    assert rows[1]["classification"] == "வீட்டுமனை"


def test_rows_from_hosted_json_resplits_merged_survey_no():
    # Observed on the live g04 smoke test: the model returned "173/1" in
    # survey_no with sub_div left null, instead of splitting per the schema.
    raw_rows = [{"survey_no": "173/1", "sub_div": None, "extent_ha_raw": "0.95.50"}]
    rows = rows_from_hosted_json(raw_rows)
    assert rows[0]["survey_no"] == "173"
    assert rows[0]["sub_div"] == "1"


def test_rows_from_hosted_json_empty():
    assert rows_from_hosted_json([]) == []
    assert rows_from_hosted_json(None) == []


def test_rows_from_text_fallback():
    text = "1 173/1 0.95.50 ரூ.11,75,000/- திருமதி அருள் எஸ்தர்"
    rows = rows_from_text(text)
    assert len(rows) == 1
    assert rows[0]["survey_no"] == "173"
    assert rows[0]["extent_ha"] == pytest.approx(0.955)
    assert rows[0]["amount_rs"] == 1175000
