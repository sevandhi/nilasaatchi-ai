"""Mapping / headers / doc-type / consistency on synthetic inputs (no golden PII)."""
import pytest

from pipeline.extract.consistency import run_checks, summarise
from pipeline.extract.doctype import resolve_doc_type
from pipeline.extract.headers import Lines, extract_headers
from pipeline.extract.mapping import cell_type, map_parcel_columns, map_village_columns, prepare_table
from pipeline.extract.records import apply_page_classification, apply_prose_survey, build_records

CTX = {"doc_id": "t", "page_no": 1, "privacy_tier": "PII", "source_engine": "vlm:test", "model_id": "m"}


@pytest.mark.parametrize("raw,t", [("0.95.50", "ha"), ("0 - 53.50", "ha"), ("173/1", "survey"), ("2.35", "dec"),
                                   ("12", "int"), ("1,35,496/-", "money"), ("திரு. ராமன்", "text"), ("--", "empty"),
                                   ("4A", "subdiv")])
def test_cell_type(raw, t):
    assert cell_type(raw) == t


def test_parcel_mapping_noisy_headers():
    # VLM header noise seen on golden pages: வெக்டர் (ஹெக்டேர்), ஒக்கர் (ஏக்கர்), உதமிதாரர் (உடமைதாரர்)
    t = {"columns": ["வ.எண்", "புல எண்", "வெக்டர்", "ஒக்கர்", "நில உதமிதாரர்களின் விபரம்"],
         "rows": [["1", "168", "1.61.00", "3.98", "சோதனை பெயர் ஒன்று"], ["2", "169/1", "0.32.50", "0.80", None],
                  ["மொத்தம்", None, "1.93.50", "4.78", None]]}
    pt = prepare_table(t)
    cm = map_parcel_columns(pt, "AWARD_7_3")
    assert cm.fields == ["serial", "survey_no", "extent_ha", "extent_ac", "owner"]
    recs, tot, _ = build_records({"tables": [t]}, "AWARD_7_3", "parcel", CTX, "", {}, [])
    assert [r["extent_ha"] for r in recs] == [1.61, 0.325]
    assert recs[1]["owner_raw"] == recs[0]["owner_raw"] and "owner_raw" in recs[1]["filled_down"]
    assert tot["extent_ha"] == pytest.approx(1.935)
    checks = run_checks(recs, tot, {}, "parcel")
    assert summarise(checks) == "pass"
    for r in recs:  # evidence on every record
        assert {"doc_id", "page", "bbox", "source_engine", "model_id", "confidence"} <= set(r["evidence"])


def test_printed_total_mismatch_fails():
    t = {"columns": ["புல எண்", "விஸ்தீரணம் ஹெக்."], "rows": [["1/1", "0.50.00"], ["1/2", "0.40.00"],
                                                               ["மொத்தம்", "1.00.00"]]}
    recs, tot, _ = build_records({"tables": [t]}, "LDR", "parcel", CTX, "", {}, [])
    assert summarise(run_checks(recs, tot, {}, "parcel")) == "fail"


def test_village_totals_by_arithmetic():
    # garbled identical labels: patta + poramboke = total is recovered from the numbers
    t = {"columns": ["கிராமம்", "x", "x", "x", "x"],
         "rows": [["அல்லிகுளம்", "185.96.50", "0.49.00", "186.45.50", "460.54"],
                  ["பேரூரணி", "10.00.00", "1.00.00", "11.00.00", "27.17"]]}
    cm = map_village_columns(prepare_table(t))
    assert cm.fields == ["village", "patta_ha", "poramboke_ha", "total_ha", "total_ac"]
    recs, tot, _ = build_records({"tables": [t]}, "AS", "village_totals", CTX, "", {}, [])
    assert recs[0]["village"] == "Allikulam" and recs[1]["village"] == "Peroorani"
    assert summarise(run_checks(recs, tot, {}, "village_totals")) == "pass"


def test_prose_survey_fill_skips_present_and_merged():
    recs = [{"record_type": "parcel_row", "survey_no": None, "sub_div": None},
            {"record_type": "parcel_row", "survey_no": "173", "sub_div": "2"}]
    text = "மேற்படி புல எளர் 173/1 விஸ்தீரணம் 0.95.50\nமேற்படி புல எண்: 173/20.86.00 ஹெக்டேர்"
    apply_prose_survey(recs, text)
    assert (recs[0]["survey_no"], recs[0]["sub_div"]) == ("173", "1")


def test_page_classification():
    recs = [{"record_type": "parcel_row", "survey_no": "1", "classification": None}]
    apply_page_classification(recs, "அலகு - 1 வகைபாடு: புன்செய்")
    assert recs[0]["classification"] == "DRY"
    recs = [{"record_type": "parcel_row", "survey_no": "1", "classification": None}]
    apply_page_classification(recs, "புன்செய் நிலம் ... நன்செய் நிலம்")
    assert recs[0]["classification"] is None


