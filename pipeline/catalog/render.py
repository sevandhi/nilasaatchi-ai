"""150-dpi WebP page-preview rendering (D-021: only 150-dpi previews up front; 300-dpi PNGs are
rendered on demand in P2 OCR, not here).

Idempotent and content-hash-cached: pages live at data/pages/{sha[:2]}/{sha}/{n}.webp, keyed by
the document's sha256, so re-running `make catalog` skips any page whose file already exists.
"""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

DPI = 150


def preview_dir(pages_root: Path, sha256: str) -> Path:
    return pages_root / sha256[:2] / sha256


def render_document_pages(
    pdf_path: str | Path, sha256: str, n_pages: int, pages_root: Path, dpi: int = DPI
) -> tuple[list[str | None], str | None]:
    """Render every missing page of one document to WebP. Returns (per-page relative preview
    path or None, error or None). Never raises: a render failure is recorded per-page as None
    and returned as an error string, so one bad PDF doesn't stop the batch."""
    out_dir = preview_dir(pages_root, sha256)
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = [p for p in range(1, n_pages + 1) if not (out_dir / f"{p}.webp").exists()]
    error: str | None = None
    if missing:
        doc = None
        try:
            doc = pdfium.PdfDocument(str(pdf_path))
            scale = dpi / 72
            for p in missing:
                page = doc[p - 1]
                try:
                    bitmap = page.render(scale=scale)
                    pil = bitmap.to_pil().convert("RGB")
                    pil.save(out_dir / f"{p}.webp", "WEBP", quality=82)
                finally:
                    page.close()
        except Exception as exc:  # noqa: BLE001 - one corrupt PDF must not kill the batch
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if doc is not None:
                doc.close()

    repo_root = pages_root.parent.parent if pages_root.name == "pages" else pages_root
    results: list[str | None] = []
    for p in range(1, n_pages + 1):
        path = out_dir / f"{p}.webp"
        if path.exists():
            try:
                results.append(str(path.relative_to(repo_root)))
            except ValueError:
                results.append(str(path))
        else:
            results.append(None)
    return results, error
