from pipeline.classify.scheme import aggregate_doc_scheme, classify_scheme


def test_allikulam_by_name():
    assert classify_scheme("SIPCOT Allikulam Scheme, Ramasamypuram village") == "allikulam"


def test_allikulam_by_oil_refinery_alias():
    assert classify_scheme("Formation of Oil Refinery Project by SIPCOT, Allikulam") == "allikulam"


def test_allikulam_by_village_name_alone():
    assert classify_scheme("Notice concerning Keelathattaparai survey numbers") == "allikulam"


def test_other_scheme_solar():
    assert classify_scheme("SIPCOT Establishment of Solar Power Plant, Tirunelveli district, Manur taluk") == "other_scheme"


def test_unknown_when_neither():
    assert classify_scheme("An unrelated Chennai railway errata notice, Tondiarpet division") == "unknown"


def test_allikulam_wins_when_both_present_on_one_page():
    text = "Allikulam scheme notice, also referencing Tirunelveli district boundary for context"
    assert classify_scheme(text) == "allikulam"


def test_aggregate_prefers_allikulam_over_other():
    assert aggregate_doc_scheme(["unknown", "allikulam", "other_scheme"]) == "allikulam"


def test_aggregate_other_scheme_when_no_allikulam():
    assert aggregate_doc_scheme(["unknown", "other_scheme"]) == "other_scheme"


def test_aggregate_unknown_when_all_unknown():
    assert aggregate_doc_scheme(["unknown", "unknown"]) == "unknown"


def test_aggregate_empty_list():
    assert aggregate_doc_scheme([]) == "unknown"
