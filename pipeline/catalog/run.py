"""T1.1 catalog orchestration: hash -> dedup by sha256 -> text-layer extract -> render previews
-> upsert into `document` / `page`. Fully idempotent (ON CONFLICT upserts + content-hash-keyed
page files) and re-runnable with `--limit N` for smoke runs.
"""
from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from .dbutil import get_conn
from .hashing import hash_file
from .render import render_document_pages
from .textlayer import extract_pages_text, text_quality
from .walk import CatalogEntry, discover

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES_ROOT = REPO_ROOT / "data" / "pages"
UNZIPPED_ROOT = REPO_ROOT / "data" / "unzipped"

WORKERS = 12


# ---- pass 1: hash --------------------------------------------------------------------------
def _hash_one(rel_path: str) -> tuple[str, str, str, int, str | None]:
    abs_path = REPO_ROOT / rel_path
    try:
        sha256, md5, size = hash_file(abs_path)
        return rel_path, sha256, md5, size, None
    except Exception as exc:  # noqa: BLE001
        return rel_path, "", "", 0, f"{type(exc).__name__}: {exc}"


# ---- pass 2: per unique document (text layer + render) -------------------------------------
@dataclass
class DocResult:
    sha256: str
    n_pages: int
    page_texts: list[str]
    page_quality: list[tuple[int, float, bool]]
    previews: list[str | None]
    error: str | None


def _process_one(args: tuple[str, str]) -> DocResult:
    sha256, primary_abs_path = args
    n_pages = 0
    error = None
    try:
        doc = pdfium.PdfDocument(primary_abs_path)
        n_pages = len(doc)
        doc.close()
    except Exception as exc:  # noqa: BLE001
        error = f"open failed: {type(exc).__name__}: {exc}"
        return DocResult(sha256, 0, [], [], [], error)

    page_texts = extract_pages_text(primary_abs_path, n_pages)
    page_quality = [text_quality(t) for t in page_texts]
    previews, render_error = render_document_pages(primary_abs_path, sha256, n_pages, PAGES_ROOT)
    if render_error:
        error = render_error
    return DocResult(sha256, n_pages, page_texts, page_quality, previews, error)


# ---- grouping -------------------------------------------------------------------------------
def group_by_sha(entries: list[CatalogEntry], hashes: dict[str, tuple[str, str, int, str | None]]):
    """Return {sha256: {'paths': [...], 'md5':, 'bytes':, 'primary': CatalogEntry,
    'folder_labels': set(), 'zip_members': set(), 'errors': [...]}}"""
    groups: dict[str, dict] = {}
    hash_errors: list[tuple[str, str]] = []
    for e in entries:
        sha256, md5, size, err = hashes[e.rel_path]
        if err:
            hash_errors.append((e.rel_path, err))
            continue
        g = groups.setdefault(
            sha256,
            {"paths": [], "md5": md5, "bytes": size, "folder_labels": set(), "zip_members": set()},
        )
        g["paths"].append(e)
        g["folder_labels"].add(e.folder_label)
        if e.zip_member:
            g["zip_members"].add(e.zip_member)
    for sha256, g in groups.items():
        g["paths"].sort(key=lambda e: e.rel_path)
        g["primary"] = g["paths"][0]
    return groups, hash_errors


# ---- DB upserts -----------------------------------------------------------------------------
DOC_UPSERT = """
INSERT INTO document (sha256, md5, dup_group_id, bytes, pages, text_chars_per_page,
                       folder_label, path, all_paths, zip_member, status)
VALUES (%(sha256)s, %(md5)s, %(dup_group_id)s, %(bytes)s, %(pages)s, %(text_chars_per_page)s,
        %(folder_label)s, %(path)s, %(all_paths)s, %(zip_member)s, 'catalogued')
ON CONFLICT (sha256) DO UPDATE SET
  md5 = EXCLUDED.md5, bytes = EXCLUDED.bytes, pages = EXCLUDED.pages,
  text_chars_per_page = EXCLUDED.text_chars_per_page, folder_label = EXCLUDED.folder_label,
  path = EXCLUDED.path, all_paths = EXCLUDED.all_paths, zip_member = EXCLUDED.zip_member,
  updated_at = now()
RETURNING id;
"""

