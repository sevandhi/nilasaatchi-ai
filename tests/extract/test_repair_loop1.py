"""Repair loop 1 (held-out misses): multi-block award pages, payout tables, treasury advices,
two-hectare 7(3) tables, continuation headers, rotation. Synthetic values only."""
from pathlib import Path

import pytest
from PIL import Image

from pipeline.extract.consistency import run_checks, summarise
from pipeline.extract.headers import Lines, extract_headers
from pipeline.extract.mapping import cell_type, map_parcel_columns, prepare_table
from pipeline.extract.page import improves_consistency, upright
from pipeline.extract.records import apply_block_prose, build_records
from pipeline.normalize.names import owner_display

CTX = {"doc_id": "t", "page_no": 1, "privacy_tier": "PII", "source_engine": "vlm:test", "model_id": "m"}
LAND_COLS = ["வ. எண்", "புல எண்", "விஸ்தீரணம் ஹெக்.", "விஸ்தீரணம் ஏக்.", "பட்டா எண்", "நில உரிமையாளர்"]


def test_ungrouped_amount_is_an_amount():
    assert cell_type("1740000.00") == "bignum" and cell_type("1,74,000.00") == "money"


def test_per_block_totals_checked_per_table():
    t1 = {"columns": LAND_COLS, "rows": [["1", "11/1", "0.50.00", "1.235", "10", "அ மகன் ஆ"],
                                         ["மொத்தம்", "0.50.00", "1.235", None, None, None]]}   # shifted total
    t2 = {"columns": LAND_COLS, "rows": [["1", "11/2", "0.30.00", "0.741", "11", "இ மகன் ஈ"],
                                         [None, "மொத்தம்", "0.30.00", "0.741", None, None]]}
    ctx = dict(CTX)
    recs, tot, _ = build_records({"tables": [t1, t2]}, "AWARD_7_2", "parcel", ctx, "", {}, [])
    per = tot.pop("_per_table")
    assert per[0]["extent_ha"] == 0.5 and per[0]["extent_ac"] == 1.235 and per[1]["extent_ha"] == 0.3
    assert summarise(run_checks(recs, tot, {}, "parcel", per)) == "pass"


def test_unlabelled_total_row_is_not_a_parcel():
    t = {"columns": LAND_COLS, "rows": [["1", "12/1", "1.00.00", "2.47", "5", "அ மகன் ஆ"],
                                        ["2", "12/2", "0.50.00", "1.235", None, None],
                                        [None, None, "1.50.00", "3.705", None, None]]}
    recs, tot, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [r["survey_no"] + "/" + r["sub_div"] for r in recs] == ["12/1", "12/2"]
    assert tot["_per_table"][0]["extent_ha"] == 1.5
    assert recs[1]["owner_raw"] == recs[0]["owner_raw"]            # merged owner cell filled down


