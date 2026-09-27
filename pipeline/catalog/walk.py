"""Discover every catalogable file: Dataset/Land_documents/**/*.pdf plus the Form E zip member
PDFs (unzipped into data/unzipped/ first). Dataset/ itself is read-only and is never modified.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .unzip import unzip_form_e

LAND_DOCS = "Dataset/Land_documents"
ZIP_REL = "Dataset/Land_documents/LA_Patta_land_Documents/Form E/F_FORM_UNITS.zip"


@dataclass(frozen=True)
class CatalogEntry:
    abs_path: Path
    rel_path: str          # repo-relative, posix separators
    folder_label: str      # immediate parent directory name (a prior only, see land-domain-knowledge §3)
    zip_member: str | None # original zip-internal path, if sourced from a zip archive


def discover(repo_root: Path, unzipped_root: Path) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []

    land_docs_root = repo_root / LAND_DOCS
    # NB: use name.endswith(...), not Path.suffix -- a stray file named literally ".pdf" exists
    # in the dataset (Court Deposit/), and pathlib treats a leading-dot name as having *no*
    # suffix (stem=".pdf", suffix=""), which would silently drop it. See land-domain-knowledge
    # anomaly notes in the phase-1 report.
    pdf_paths = sorted(
        {p for p in land_docs_root.rglob("*") if p.is_file() and p.name.lower().endswith(".pdf")},
        key=lambda p: str(p),
    )
    for p in pdf_paths:
        entries.append(
            CatalogEntry(
                abs_path=p,
                rel_path=str(p.relative_to(repo_root)),
                folder_label=p.parent.name,
                zip_member=None,
            )
        )

    zip_path = repo_root / ZIP_REL
    if zip_path.exists():
        members = unzip_form_e(zip_path, unzipped_root)
        for member_name, extracted_path in sorted(members, key=lambda t: t[0]):
            if not extracted_path.name.lower().endswith(".pdf"):
                continue
            entries.append(
                CatalogEntry(
                    abs_path=extracted_path,
                    rel_path=str(extracted_path.relative_to(repo_root)),
                    folder_label="Form E",
                    zip_member=member_name,
                )
            )

    entries.sort(key=lambda e: e.rel_path)
    return entries
