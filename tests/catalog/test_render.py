
from pipeline.catalog.render import render_document_pages


def test_render_creates_webp_per_page(tmp_path, two_page_pdf_path):
    pages_root = tmp_path / "pages"
    sha = "deadbeef" * 8
    previews, error = render_document_pages(two_page_pdf_path, sha, 2, pages_root)

    assert error is None
    assert len(previews) == 2
    for n, p in enumerate(previews, start=1):
        assert p is not None
        assert (pages_root / sha[:2] / sha / f"{n}.webp").exists()


def test_render_is_idempotent_skips_existing(tmp_path, minimal_pdf_path):
    pages_root = tmp_path / "pages"
    sha = "cafe" * 16
    previews1, _err1 = render_document_pages(minimal_pdf_path, sha, 1, pages_root)
    out_file = pages_root / sha[:2] / sha / "1.webp"
    assert out_file.exists()
    mtime1 = out_file.stat().st_mtime

    # Corrupt the source path so a *real* re-render would fail; idempotent skip means the
    # existing file is left alone and no error is raised.
    previews2, err2 = render_document_pages(tmp_path / "does-not-exist.pdf", sha, 1, pages_root)

    assert err2 is None
    assert previews2 == previews1
    assert out_file.stat().st_mtime == mtime1


def test_render_missing_pdf_records_error_without_raising(tmp_path):
    pages_root = tmp_path / "pages"
    previews, error = render_document_pages(tmp_path / "nope.pdf", "aaaa" * 16, 2, pages_root)
    assert error is not None
    assert previews == [None, None]
