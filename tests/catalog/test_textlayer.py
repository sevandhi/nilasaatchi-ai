from pipeline.catalog.textlayer import TEXT_OK_MIN_SCORE, extract_pages_text, text_quality


def test_text_quality_good_prose_passes():
    text = "வருவாய் கிராமம் : பேரூரணி Government Order dated 26.02.2021 Unit 4 Block 5 " * 3
    n, score, ok = text_quality(text)
    assert n >= 50
    assert score >= 0.8
    assert ok is True


def test_text_quality_garbage_fails():
    # OCR garbage: no dictionary-like tokens, mostly punctuation/symbols
    text = "%$#@ ]] ][ ;;; ,,, ... ***" * 5
    _n, _score, ok = text_quality(text)
    assert ok is False


def test_text_quality_rejects_non_unicode_font_encoding_garbage():
    # Regression (T1.6 evaluation): a legacy non-Unicode Tamil font (TSCII/Bamini-style) can
    # make pdftotext emit Latin-lookalike garbage that a naive "looks word-shaped" heuristic
    # would score as good text. Real dictionary-word matching must reject it.
    garbage = (
        "SIG`Gg LoITOLIL LLD, soflorroau=L QGaumn9yoJAt (.6r) (y.Gn.Qum) "
        "S6ofl orauLL QJGAmnIAIN0 (fl.r.) liesTL, AsebGousil yatGotflt "
        "ynssr soflswo0:sGLn. Qgr. Gyoshl, B.Sc. (Agri.), M.A."
    )
    _n, score, ok = text_quality(garbage)
    assert ok is False
    assert score < TEXT_OK_MIN_SCORE


def test_text_quality_accepts_real_english_gazette_prose():
    text = (
        "GOVERNMENT OF TAMIL NADU GAZETTE EXTRAORDINARY PUBLISHED BY AUTHORITY "
        "Notifications of interest to the General Public issued by Heads of Departments"
    )
    _n, score, ok = text_quality(text)
    assert ok is True
    assert score >= TEXT_OK_MIN_SCORE


def test_text_quality_empty_string():
    n, score, ok = text_quality("")
    assert (n, score, ok) == (0, 0.0, False)


def test_text_quality_short_text_below_min_chars():
    _n, _score, ok = text_quality("hi")
    assert ok is False


def test_extract_pages_text_missing_binary_returns_blanks(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("pdftotext not found")

    monkeypatch.setattr(subprocess, "run", boom)
    pages = extract_pages_text("no_such_file.pdf", n_pages=3)
    assert pages == ["", "", ""]


def test_extract_pages_text_splits_on_form_feed(monkeypatch, tmp_path):
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = b"page one\x0cpage two\x0cpage three\x0c"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    pages = extract_pages_text(tmp_path / "x.pdf", n_pages=3)
    assert pages == ["page one", "page two", "page three"]


def test_extract_pages_text_pads_short_output(monkeypatch, tmp_path):
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = b"only one page\x0c"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    pages = extract_pages_text(tmp_path / "x.pdf", n_pages=2)
    assert pages == ["only one page", ""]
