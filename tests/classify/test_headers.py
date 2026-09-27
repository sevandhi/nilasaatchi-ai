from pipeline.classify.headers import parse_headers


def test_unit_block_english():
    h = parse_headers("Unit-7 Block: 9 Allikulam village, Thoothukudi")
    assert h.unit_no == "7"
    assert h.block_no == "9"
    assert h.village == "Allikulam"


def test_unit_block_tamil():
    h = parse_headers("அலகு 4 பிளாக் 5 மேலத்தட்டப்பாறை")
    assert h.unit_no == "4"
    assert h.block_no == "5"
    assert h.village == "Melathattaparai"


def test_village_alias_variant():
    h = parse_headers("கீழத்தட்டப்பாறை கிராமம்")
    assert h.village == "Keelathattaparai"


def test_full_date_precision_day():
    h = parse_headers("dated 21.03.2025 the possession was handed over")
    assert h.doc_date == "2025-03-21"
    assert h.date_precision == "day"


def test_blank_day_date_precision_month():
    h = parse_headers("proceedings dated .02.2025 regarding the award")
    assert h.doc_date == "2025-02-01"
    assert h.date_precision == "month"


def test_doc_no_lr_no():
    h = parse_headers("Lr.No.LA/TUT/Allikulam/2020 dated 31.03.2021")
    assert h.doc_no == "LA/TUT/Allikulam/2020"


def test_doc_no_go_number():
    h = parse_headers("G.O.(Ms) No.100 dated 26.02.2021")
    assert h.doc_no == "100"


def test_doc_no_batch_id():
    h = parse_headers("Proceedings No. A7/10/Unit-7-Block-9/2024 dated 15.01.2024")
    assert h.doc_no == "A7/10/Unit-7-Block-9/2024"


def test_lang_detection_tamil():
    h = parse_headers("வருவாய் கிராமம் பேரூரணி பட்டா எண் 1663 " * 3)
    assert h.lang == "ta"


def test_lang_detection_english():
    h = parse_headers("GOVERNMENT OF TAMIL NADU GAZETTE EXTRAORDINARY " * 3)
    assert h.lang == "en"


def test_lang_detection_mixed():
    h = parse_headers("Government Gazette அரசு வர்த்தமானி Government Gazette அரசு வர்த்தமானி")
    assert h.lang == "ta+en"


def test_invalid_day_from_ocr_garbage_is_skipped_not_raised():
    # Regression: OCR garbage can run digits together (land-domain-knowledge §11b), producing
    # an out-of-range day ("86"). This must be skipped, not turned into an invalid ISO date
    # that would crash the `document.doc_date` UPDATE (DatetimeFieldOverflow).
    h = parse_headers("proceedings ref 04.86.2021 continued, actual date 21.03.2025 applies")
    assert h.doc_date == "2025-03-21"
    assert h.date_precision == "day"


def test_all_dates_invalid_returns_none():
    h = parse_headers("nonsense date 99.99.2021 only")
    assert h.doc_date is None
    assert h.date_precision is None


def test_normalize_llm_date_accepts_iso():
    from pipeline.classify.headers import normalize_llm_date
    assert normalize_llm_date("2021-03-15") == "2021-03-15"


def test_normalize_llm_date_falls_back_to_ddmmyyyy():
    # Regression: an LLM asked for ISO can still answer in the document's own dd.mm.yyyy
    # format; "15.03.2021" must not be misread as month=15 (DatetimeFieldOverflow).
    from pipeline.classify.headers import normalize_llm_date
    assert normalize_llm_date("15.03.2021") == "2021-03-15"


def test_normalize_llm_date_rejects_garbage():
    from pipeline.classify.headers import normalize_llm_date
    assert normalize_llm_date("not a date") is None
    assert normalize_llm_date(None) is None
    assert normalize_llm_date("2021-15-99") is None


def test_empty_text_returns_all_none():
    h = parse_headers("")
    assert h.unit_no is None
    assert h.village is None
    assert h.doc_date is None
    assert h.lang is None