@pytest.mark.parametrize("text,expected", [
    ("LAND DELIVERY CERTIFICATE\nVillage : Allikulam", "LDR"),
    ("Total compensation amount apportioned to the land owners", "FORM_F"),
    ("வட்டாட்சியர் அலுவலக இணைய சேவை - நில உரிமை விபரங்கள்", "CHITTA"),
    # award preamble citing 3(1)/3(2) and G.O. numbers must not override the prior (D-030)
    ("பார்வை: அரசாணை நிலை எண்.29 ... பிரிவு 3(2)ன் கீழ் ... 7(2)", "AWARD_7_2"),
])
def test_doc_type_cues(text, expected):
    assert resolve_doc_type(None, text, None, "AWARD_7_2")[0] == expected


def test_headers_multi_village_blocks_vlm_guess():
    lines = Lines(None, "Table - 1\nVillage Melathattaparai and Perurani\nDistrict Thoothukudi")
    h = extract_headers(lines, {"village": "Melathattaparai"}, "SLPNC")
    assert h["village"]["value"] is None


def test_headers_labelled_village_unit_block_date():
    lines = Lines(None, "ந.க.அ5/3/2021 நாள்: 02.12.2021\nகிராமம் : பேரூரணி அலகு: 5 பிளாக் : 1")
    h = extract_headers(lines, None, "SEC32_NOTICE")
    assert h["village"]["value"] == "Peroorani"
    assert h["unit_no"]["value"] == "5" and h["block_no"]["value"] == "1"
    assert h["doc_date"]["value"] == "2021-12-02"


def test_headers_scheme_name_is_not_a_village():
    lines = Lines(None, "Sub: LA - SIPCOT Allikulam Scheme - Administrative Sanction")
    assert extract_headers(lines, None, "AS")["village"]["value"] is None


@pytest.mark.parametrize("text,expected", [("ட படிவம்-பி (அதி 4-௯ பார்க்கவும்)", "SEC32_NOTICE"),
                                           ("படிவம் - E\n(விதி 9-ஐ பார்க்க)", "FORM_E")])
def test_tamil_form_letters(text, expected):
    # a Tamil vowel sign is not a regex word char: \b after "பி" never matched
    assert resolve_doc_type(None, text, None, None)[0] == expected


def test_split_merged_ha_ac_column():
    t = {"columns": ["SL. NO", "SUR VEY NO", "SUB DIVISION", "Hec. / Acre", "North"],
         "rows": [["1", "1", "1", "0.60.50 / 1.494", "1/2"], ["2", "1", "2", "0.60.00 / 1.482", "2/1"]]}
    recs, _, _ = build_records({"tables": [t]}, "LDR", "parcel", CTX, "", {}, [])
    assert [(r["extent_ha"], r["extent_ac"]) for r in recs] == [(0.605, 1.494), (0.6, 1.482)]


def test_village_poramboke_label_variant_and_unlabelled_total():
    t = {"columns": ["கிராமம்", "நில எடுப்பு விஸ்தீரணம் (ஹெக்டேர், ஏக்கர்)", "நில உரிமை மாறும் பூமி போக்கு"],
         "rows": [["அல்லிக்குளம்", "185.96.50", "0.49.00"], ["இராமசாமிபுரம்", "95.23.00", "0"],
                  [None, "281.19.50", "0.49.00"]]}
    recs, tot, _ = build_records({"tables": [t]}, "GO", "village_totals", CTX, "", {}, [])
    assert [(r["village"], r["patta_ha"], r["poramboke_ha"]) for r in recs] == [
        ("Allikulam", 185.965, 0.49), ("Ramasamypuram", 95.23, 0.0)]
    assert tot["patta_ha"] == 281.195
    assert summarise(run_checks(recs, tot, {}, "village_totals")) == "pass"


def test_prose_sentence_in_owner_column_is_dropped():
    sentence = ("நிலம் கையகப்படுத்துவது தொடர்பாக மாவட்ட அளவிலான குழுக் கூட்டம் மாவட்ட ஆட்சியர் "
                "அவர்கள் தலைமையில் இரண்டு நாட்களில் நடைபெற்றது என்று இங்கு பதிவு செய்யப்படுகிறது.")
    t = {"columns": ["புல எண்", "விஸ்தீரணம்", "உரிமையாளர்"], "rows": [["21", "11.31.00", sentence]]}
    recs, _, flags = build_records({"tables": [t]}, "AWARD_7_2", "parcel", dict(CTX), "", {}, [])
    assert recs == [] and flags == ["prose_as_table:1"]


def test_schema_errors_never_embed_instance():
    from pipeline.extract.validate import validate_record
    rec = {"record_type": "parcel_row", "owner_raw": "PRIVATE NAME", "owners": [], "evidence": {}}
    errs = validate_record(rec, "AWARD_7_2")
    assert errs and not any("PRIVATE NAME" in e for e in errs)
