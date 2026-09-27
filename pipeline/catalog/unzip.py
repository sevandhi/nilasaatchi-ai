"""Unzip Form E/F_FORM_UNITS.zip into data/unzipped/ (T1.1). Idempotent: re-extraction is
skipped when the target already has the right size."""
from __future__ import annotations

import zipfile
from pathlib import Path


def unzip_form_e(zip_path: Path, out_root: Path) -> list[tuple[str, Path]]:
    """Extract every file entry from `zip_path` under `out_root`, preserving the internal zip
    layout. Returns [(zip_member_path, extracted_absolute_path), ...] for files only (directory
    entries are skipped)."""
    out: list[tuple[str, Path]] = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = out_root / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size != info.file_size:
                with z.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            out.append((info.filename, target))
    return out
