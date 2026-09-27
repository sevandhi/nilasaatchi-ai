from pipeline.classify import run as run_module
from pipeline.classify.run import DocInput, classify_one, reset_pending


def _doc(pages=None) -> DocInput:
    return DocInput(
        id=1, sha256="deadbeef", path="Dataset/Land_documents/Funds-Allocated/fund1.pdf",
        folder_label="Funds-Allocated",
        pages=pages or [(1, "some text", True, "data/pages/de/deadbeef/1.webp")],
    )


def test_stage_always_derived_from_doc_type_not_llm_stage(monkeypatch):
    # Regression (round 1 of D-030 tuning): the LLM can return an internally inconsistent
    # doc_type/stage pair (FUNDS docs came back with stage=PAYMENT instead of PRICE_NEGOTIATION).
    # `stage` must always be recomputed from `doc_type` via the fixed taxonomy table.
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": "FUNDS", "stage": "PAYMENT",  # deliberately wrong/inconsistent stage
        "scheme_relevance": "allikulam", "confidence": 0.9, "rationale": "fund allocation",
        "_meta": {"model_id": "fake", "task": "classify_text", "from_cache": False},
    })

    result = classify_one(_doc(), {1: "some text"})

    assert result["classified_type"] == "FUNDS"
    assert result["stage"] == "PRICE_NEGOTIATION"


def test_missing_doc_type_is_left_none_and_marked_pending(monkeypatch):
    # Incident regression (2026-09-27): a failed/fallback call must NEVER be written as a
    # fabricated "OTHER" classification -- classified_type stays None so the caller (`run`)
    # knows to leave the document's existing value untouched and only mark it pending.
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": None, "_meta": {"error": "provider down", "from_cache": False,
                                     "sso_expired": False, "fallback_model": False},
    })

    result = classify_one(_doc(), {1: "some text"})

    assert result["classified_type"] is None
    assert result["status"] == "pending"
    assert result["segments"] == []


def test_fallback_model_answer_is_treated_as_missing_not_accepted(monkeypatch):
    # Only Bedrock Ministral answers are accepted (post-incident directive); llm_classifier
    # already returns doc_type=None for a fallback-model answer, so classify_one's behaviour
    # is identical to any other failure -- pending, nothing overwritten.
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": None, "_meta": {"error": "non-Bedrock fallback model answered: groq-gpt-oss",
                                     "model_id": "groq-gpt-oss", "fallback_model": True,
                                     "from_cache": False, "sso_expired": False},
    })

    result = classify_one(_doc(), {1: "some text"})

    assert result["classified_type"] is None
    assert result["status"] == "pending"
    assert result["type_evidence"]["fallback_model"] is True


def test_segments_default_to_whole_document_when_llm_omits_them(monkeypatch):
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": "CHITTA", "scheme_relevance": "allikulam", "confidence": 0.8,
        "rationale": "chitta extract", "_meta": {"from_cache": False},
    })

    result = classify_one(_doc(), {1: "some text"})

    assert len(result["segments"]) == 1
    assert result["segments"][0]["doc_type"] == "CHITTA"
    assert result["segments"][0]["page_start"] == 1
    assert result["segments"][0]["page_end"] == 1


def test_llm_segments_are_used_when_present(monkeypatch):
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": "SEC31_GAZETTE", "scheme_relevance": "allikulam", "confidence": 0.9,
        "rationale": "gazette", "_meta": {"from_cache": False},
        "segments": [
            {"page_from": 1, "page_to": 1, "doc_type": "OTHER", "scheme_relevance": "unknown"},
            {"page_from": 2, "page_to": 3, "doc_type": "SEC31_GAZETTE", "scheme_relevance": "allikulam"},
        ],
    })
    pages = [(1, "", True, None), (2, "", True, None), (3, "", True, None)]

    result = classify_one(_doc(pages=pages), {1: "", 2: ""})

    assert len(result["segments"]) == 2
    assert result["segments"][0]["stage"] is None  # OTHER has no stage
    assert result["segments"][1]["stage"] == "SEC_3_1"


def test_reset_pending_clears_only_review_queue_rows():
    class _FakeResult:
        rowcount = 7

    class _FakeConn:
        def __init__(self):
            self.executed = []
            self.committed = False

        def execute(self, sql, *args):
            self.executed.append(sql)
            return _FakeResult()

        def commit(self):
            self.committed = True

    conn = _FakeConn()
    n = reset_pending(conn)

    assert n == 7
    assert conn.committed is True
    assert "status = 'review_queue'" in conn.executed[0]
    assert "classified_type = NULL" in conn.executed[0]


def test_llm_date_is_normalized(monkeypatch):
    monkeypatch.setattr(run_module, "classify_document_llm", lambda **kwargs: {
        "doc_type": "LDR", "scheme_relevance": "allikulam", "confidence": 0.9,
        "doc_date": "21.03.2025",  # non-ISO, as an LLM can still answer despite instructions
        "rationale": "possession certificate", "_meta": {"from_cache": False},
    })

    result = classify_one(_doc(), {1: "some text"})

    assert result["doc_date"] == "2025-03-21"
