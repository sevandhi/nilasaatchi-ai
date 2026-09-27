"""Integration tests for spikes/ocr_bakeoff/tesseract_docker.py.

These need a working docker daemon and build (or reuse) the
`nilasaatchi-ocr-tools` image, so they are skipped -- not failed -- when
docker is unavailable (see `phase0-bootstrap`: tesseract is not on the host).
"""
import shutil
from pathlib import Path

import numpy as np
import pytest

from spikes.ocr_bakeoff.tesseract_docker import DockerUnavailable, TesseractDocker

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PAGE = REPO_ROOT / "eval" / "golden" / "pages" / "g09.png"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available on this host")


@pytest.fixture(scope="module")
def tess():
    ctx = TesseractDocker(REPO_ROOT)
    try:
        ctx.start()
    except DockerUnavailable as e:
        pytest.skip(f"docker image unavailable: {e}")
    yield ctx
    ctx.stop()


def test_ocr_file_reads_english_form_f_header(tess):
    if not GOLDEN_PAGE.exists():
        pytest.skip("golden page images not present in this checkout")
    result = tess.ocr_file(GOLDEN_PAGE, lang="tam+eng", psm=6)
    assert result.returncode == 0
    assert "compensation" in result.text.lower() or "owners" in result.text.lower()
    assert result.seconds > 0


def test_ocr_cell_array_digit_whitelist(tess):
    # A clean synthetic "173/1" digit-only cell should read back cleanly with the
    # numeric whitelist -- this is the case candidate (a) full-page OCR handles
    # badly on real handwriting (land-domain-knowledge §4).
    import cv2

    img = np.full((80, 300, 3), 255, dtype=np.uint8)
    cv2.putText(img, "173/1", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    result = tess.ocr_cell_array(img)
    assert result.returncode == 0
    cleaned = result.text.strip()
    assert cleaned  # got something
    assert all(ch in "0123456789./-,\n\x0c \t" for ch in cleaned)
