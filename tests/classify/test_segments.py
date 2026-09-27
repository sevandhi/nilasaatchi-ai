from pipeline.classify.segments import build_segments, doc_level_scheme


def test_single_scheme_document_is_one_segment():
    pages = [
        "GOVERNMENT GAZETTE EXTRAORDINARY PUBLISHED BY AUTHORITY Allikulam scheme notice",
        "Continuing the Allikulam s.3(1) notice, survey numbers listed below",
    ]
    segs = build_segments(pages)
    assert len(segs) == 1
    assert segs[0].page_start == 1
    assert segs[0].page_end == 2
    assert segs[0].scheme_relevance == "allikulam"


def test_mixed_gazette_produces_three_segments_like_527():
    pages = [
        "An unrelated Chennai railway errata notice, Tondiarpet division",             # p1: unknown
        "SIPCOT Allikulam Scheme s.3(1) notice, Ramasamypuram village",                  # p2: allikulam
        "continuing Allikulam notice, survey numbers of Ramasamypuram",                  # p3: allikulam
        "SIPCOT Establishment of Solar Power Plant, Tirunelveli district, Manur taluk",  # p4: other_scheme
    ]
    segs = build_segments(pages)
    schemes = [s.scheme_relevance for s in segs]
    assert schemes == ["unknown", "allikulam", "other_scheme"]
    assert segs[1].page_start == 2 and segs[1].page_end == 3
    assert segs[2].page_start == 4 and segs[2].page_end == 4


def test_doc_level_scheme_is_allikulam_for_mixed_document():
    pages = [
        "An unrelated Chennai railway errata notice",
        "SIPCOT Allikulam Scheme s.3(1) notice, Ramasamypuram village",
        "SIPCOT Establishment of Solar Power Plant, Tirunelveli district",
    ]
    segs = build_segments(pages)
    assert doc_level_scheme(segs) == "allikulam"


def test_empty_pages_returns_no_segments():
    assert build_segments([]) == []


def test_segment_doc_type_uses_rule_engine_on_segment_text():
    pages = ["FORM E (See Rule 8) notice under section 4(2) to surrender possession"]
    segs = build_segments(pages)
    assert segs[0].doc_type == "FORM_E"
    assert segs[0].stage == "POSSESSION_NOTICE"
