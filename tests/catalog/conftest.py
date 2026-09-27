import pytest

# The smallest valid multi-object PDF that PDFium/poppler will happily open: one blank A4-ish
# page, no content stream needed. Used so catalog tests never depend on Dataset/ (read-only,
# and possibly absent in a bare test environment).
MINIMAL_PDF_1PAGE = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>
endobj
xref
0 4
0000000000 65535 f
trailer
<< /Size 4 /Root 1 0 R >>
startxref
0
%%EOF
"""


def _two_page_pdf() -> bytes:
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>
endobj
4 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>
endobj
xref
0 5
0000000000 65535 f
trailer
<< /Size 5 /Root 1 0 R >>
startxref
0
%%EOF
"""


@pytest.fixture
def minimal_pdf_path(tmp_path):
    p = tmp_path / "minimal.pdf"
    p.write_bytes(MINIMAL_PDF_1PAGE)
    return p


@pytest.fixture
def two_page_pdf_path(tmp_path):
    p = tmp_path / "two_page.pdf"
    p.write_bytes(_two_page_pdf())
    return p
