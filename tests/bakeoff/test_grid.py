"""Tests for spikes/ocr_bakeoff/grid.py table-grid detection.

Uses synthetically drawn ruled tables (fast, deterministic, no golden-set
dependency) plus a couple of smoke checks against the real golden pages when
they are present on disk.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

from spikes.ocr_bakeoff.grid import band_crops, crop_cell, detect_table_grid

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO_ROOT / "eval" / "golden" / "pages"


def _draw_ruled_table(rows: int, cols: int, cell_w: int = 120, cell_h: int = 80, margin: int = 60) -> np.ndarray:
    w = margin * 2 + cols * cell_w
    h = margin * 2 + rows * cell_h
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    x0, y0 = margin, margin
    x1, y1 = margin + cols * cell_w, margin + rows * cell_h
    for r in range(rows + 1):
        y = y0 + r * cell_h
        cv2.line(img, (x0, y), (x1, y), (0, 0, 0), 2)
    for c in range(cols + 1):
        x = x0 + c * cell_w
        cv2.line(img, (x, y0), (x, y1), (0, 0, 0), 2)
    return img


def test_detect_table_grid_on_synthetic_table():
    img = _draw_ruled_table(rows=4, cols=3)
    result = detect_table_grid(img)
    assert result.found
    assert len(result.row_lines) == 5   # 4 rows -> 5 separators
    assert len(result.col_lines) == 4   # 3 cols -> 4 separators
    assert len(result.cells) == 4 * 3


def test_detect_table_grid_on_blank_page():
    img = np.full((900, 700, 3), 255, dtype=np.uint8)
    result = detect_table_grid(img)
    assert not result.found
    assert result.reason in {"no_ruling_lines", "region_too_small", "insufficient_lines"}


def test_crop_cell_shape():
    img = _draw_ruled_table(rows=2, cols=2)
    result = detect_table_grid(img)
    assert result.found
    cell = result.cells[0]
    crop = crop_cell(img, cell)
    assert crop.shape[0] > 0 and crop.shape[1] > 0
    assert crop.shape[0] <= cell.h
    assert crop.shape[1] <= cell.w


def test_band_crops_splits_and_repeats_header():
    # 13 data rows + 1 header row, banded at 6 rows/band -> 3 bands, each with the
    # header row physically stacked on top (14 row-heights tall per band).
    img = _draw_ruled_table(rows=14, cols=3, cell_h=40)
    result = detect_table_grid(img)
    assert result.found
    crops = band_crops(img, result, rows_per_band=6)
    assert len(crops) == 3
    header_h = result.row_lines[1] - result.row_lines[0]
    for i, crop in enumerate(crops[:-1]):
        band_h = 6 * 40  # cell_h
        assert crop.shape[0] == header_h + band_h
    assert crop.shape[1] == result.bbox[2]  # matches the detected table width


def test_band_crops_empty_when_no_grid():
    img = np.full((900, 700, 3), 255, dtype=np.uint8)
    result = detect_table_grid(img)
    assert band_crops(img, result) == []


GOLDEN_GRID_EXPECTATIONS = [
    # page_id, min_rows, min_cols -- pages with a clean ruled table (Form F, LDR,
    # chitta, DLPNC, GO/AS/LPS totals tables). g06/g17 are known misses (see the
    # bake-off report) and are intentionally excluded here.
    ("g09", 2, 2),
    ("g19", 2, 2),
    ("g01", 2, 2),
]


@pytest.mark.parametrize("page_id,min_rows,min_cols", GOLDEN_GRID_EXPECTATIONS)
def test_detect_table_grid_on_golden_pages(page_id, min_rows, min_cols):
    path = GOLDEN_DIR / f"{page_id}.png"
    if not path.exists():
        pytest.skip("golden page images not present in this checkout")
    img = cv2.imread(str(path))
    result = detect_table_grid(img)
    assert result.found, f"{page_id}: {result.reason}"
    assert len(result.row_lines) >= min_rows
    assert len(result.col_lines) >= min_cols