PAGE_UPSERT = """
INSERT INTO page (document_id, page_no, text_layer_ok, text_quality, preview_path, text)
VALUES (%(document_id)s, %(page_no)s, %(text_layer_ok)s, %(text_quality)s, %(preview_path)s, %(text)s)
ON CONFLICT (document_id, page_no) DO UPDATE SET
  text_layer_ok = EXCLUDED.text_layer_ok, text_quality = EXCLUDED.text_quality,
  preview_path = COALESCE(EXCLUDED.preview_path, page.preview_path), text = EXCLUDED.text;
"""


def run(limit: int | None = None, workers: int = WORKERS) -> dict:
    t0 = time.time()
    entries = discover(REPO_ROOT, UNZIPPED_ROOT)
    total_discovered = len(entries)
    if limit:
        entries = entries[:limit]

    with mp.Pool(workers) as pool:
        hash_rows = pool.map(_hash_one, [e.rel_path for e in entries], chunksize=8)
    hashes = {r[0]: (r[1], r[2], r[3], r[4]) for r in hash_rows}

    groups, hash_errors = group_by_sha(entries, hashes)

    process_args = [(sha256, str(g["primary"].abs_path)) for sha256, g in groups.items()]
    with mp.Pool(workers) as pool:
        doc_results: list[DocResult] = pool.map(_process_one, process_args, chunksize=1)
    results_by_sha = {r.sha256: r for r in doc_results}

    n_docs = 0
    n_pages = 0
    process_errors: list[tuple[str, str]] = []
    cross_folder_groups = 0
    zip_overlap_groups = 0

    with get_conn() as conn:
        for sha256, g in groups.items():
            dr = results_by_sha[sha256]
            if dr.error:
                process_errors.append((sha256, dr.error))
            primary = g["primary"]
            all_paths = [e.rel_path for e in g["paths"]]
            folders = g["folder_labels"]
            if len(folders) > 1:
                cross_folder_groups += 1
            has_zip = bool(g["zip_members"])
            has_nonzip = any(e.zip_member is None for e in g["paths"])
            if has_zip and has_nonzip:
                zip_overlap_groups += 1
            zip_member = primary.zip_member or (next(iter(g["zip_members"])) if g["zip_members"] else None)

            text_chars = [q[0] for q in dr.page_quality]
            row = conn.execute(
                DOC_UPSERT,
                {
                    "sha256": sha256,
                    "md5": g["md5"],
                    "dup_group_id": sha256,  # one row per unique sha256; itself is the group leader
                    "bytes": g["bytes"],
                    "pages": dr.n_pages,
                    "text_chars_per_page": text_chars,
                    "folder_label": primary.folder_label,
                    "path": primary.rel_path,
                    "all_paths": all_paths,
                    "zip_member": zip_member,
                },
            ).fetchone()
            document_id = row[0]
            n_docs += 1

            for i in range(dr.n_pages):
                page_no = i + 1
                text = dr.page_texts[i] if i < len(dr.page_texts) else ""
                _, quality, ok = dr.page_quality[i] if i < len(dr.page_quality) else (0, 0.0, False)
                preview = dr.previews[i] if i < len(dr.previews) else None
                conn.execute(
                    PAGE_UPSERT,
                    {
                        "document_id": document_id,
                        "page_no": page_no,
                        "text_layer_ok": ok,
                        "text_quality": quality,
                        "preview_path": preview,
                        "text": text,
                    },
                )
                n_pages += 1
        conn.commit()

    elapsed = time.time() - t0
    return {
        "total_discovered": total_discovered,
        "processed_entries": len(entries),
        "unique_documents": n_docs,
        "unique_pages": n_pages,
        "cross_folder_dup_groups": cross_folder_groups,
        "zip_overlap_groups": zip_overlap_groups,
        "hash_errors": hash_errors,
        "process_errors": process_errors,
        "elapsed_s": round(elapsed, 1),
    }