def test_payout_total_column_and_previous_block_survey():
    land = {"columns": LAND_COLS, "rows": [["1", "13/4", "0.49.50", "1.22", "7", "அ த/பெ. ஆ"]]}
    pay = {"columns": ["வ.எண்", "பெயர்", "நிலத்திற்கான இழப்பீட்டுத் தொகை ரு.",
                       "மரங்கள் மற்றும் கட்டமைப்பிற்கான இழப்பீட்டுத் தொகை ரு.", "மொத்த இழப்பீட்டுத் தொகை ரு."],
           "rows": [["1", "அ த/பெ. ஆ", "6,10,000", "40,000", "6,50,000"]]}
    recs, _, _ = build_records({"tables": [land, pay]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    comp = [r for r in recs if r.get("table_role") == "compensation"][0]
    assert (comp["amount_rs"], comp["land_amount_rs"], comp["tree_amount_rs"]) == (650000, 610000, 40000)
    assert (comp["survey_no"], comp["sub_div"]) == ("13", "4")


def test_block_prose_amounts_and_heading_owner():
    text = ("முந்தைய பக்கம் இழப்பீடு ரூ.9,99,000/-ஐ வழங்குவது\n"
            "2. புல எண்: 224/1 - கந்தன் மகன் முருகன்:\n"
            "மேற்படி ... ஏக்கருக்கு ரூ.5,00,000/- வீதம் ... இழப்பீடு ரூ.8,09,000/-ஐ வழங்குவது\n"
            "3. புல எண்: 224/2\n"
            "நிலத்திற்கான இழப்பீடு ரூ.15,99,500/-ஐ வழங்குவது")
    recs = [{"record_type": "parcel_row", "survey_no": "224", "sub_div": "1", "owner_raw": None, "amount_rs": None},
            {"record_type": "parcel_row", "survey_no": "224", "sub_div": "2", "owner_raw": "X", "amount_rs": None}]
    assert apply_block_prose(recs, text) == 3
    assert recs[0]["amount_rs"] == 809000 and recs[1]["amount_rs"] == 1599500    # rate and prior-page amount ignored
    assert recs[0]["owner_raw"].startswith("கந்தன் மகன் முருகன்")


def test_hectare_triples_beat_invented_labels_and_serial_swap():
    # continuation page: the VLM invented English headers over the wrong columns
    t = {"columns": ["Survey No.", "Extent (Hectares)", "Extent (Acres)", "Owner(s)"],
         "rows": [["12", "29/9", "0.45.50", "அ மகன் ஆ"], ["13", "29/10", "0.63.00", "இ மகன் ஈ"]]}
    cm = map_parcel_columns(prepare_table(t), "AWARD_7_3")
    assert cm.fields[:3] == ["serial", "survey_no", "extent_ha"] and "extent_ac" not in cm.fields


def test_two_hectare_columns_7_3():
    t = {"columns": ["வ.எண்", "புல எண்", "மொத்த விஸ்தீரணம் (ஹெக்டேர்)", "நிலஎடுப்பு விஸ்தீரணம் (ஹெக்டேர்)", "உரிமையாளர்"],
         "rows": [["1", "29/1A", "0.10.00", "0.05.00", "அ மகன் ஆ"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_3", "parcel", dict(CTX), "", {}, [])
    assert recs[0]["extent_ha"] == 0.05 and recs[0]["extent_ac"] is None
    assert recs[0]["raw"]["extent_ha_total_value"] == "0.1"


@pytest.mark.parametrize("cell,expected", [
    ("முருகன்\nத/பெ. சாமியாபிள்ளை", "முருகன் த/பெ. சாமியாபிள்ளை"),
    ("1.அ மகன் ஆ\n2.இ மகன் ஈ", "அ மகன் ஆ ; இ மகன் ஈ"),
    ("அ த.பெ. ஆ\nமற்றும் 1 நபர்", "அ த.பெ. ஆ மற்றும் 1 நபர்"),
    ("அ மகன் ஆ\\n2.இ மகன் ஈ", None),     # escaped newline from the VLM is normalised by mapping.norm
])
def test_owner_line_continuation(cell, expected):
    if expected is not None:
        assert owner_display(cell) == expected


def test_treasury_advice_headers_and_tables():
    lines = Lines(None, "Government of TamilNadu\nPayment Advice - (Treasury)\n"
                        "Treasury Reference Number 2007005600000001\nTreasury Reference Date 25-10-2024\n"
                        "Land Acquisition Compensation Unit 2")
    h = extract_headers(lines, None, "DISBURSEMENT")
    assert h["doc_no"]["value"] == "2007005600000001" and h["doc_date"]["value"] == "2024-10-25"
    assert h["unit_no"]["value"] is None and h["village"]["value"] is None
    ben = {"columns": ["Beneficiary Name", "Payment Type", "Bank Account No", "Bank IFSC", "Amount"],
           "rows": [["A KUMAR", "NEFT", "123456789012", "ABCD0000001", "1740000.00"],
                    ["B DEVI", "NEFT", "123456789013", "ABCD0000002", "120500.00"],
                    ["Total", None, None, None, "1860500.00"]]}
    kv = {"columns": ["Drawing Officer", "Details"], "rows": [["Treasury/PAO", "ST TUTICORIN"], ["HOD", "X"]]}
    debit = {"columns": ["DEBIT", "Amount", "CREDIT"], "rows": [["8443001 Not Bearing Interest", "1860500", None]]}
    recs, tot, _ = build_records({"tables": [kv, ben, debit]}, "DISBURSEMENT", "owner_amount", dict(CTX), "", {}, [])
    assert [(r["owner_raw"], r["amount_rs"]) for r in recs] == [("A KUMAR", 1740000), ("B DEVI", 120500)]
    assert tot["amount_rs"] == 1860500
    assert summarise(run_checks(recs, tot, {}, "owner_amount")) == "pass"


def test_second_read_must_not_lose_coverage():
    rec = lambda s: {"record_type": "parcel_row", "survey_no": s, "sub_div": None}  # noqa: E731
    first = {"self_consistency": "fail", "checks": [{"result": "fail"}],
             "records": [rec("1"), rec("2"), rec("3"), rec("4")]}
    second = {"self_consistency": "pass", "checks": [{"result": "pass"}], "records": [rec("1")]}
    assert improves_consistency(first, second) is False
    second_full = {**second, "records": first["records"]}
    assert improves_consistency(first, second_full) is True


def test_upright_rotates_sideways_scan(tmp_path, monkeypatch):
    import pipeline.extract.page as page_mod
    monkeypatch.setattr(page_mod, "ROTATED_DIR", tmp_path / "rot")
    p = tmp_path / "side.png"
    Image.new("RGB", (300, 200), "white").save(p)

    class OCR:
        def __init__(self, rot, conf):
            self.r = (rot, conf)

        def detect_orientation(self, _):
            return self.r
    out, rot = upright(p, OCR(90, 9.8), "ab" * 32)
    assert rot == 90 and Image.open(out).size == (200, 300)
    assert upright(p, OCR(90, 0.5), "cd" * 32) == (p, 0)        # low OSD confidence: leave as is
    assert upright(p, OCR(0, 9.0), "ef" * 32) == (p, 0)


def test_upright_ignores_weak_180_and_portrait_sideways(tmp_path, monkeypatch):
    import pipeline.extract.page as page_mod
    monkeypatch.setattr(page_mod, "ROTATED_DIR", tmp_path / "rot")
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (200, 300), "white").save(portrait)

    class OCR:
        def __init__(self, rot, conf):
            self.r = (rot, conf)

        def detect_orientation(self, _):
            return self.r
    # upright LDR table page: OSD "180, 1.99" must not flip it (loop-2 regression)
    assert upright(portrait, OCR(180, 1.99), "11" * 32) == (portrait, 0)
    assert upright(portrait, OCR(180, 5.0), "12" * 32) == (portrait, 0)
    assert upright(portrait, OCR(90, 3.0), "13" * 32) == (portrait, 0)   # portrait image: needs ≥ 8
    out, rot = upright(portrait, OCR(180, 9.0), "14" * 32)
    assert rot == 180 and Image.open(out).size == (200, 300)


def test_fallback_model_cache_hit_is_upgraded_when_live(tmp_path, monkeypatch):
    import sys
    import types

    from pipeline.extract import vlm as vlm_mod
    from pipeline.extract.cache import ContentCache
    img = tmp_path / "p.png"
    Image.new("RGB", (400, 600), "white").save(img)
    cache = ContentCache(tmp_path / "c")
    models = iter(["groq-qwen-vl", "bedrock-ministral-8b"])

    def fake_call(task, payload, **kw):
        return types.SimpleNamespace(ok=True, data={"tables": []}, model_id=next(models), tokens_in=1,
                                     tokens_out=1, shadow_cost_usd=0.0, error=None)
    fake = types.ModuleType("app.router")
    fake.call = fake_call
    monkeypatch.setitem(sys.modules, "app.router", fake)
    first = vlm_mod.transcribe(img, "LDR", "PII", "primary", cache=cache)
    assert first["model_id"] == "groq-qwen-vl" and not first["from_cache"]
    assert vlm_mod.transcribe(img, "LDR", "PII", "primary", cache=cache, allow_calls=False)["from_cache"]
    second = vlm_mod.transcribe(img, "LDR", "PII", "primary", cache=cache)
    assert second["model_id"] == "bedrock-ministral-8b" and not second["from_cache"]
    assert vlm_mod.transcribe(img, "LDR", "PII", "primary", cache=cache)["from_cache"]


def test_ldr_blurred_rows_are_not_totals_and_no_swap():
    # LDR layout (non-held-out page shape): serial by header, small sequential survey numbers,
    # boundary columns full of survey refs, a blurred row that lost serial + survey
    t = {"columns": ["Serial No", "Survey No", "Sub Division", "Extent", "North", "East", "South", "West"],
         "rows": [["1", "1", "1", "0.30.60", "2/1", "3/1", "4/1", "5/1"],
                  [None, None, "2", "0.30.60", "2/2", "3/2", "4/2", "5/2"],
                  ["3", "2", "1", "0.40.00", "6/1", "7/1", "8/1", "9/1"],
                  ["4", "3", "1", "0.50.00", "6/2", "7/2", "8/2", "9/2"],
                  [None, None, None, "1.51.20", None, None, None, None]]}
    recs, tot, _ = build_records({"tables": [t]}, "LDR", "parcel", dict(CTX), "", {}, [])
    assert [r["extent_ha"] for r in recs] == [0.306, 0.306, 0.4, 0.5]
    assert [r["survey_no"] for r in recs] == ["1", "1", "2", "3"]     # merged survey cell filled down
    assert tot["_per_table"][0]["extent_ha"] == 1.512


# ---- repair loop 3 (dev2: large 7(2) schedules, apportionment + heirs tables) ----
def test_apportionment_table_with_invented_labels_and_wrapped_rows():
    # sideways page: invented English headers; share split over two columns; amount duplicated on
    # the continuation line; a label says "Survey Number" but no cell is a survey
    t = {"columns": ["Sl.No", "Description", "Survey Number", "Extent", "Total Amount (₹)"],
         "rows": [["24.", "அ க/பெ ஆ", "6-ல் 1 பங்கில் (1/6)", "5-ல் 1 பங்கு (1/5)", "60,934"],
                  [None, None, "6-ல் 1 பங்கில் (1/6)", "5-ல் 1 பங்கு (1/5)", "60,934"],
                  ["25.", "இ த/பெ ஈ", "6-ல் 1 பங்கில் (1/6)", None, None],
                  [None, None, "5-ல் 1 பங்கு (1/5)", None, "60,933"]]}
    ctx = dict(CTX)
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", ctx, "", {}, [])
    assert [(r["owner_raw"], r["amount_rs"]) for r in recs] == [("அ க/பெ ஆ", 60934), ("இ த/பெ ஈ", 60933)]
    assert all(r["extent_ha"] is None and r["extent_ac"] is None for r in recs)   # shares are never extents
    assert recs[1]["share"].startswith("6-ல் 1 பங்கில்") and "5-ல் 1 பங்கு" in recs[1]["share"]


def test_share_column_headed_as_acres_is_not_an_extent():
    t = {"columns": ["வ. எண்", "பெயர்", "இழப்பீட்டு விஸ்தீரணம் (ஏக்கரில்)", "நிலத்திற்கான இழப்பீட்டுத் தொகை (ரூ)"],
         "rows": [["1.", "அ", "6-ல் 1 பங்கு", "55,584"], ["2.", "ஆ", "6-ல் 1 பங்கு", "55,584"],
                  ["மொத்தம்", None, "0.667", "1,11,168"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [(r["amount_rs"], r["extent_ac"]) for r in recs] == [(55584, None), (55584, None)]


def test_heirs_table_rows_are_kept():
    t = {"columns": ["வ. எண்", "வாரிசுதாரர்களின் பெயர்", "இறந்தவருக்கான உறவுமுறை"],
         "rows": [["1.", "அன்னம்", "மனைவி"], ["2.", "விஜயா", "மகள்"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [(r["owner_raw"], r["relation"], r["table_role"]) for r in recs] == [
        ("அன்னம்", "மனைவி", "heirs"), ("விஜயா", "மகள்", "heirs")]
    assert summarise(run_checks(recs, {}, {}, "parcel")) in {"pass", "n/a"}


def test_merged_owner_on_total_row_fills_the_table():
    t = {"columns": ["புல எண்", "விஸ்தீரணம் / ஹெக்", "விஸ்தீரணம் / ஏக்", "நில உரிமையாளர்"],
         "rows": [["283/1B", "0.81.00", "2.00", None], ["283/1C", "0.40.00", "0.988", None],
                  ["மொத்தம்", "1.21.00", "2.988", "அக்ரோ நிறுவனம் லிமிடெட்"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [r["owner_raw"] for r in recs] == ["அக்ரோ நிறுவனம் லிமிடெட்"] * 2


def test_owner_cell_label_line_dropped():
    t = {"columns": LAND_COLS, "rows": [["1", "12/1", "1.00.00", "2.47", "5", "நில உரிமையாளர்\nஅ மகன் ஆ"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert recs[0]["owner_raw"] == "அ மகன் ஆ"


def test_tesseract_owner_vote_fixes_vlm_spelling_only_when_close():
    from pipeline.extract.records import apply_tesseract_owner_votes
    text = ("இந்த நிலம் எர்த் வானம் அக்ரோ ப்ராடக்ட்ஸ் பிரைவேட் லிமிடெட் நிறுவனத்தின் பெயரில் உள்ளது "
            "மேற்படி புல எண் விஸ்தீரணம் ஹெக்டேர் ஏக்கர் பட்டா உரிமையாளர் இழப்பீடு தொகை வழங்க ஆணை இடப்படுகிறது "
            "இிரைவேட் இிரைவேட்")
    recs = [{"record_type": "parcel_row", "owner_raw": "ஏற் வானம் அக்ரோ பிராட்க்ட்ஸ் பிநரவேட் லிமிடெட்"},
            {"record_type": "parcel_row", "owner_raw": "முற்றிலும் வேறு பெயர்"}]
    assert apply_tesseract_owner_votes(recs, text) == 1
    assert recs[0]["owner_raw"] == "ஏற் வானம் அக்ரோ ப்ராடக்ட்ஸ் பிரைவேட் லிமிடெட்"   # debris "இி…" never chosen
    assert recs[0]["owner_vlm_raw"].startswith("ஏற்") and recs[1]["owner_raw"] == "முற்றிலும் வேறு பெயர்"


# ---- loop-3 follow-up (held-out diagnosis: merged survey cells, summary tables) + D-040 ----
def test_award_merged_survey_cell_fills_rows():
    t = {"columns": ["வ. எண்", "புல எண்", "உட்பிரிவு", "விஸ்தீரணம் ஹெக்.", "விஸ்தீரணம் ஏக்.", "நில உரிமையாளர்"],
         "rows": [["1", "283", "1B", "0.81.00", "2.00", "அ நிறுவனம்"],
                  ["2", None, None, "0.40.00", "0.988", None],
                  ["3", None, None, None, "1.037", None]]}
    ctx = dict(CTX)
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", ctx, "", {}, [])
    assert [r["survey_no"] for r in recs] == ["283", "283", "283"]
    assert [r["owner_raw"] for r in recs] == ["அ நிறுவனம்"] * 3
    assert "survey_no" in recs[1]["filled_down"]


def test_summary_items_table_is_not_a_parcel_table():
    from pipeline.extract.validate import validate_record
    t = {"columns": ["வ. எண்", "விவரம்", "விஸ்தீரணம் (ஹெக்டேர்)", "விஸ்தீரணம் (ஏக்கர்)", "தொகை (ரூ)"],
         "rows": [["1", "பட்டா நிலம்", "10.87.50", "26.87", "1,34,35,000"],
                  ["2", "அரசு புறம்போக்கு நிலம்", "0.50.00", "1.235", "-"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [r["record_type"] for r in recs] == ["summary_item", "summary_item"]
    assert (recs[0]["extent_ha"], recs[0]["extent_ac"], recs[0]["amount_rs"]) == (10.875, 26.87, 13435000)
    assert all(validate_record(r, "AWARD_7_2") == [] for r in recs)


def test_d040_carried_headers_excluded_both_sides():
    from eval.extraction.score import carried_gold_fields, score_page
    gold = {"page_id": "x", "doc_type": "AWARD_7_2", "rows": [],
            "header": {"village": "Peroorani", "unit_no": "5", "block_no": "1", "doc_date": None, "doc_no": None},
            "notes": "Continuation of g04; village/unit/block carried from g04 text (not printed on this page)"}
    assert carried_gold_fields(gold) == {"village", "unit_no", "block_no"}
    pred = {"doc_type": "AWARD_7_2", "records": [], "header": {
        "village": {"value": "Allikulam", "header_source": "carried"}, "unit_no": {"value": None},
        "block_no": {"value": None}, "doc_date": {"value": None}, "doc_no": {"value": None}}}
    t = score_page(gold, pred)["tally"].c
    assert t["header_acc"] == {"tp": 2, "fp": 0, "fn": 0}          # only doc_date + doc_no scored
    gold2 = {**gold, "header": {**gold["header"], "village": None}, "notes": ""}
    t2 = score_page(gold2, pred)["tally"].c                          # pred carried, gold null -> skipped
    assert t2["header_acc"]["fn"] == 2                                # unit/block missing still count


def test_isolated_missing_survey_is_not_filled_from_above():
    t = {"columns": ["வ. எண்", "புல எண்", "விஸ்தீரணம் ஹெக்.", "நில உரிமையாளர்"],
         "rows": [["1", "168", "1.61.00", "அ"], ["2", "169/1", "0.32.50", "அ"], ["3", "170/2", "0.47.00", "அ"],
                  ["4", None, "3.96.32", "ஆ"], ["5", "171/2", "0.73.68", "இ"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_3", "parcel", dict(CTX), "", {}, [])
    assert recs[3]["survey_no"] is None                     # a misread gap, not a merged cell


def test_compensation_groups_with_survey_in_serial_column():
    # exact held-out shape (synthetic values): [serial, survey, name, extent, amount]; the VLM wrote
    # the survey numbers into the SERIAL column and left the survey column empty; each survey spans
    # a group of owner rows; only the group head has the extent; last row is the total
    t = {"columns": ["வ. எண்", "புல எண்", "பெயர்", "விஸ்தீரணம் (ஹெக்)", "இழப்பீட்டுத் தொகை (ரூ)"],
         "rows": [["45/2", None, "அ த/பெ ஆ", "0.40.00", "4,94,000"],
                  [None, None, "இ த/பெ ஈ", None, "2,47,000"],
                  [None, None, "உ க/பெ ஊ", None, "2,47,000"],
                  ["46/1A", None, "எ த/பெ ஏ", "0.20.00", "4,94,000"],
                  [None, None, "ஐ த/பெ ஒ", None, "2,47,000"],
                  [None, None, None, "0.60.00", "17,29,000"]]}
    recs, tot, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [(r["survey_no"], r["sub_div"]) for r in recs] == [("45", "2")] * 3 + [("46", "1A")] * 2
    assert [r["extent_ha"] for r in recs] == [0.4, None, None, 0.2, None]          # extent on the head only
    assert [r["amount_rs"] for r in recs] == [494000, 247000, 247000, 494000, 247000]
    assert recs[1]["group_ref"] == recs[0]["group_ref"] != recs[3]["group_ref"] == recs[4]["group_ref"]
    assert "survey_no:group" in recs[2]["filled_down"]
    per = tot["_per_table"][0]
    assert per["extent_ha"] == 0.6 and per["amount_rs"] == 1729000               # total row stays a total


def test_group_extent_moves_from_centred_member_to_group_head():
    # held-out shape: the merged extent cell is vertically centred, so the VLM put it on a middle
    # row of the group; acres absent even though the header names acres (never derived)
    t = {"columns": ["வ. எண்", "புல எண்", "பெயர்", "விஸ்தீரணம் (ஹெக்)", "விஸ்தீரணம் (ஏக்கர்)", "இழப்பீட்டுத் தொகை (ரூ)"],
         "rows": [["45/2", None, "அ த/பெ ஆ", None, None, "2,47,000"],
                  [None, None, "இ த/பெ ஈ", "0.40.00", None, "2,47,000"],
                  [None, None, "உ க/பெ ஊ", None, None, "2,47,000"],
                  ["46/1A", None, "எ த/பெ ஏ", "0.20.00", None, "4,94,000"],
                  [None, None, "ஐ த/பெ ஒ", None, None, "2,47,000"]]}
    recs, _, _ = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert [r["survey_no"] + "/" + r["sub_div"] for r in recs] == ["45/2"] * 3 + ["46/1A"] * 2
    assert [r["extent_ha"] for r in recs] == [0.4, None, None, 0.2, None]
    assert recs[0]["extent_aligned"] == "group_head" and "extent_aligned" not in recs[3]
    assert all(r["extent_ac"] is None for r in recs)                         # acres never invented
    assert len({recs[0]["group_ref"], recs[1]["group_ref"], recs[2]["group_ref"]}) == 1
