"""Table-grid detection via OpenCV morphological ruling-line extraction.

Promoted from spikes/ocr_bakeoff/grid.py. Per D-010 the grid is no longer used for cell OCR;
it only locates tables and row bands so each VLM row can carry an evidence bbox
(`bbox_method: grid_row | grid_table | page`). `row_bboxes` / `assign_row_bboxes` are new.

Approach: binarise, extract long horizontal and vertical strokes with
erode/dilate using a kernel sized relative to the page, take the largest
connected bounding box as the table region, then find row/column separator
lines as profile peaks *relative to their own maximum* inside that box (a
fixed fraction of the page width is too strict when ruling lines are faint or
broken by a scan, which happens on several golden pages).

If the fraction of the page occupied by the largest ruled region is too small
(the page has no table -- gazette prose, a plain letter, a bank cheque), grid
detection reports ``found=False`` and the caller falls back to full-page OCR.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Cell:
    row: int
    col: int
    x: int
    y: int
    w: int
    h: int


@dataclass
class GridResult:
    found: bool
    bbox: tuple[int, int, int, int] | None  # x, y, w, h
    row_lines: list[int]
    col_lines: list[int]
    cells: list[Cell]
    reason: str = ""


MIN_AREA_FRAC = 0.02       # below this, treat the page as having no table
MIN_CELL_PX = 12           # drop degenerate slivers from noisy line detection


def _binarise(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        ~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2
    )


def _line_groups(idxs: np.ndarray, gap: int = 8) -> list[int]:
    if len(idxs) == 0:
        return []
    groups = [[int(idxs[0])]]
    for v in idxs[1:]:
        v = int(v)
        if v - groups[-1][-1] <= gap:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [round(sum(g) / len(g)) for g in groups]


def detect_table_grid(image: np.ndarray) -> GridResult:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape
    bw = _binarise(gray)

    horiz_size = max(10, w // 30)
    vert_size = max(10, h // 30)
    horiz = cv2.dilate(
        cv2.erode(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_size, 1))),
        cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_size, 1)),
    )
    vert = cv2.dilate(
        cv2.erode(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_size))),
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_size)),
    )
    grid_mask = cv2.bitwise_or(horiz, vert)

    contours, _ = cv2.findContours(grid_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return GridResult(False, None, [], [], [], reason="no_ruling_lines")
    areas = [cv2.contourArea(c) for c in contours]
    x, y, ww, hh = cv2.boundingRect(contours[int(np.argmax(areas))])
    if (ww * hh) / (w * h) < MIN_AREA_FRAC:
        return GridResult(False, None, [], [], [], reason="region_too_small")

    hor_roi = horiz[y : y + hh, x : x + ww]
    ver_roi = vert[y : y + hh, x : x + ww]
    row_profile = (hor_roi > 0).sum(axis=1).astype(float)
    col_profile = (ver_roi > 0).sum(axis=0).astype(float)
    row_thresh = max(0.5 * row_profile.max(), 0.15 * ww) if row_profile.max() > 0 else 1e9
    col_thresh = max(0.5 * col_profile.max(), 0.15 * hh) if col_profile.max() > 0 else 1e9
    row_lines = _line_groups(np.where(row_profile >= row_thresh)[0])
    col_lines = _line_groups(np.where(col_profile >= col_thresh)[0])

    if len(row_lines) < 2 or len(col_lines) < 2:
        return GridResult(False, (x, y, ww, hh), row_lines, col_lines, [], reason="insufficient_lines")

    cells: list[Cell] = []
    for ri in range(len(row_lines) - 1):
        ry0, ry1 = row_lines[ri], row_lines[ri + 1]
        if ry1 - ry0 < MIN_CELL_PX:
            continue
        for ci in range(len(col_lines) - 1):
            cx0, cx1 = col_lines[ci], col_lines[ci + 1]
            if cx1 - cx0 < MIN_CELL_PX:
                continue
            cells.append(Cell(row=ri, col=ci, x=x + cx0, y=y + ry0, w=cx1 - cx0, h=ry1 - ry0))
    return GridResult(True, (x, y, ww, hh), row_lines, col_lines, cells)


def crop_cell(image: np.ndarray, cell: Cell, inset: int = 4) -> np.ndarray:
    x0 = max(cell.x + inset, 0)
    y0 = max(cell.y + inset, 0)
    x1 = min(cell.x + cell.w - inset, image.shape[1])
    y1 = min(cell.y + cell.h - inset, image.shape[0])
    if x1 <= x0 or y1 <= y0:
        return image[cell.y : cell.y + cell.h, cell.x : cell.x + cell.w]
    return image[y0:y1, x0:x1]


def draw_overlay(image: np.ndarray, result: GridResult) -> np.ndarray:
    overlay = image.copy()
    if result.bbox is None:
        return overlay
    x, y, ww, hh = result.bbox
    cv2.rectangle(overlay, (x, y), (x + ww, y + hh), (0, 200, 0), 3)
    for r in result.row_lines:
        cv2.line(overlay, (x, y + r), (x + ww, y + r), (0, 0, 255), 3)
    for c in result.col_lines:
        cv2.line(overlay, (x + c, y), (x + c, y + hh), (255, 0, 0), 3)
    return overlay


def save_overlay(image_path: Path, result: GridResult, out_path: Path) -> None:
    image = cv2.imread(str(image_path))
    overlay = draw_overlay(image, result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), overlay)


# ---------------------------------------------------------------------------
# Evidence bboxes for VLM rows (P2)
# ---------------------------------------------------------------------------
def row_bands(result: GridResult) -> list[tuple[int, int, int, int]]:
    """Pixel (x0, y0, x1, y1) of every ruled row band inside the detected table."""
    if not result.found or result.bbox is None:
        return []
    x, y, ww, _ = result.bbox
    out = []
    for r0, r1 in zip(result.row_lines, result.row_lines[1:]):
        if r1 - r0 >= MIN_CELL_PX:
            out.append((x, y + r0, x + ww, y + r1))
    return out


def _frac(box: tuple[int, int, int, int], w: int, h: int) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]


def assign_row_bboxes(result: GridResult, n_rows: int, width: int, height: int
                      ) -> tuple[list[list[float]], str]:
    """Map `n_rows` VLM rows (in reading order, total row included) to page-fraction bboxes.

    The VLM does not transcribe header bands, so rows are aligned to the *last* `n_rows`
    ruled bands when the grid has at least that many; otherwise every row gets the table box
    (`grid_table`), and with no grid the whole page (`page`). Returns (bboxes, method)."""
    page_box = [0.0, 0.0, 1.0, 1.0]
    if n_rows <= 0:
        return [], "page"
    bands = row_bands(result)
    if bands and len(bands) >= n_rows:
        return [_frac(b, width, height) for b in bands[-n_rows:]], "grid_row"
    if result.found and result.bbox is not None:
        x, y, ww, hh = result.bbox
        tb = _frac((x, y, x + ww, y + hh), width, height)
        return [tb] * n_rows, "grid_table"
    return [page_box] * n_rows, "page"
