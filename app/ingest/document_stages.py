"""One function per document-ingest stage. Every function reuses the existing batch pipeline code
(pipeline.catalog / pipeline.classify / pipeline.extract / pipeline.load / pipeline.match /
pipeline.findings) scoped to a single document, instead of re-implementing OCR/extraction/matching.

Each stage function returns {"status": "done"|"failed", "detail": str, **extra} and must never
raise for an *honest* empty/degraded result (unknown doc_type, village outside FMB, extraction
budget exhausted, ...) — those are `"done"` with a clear `detail`. Only genuine crashes (DB errors,
docker/OCR unavailable, unexpected exceptions) return `"failed"`; `stage_cli.py` also catches any
exception a stage function does raise and records it the same way.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
INBOX_DIR = REPO_ROOT / "data" / "inbox"
EXTRACT_OUT_DIR = REPO_ROOT / "data" / "extract" / "pages"
DEFAULT_EXTRACT_BUDGET_USD = float(os.environ.get("INGEST_EXTRACT_BUDGET_USD", "1.0"))


# --------------------------------------------------------------------------------------- store
def store_pdf(content: bytes) -> tuple[str, Path]:
    """Save the uploaded bytes to data/inbox/<md5>.pdf (idempotent). Returns (md5, abs_path).
    Never writes into Dataset/ or Documents/ (read-only, per CLAUDE.md)."""
    md5 = hashlib.md5(content).hexdigest()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = INBOX_DIR / f"{md5}.pdf"
    if not path.exists():
        path.write_bytes(content)
    return md5, path


def find_duplicate(conn: psycopg.Connection, md5: str) -> int | None:
    row = conn.execute("SELECT id FROM document WHERE md5 = %s ORDER BY id LIMIT 1", (md5,)).fetchone()
    if row is None:
        return None
    return row["id"] if isinstance(row, dict) else row[0]


# -------------------------------------------------------------------------------------- catalog
def run_catalog_stage(conn: psycopg.Connection, path: Path, folder_label: str) -> dict:
    """Catalog exactly one PDF (pipeline.catalog.run's hash -> text-layer -> render -> upsert
    pipeline, reused function-by-function since `pipeline.catalog.walk.discover()` is hard-wired
    to Dataset/ and cannot be pointed at one arbitrary file)."""
    import pypdfium2 as pdfium

    from pipeline.catalog.hashing import hash_file
    from pipeline.catalog.render import render_document_pages
    from pipeline.catalog.run import DOC_UPSERT, PAGE_UPSERT, PAGES_ROOT
    from pipeline.catalog.textlayer import extract_pages_text, text_quality

    sha256, md5, size = hash_file(path)
    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError:
        return {"status": "failed",
                "detail": "the file could not be opened as a PDF (it may be damaged or incomplete)"}
    n_pages = len(doc)
    doc.close()
    page_texts = extract_pages_text(path, n_pages)
    page_quality = [text_quality(t) for t in page_texts]
    previews, render_error = render_document_pages(path, sha256, n_pages, PAGES_ROOT)
    rel_path = str(path.relative_to(REPO_ROOT))
    text_chars = [q[0] for q in page_quality]
    row = conn.execute(
        DOC_UPSERT,
        {"sha256": sha256, "md5": md5, "dup_group_id": sha256, "bytes": size, "pages": n_pages,
         "text_chars_per_page": text_chars, "folder_label": folder_label, "path": rel_path,
         "all_paths": [rel_path], "zip_member": None},
    ).fetchone()
    document_id = row["id"] if isinstance(row, dict) else row[0]
    for i in range(n_pages):
        page_no = i + 1
        text = page_texts[i] if i < len(page_texts) else ""
        _, quality, ok = page_quality[i] if i < len(page_quality) else (0, 0.0, False)
        preview = previews[i] if i < len(previews) else None
        conn.execute(PAGE_UPSERT, {"document_id": document_id, "page_no": page_no, "text_layer_ok": ok,
                                   "text_quality": quality, "preview_path": preview, "text": text})
    conn.commit()
    detail = f"{n_pages} pages catalogued (sha256 {sha256[:12]}…)"
    if render_error:
        detail += f"; page-render warning: {render_error}"
    return {"status": "done", "detail": detail, "document_id": document_id, "pages": n_pages, "sha256": sha256}


# ------------------------------------------------------------------------------------- classify
def run_classify_stage(conn: psycopg.Connection, document_id: int, declared_type: str | None = None) -> dict:
    """Classify exactly one document: the same `classify_one` (D-030 LLM decision via the router,
    with its own PII-tier fallback chain — see config/models.yaml `classify_text`/`classify_page`)
    the full-corpus batch uses, applied to one row fetched by `_fetch_docs`. The batch-only
    `preflight_bedrock()` guard is skipped here: it exists to avoid burning OCR time across
    thousands of pages before a big run, which does not apply to a single already-catalogued
    document, and the router's own chain already falls back off Bedrock when AWS is unavailable."""
    from psycopg.types.json import Jsonb

    from pipeline.classify.run import (
        DOC_UPDATE,
        PENDING_UPDATE,
        SEGMENT_DELETE,
        SEGMENT_INSERT,
        _fetch_docs,
        _json_safe,
        _resolve_header_pages_text,
        classify_one,
    )

    row = conn.execute("SELECT sha256 FROM document WHERE id = %s", (document_id,)).fetchone()
    if row is None:
        return {"status": "failed", "detail": f"document {document_id} not found"}
    sha256 = row["sha256"] if isinstance(row, dict) else row[0]
    docs = _fetch_docs(conn, None, {sha256})
    if not docs:
        return {"status": "failed", "detail": "document has no pages to classify"}
    doc = docs[0]
    header_texts = _resolve_header_pages_text(doc, True)
    res = classify_one(doc, header_texts)
    out = dict(res)
    ai_type, ai_conf = out["classified_type"], out["type_confidence"]
    if declared_type:
        # the uploader said what the document is: use it, keep the AI's guess alongside as evidence
        from pipeline.classify.taxonomy import stage_of

        out["type_evidence"] = {**(out.get("type_evidence") or {}), "source": "declared_by_uploader",
                                "ai_type": ai_type, "ai_confidence": ai_conf}
        out.update(classified_type=declared_type, stage=stage_of(declared_type), type_confidence=1.0,
                   segments=[])
        if out.get("scheme_relevance") in (None, "unknown"):
            out["scheme_relevance"] = "allikulam"
    out["type_evidence"] = Jsonb(_json_safe(out["type_evidence"]))
    segments = out.pop("segments")
    with conn.transaction():
        if out["classified_type"] is None:
            conn.execute(PENDING_UPDATE, out)
        else:
            conn.execute(DOC_UPDATE, out)
            conn.execute(SEGMENT_DELETE, (doc.id,))
            for seg in segments:
                seg = dict(seg)
                seg["document_id"] = doc.id
                seg["type_evidence"] = Jsonb(_json_safe(seg["type_evidence"]))
                conn.execute(SEGMENT_INSERT, seg)
    conn.commit()
    if out["classified_type"] is None:
        ev = res["type_evidence"]
        return {"status": "done", "detail": f"classification pending/unavailable: {ev.get('error') or 'no model answered'}",
                "doc_type": None}
    if declared_type:
        agree = "agrees" if ai_type == declared_type else f"AI suggested {ai_type or 'unclassified'}"
        return {"status": "done", "doc_type": declared_type, "stage": out["stage"], "ai_type": ai_type,
                "scheme_relevance": out["scheme_relevance"], "confidence": 1.0,
                "detail": f"{declared_type} as declared by the uploader ({agree}; "
                          f"scheme_relevance={out['scheme_relevance']})"}
    return {"status": "done", "doc_type": out["classified_type"], "stage": out["stage"],
            "scheme_relevance": out["scheme_relevance"], "confidence": out["type_confidence"],
            "detail": f"classified as {out['classified_type']} (scheme_relevance={out['scheme_relevance']}, "
                      f"confidence={out['type_confidence']})"}


# --------------------------------------------------------------------------------------- extract
def run_extract_stage(conn: psycopg.Connection, document_id: int,
                      budget_usd: float = DEFAULT_EXTRACT_BUDGET_USD) -> dict:
    """Extract every extraction-eligible page of one document via `pipeline.extract.batch`'s own
    `Runner`/`run_document`/`extract_page` (D-010 whole-page VLM + per-doc-type mapping +
    self-consistency): the exact same code the Stage-B corpus batch uses, selection just scoped to
    this document's pages instead of the whole corpus."""
    from pipeline.extract.batch import PRIORITY, Runner, run_document
    from pipeline.extract.tesseract import DockerUnavailable, Tesseract

    doc_row = conn.execute("SELECT scheme_relevance, classified_type, path FROM document WHERE id = %s",
                          (document_id,)).fetchone()
    if doc_row is None:
        return {"status": "failed", "detail": f"document {document_id} not found"}
    scheme_relevance = doc_row["scheme_relevance"] if isinstance(doc_row, dict) else doc_row[0]
    if (scheme_relevance or "unknown") == "other_scheme":
        return {"status": "done", "detail": "scheme_relevance=other_scheme: no pages selected for extraction",
                "pages_selected": 0, "extracted": 0}

    rows = conn.execute(
        """SELECT d.id, d.sha256, d.path, d.classified_type, d.folder_label, p.page_no, p.id AS page_id,
                  p.text_layer_ok,
                  (SELECT s.doc_type FROM document_segment s WHERE s.document_id = d.id
                     AND p.page_no BETWEEN s.page_start AND s.page_end AND s.doc_type IS NOT NULL
                   ORDER BY s.seg_no LIMIT 1) AS seg_type
           FROM document d JOIN page p ON p.document_id = d.id
           WHERE d.id = %s ORDER BY p.page_no""",
        (document_id,),
    ).fetchall()

    def g(r, k, i):
        return r[k] if isinstance(r, dict) else r[i]

    pages = []
    for r in rows:
        t = g(r, "seg_type", 8) or g(r, "classified_type", 3)
        if t not in PRIORITY:
            continue
        pages.append({"doc_id": g(r, "id", 0), "sha256": g(r, "sha256", 1), "path": g(r, "path", 2),
                      "type": t, "type_source": "segment" if g(r, "seg_type", 8) else "document",
                      "folder": g(r, "folder_label", 4), "page_no": g(r, "page_no", 5),
                      "page_id": g(r, "page_id", 6), "text_layer_ok": bool(g(r, "text_layer_ok", 7))})
    if not pages:
        classified_type = doc_row["classified_type"] if isinstance(doc_row, dict) else doc_row[1]
        return {"status": "done",
                "detail": f"doc_type={classified_type!r} is not extraction-eligible (unknown/unclassified "
                          "or not in the priority type list): 0 pages extracted",
                "pages_selected": 0, "extracted": 0}

    text_rows = conn.execute(
        "SELECT id, text FROM page WHERE document_id = %s AND text_layer_ok", (document_id,)
    ).fetchall()
    text_by_page = {g(r, "id", 0): g(r, "text", 1) for r in text_rows}

    try:
        with Tesseract() as ocr:
            runner = Runner(budget_usd, max_fail=5)
            run_document(runner, pages, ocr, text_by_page)
    except DockerUnavailable as exc:
        return {"status": "failed", "detail": f"OCR docker tools unavailable: {exc}"}
    except Exception as exc:  # noqa: BLE001 - one bad document must not crash the ingest job
        return {"status": "failed", "detail": f"extraction crashed: {type(exc).__name__}: {exc}"}

    detail = (f"{len(pages)} pages selected, {runner.counts['done']} extracted, "
             f"{runner.counts['skipped']} already cached, {runner.counts['failed']} failed "
             f"(spent ${runner.spent:.4f} of ${budget_usd})")
    if runner.stop_reason:
        reason = runner.stop_reason
        if "SSO" in reason:
            reason = "the cloud table-reading service is not signed in; try again once it is available"
        detail += f"; stopped early: {reason}"
    # Nothing read (fresh or cached) means the table reader was unavailable: report it as a failure
    # rather than letting later stages "succeed" on an empty extraction.
    read_any = runner.counts["done"] + runner.counts["skipped"] > 0
    status = "done" if read_any or not pages else "failed"
    if status == "done" and (runner.counts["failed"] or runner.stop_reason):
        detail = "partial: " + detail
    return {"status": status, "detail": detail, "pages_selected": len(pages), **runner.counts,
            "spent_usd": round(runner.spent, 5), "stop_reason": runner.stop_reason}


# ------------------------------------------------------------------------------------------ load
def run_load_stage(conn: psycopg.Connection, document_id: int) -> dict:
    """`pipeline.load.extractions`'s own idempotent loader (content_hash keyed), scoped to this
    document's output directory (data/extract/pages/<doc_id>/)."""
    from pipeline.load.extractions import iter_pages, load_document_events, load_page

    doc_dir = EXTRACT_OUT_DIR / str(document_id)
    tot = {"pages": 0, "skipped": 0, "extractions": 0, "facts": 0, "events": 0, "owners": 0, "queue": 0}
    if doc_dir.exists():
        for _f, page in iter_pages([doc_dir]):
            with conn.transaction():
                s = load_page(conn, page, replace=False)
            tot["pages"] += 1
            for k, v in s.items():
                tot[k] += v
    with conn.transaction():
        tot["doc_events_new"] = load_document_events(conn)
    conn.commit()
    detail = (f"{tot['pages']} extraction pages loaded ({tot['skipped']} already loaded): "
             f"{tot['extractions']} rows, {tot['facts']} facts, {tot['events']} events, "
             f"{tot['queue']} sent to review")
    return {"status": "done", "detail": detail, **tot}


# ----------------------------------------------------------------------------------------- match
def run_match_stage(conn: psycopg.Connection) -> dict:
    """`pipeline.match`'s own incremental matcher (match_status IS NULL only — i.e. exactly the
    rows this document just added, plus any other unmatched backlog), reused verbatim."""
    import pipeline.match.__main__ as match_main

    match_main.sync_thresholds(conn)
    index = match_main.load_index(conn)
    stats = {}
    for table, link, idc, sql in (
        ("parcel_fact", "parcel_fact_link", "fact_id", match_main.FACT_SQL),
        ("acquisition_event", "acquisition_event_link", "event_id", match_main.EVENT_SQL),
    ):
        st = match_main.run_table(conn, table, link, idc, sql, index, full=False)
        conn.commit()
        stats[table] = dict(st)
    detail = "; ".join(f"{t}: " + ", ".join(f"{k}={v}" for k, v in s.items()) for t, s in stats.items() if s)
    return {"status": "done", "detail": detail or "no unmatched rows", **stats}


# -------------------------------------------------------------------------------------- findings
def run_findings_stage(conn: psycopg.Connection, document_id: int | None) -> dict:
    """Re-run the whole findings engine (`pipeline.findings`'s own `run`/`upsert`, idempotent via
    `finding_key` upsert) — the same full recompute `make findings` does — then report the delta
    (`new_findings`) plus (for a document job) this document's linked parcels and review-queue
    count. `document_id=None` (the satellite-refresh job) reports only `new_findings`."""
    import pipeline.findings.__main__ as findings_main

    before = {r["finding_key"] if isinstance(r, dict) else r[0]
             for r in conn.execute("SELECT finding_key FROM finding").fetchall()}
    fs = findings_main.run(conn)
    findings_main.upsert(conn, fs)
    conn.commit()
    after = {r["finding_key"] if isinstance(r, dict) else r[0]
            for r in conn.execute("SELECT finding_key FROM finding").fetchall()}
    new_findings = len(after - before)

    if document_id is None:
        return {"status": "done", "detail": f"{new_findings} new findings", "new_findings": new_findings}

    linked = conn.execute(
        """SELECT DISTINCT parcel_uid FROM (
             SELECT f.parcel_uid FROM parcel_fact f JOIN extraction e ON e.id = f.extraction_id
               WHERE e.document_id = %(d)s AND f.parcel_uid IS NOT NULL
             UNION
             SELECT pfl.parcel_uid FROM parcel_fact_link pfl
               JOIN parcel_fact f ON f.id = pfl.fact_id JOIN extraction e ON e.id = f.extraction_id
               WHERE e.document_id = %(d)s
             UNION
             SELECT a.parcel_uid FROM acquisition_event a WHERE a.document_id = %(d)s AND a.parcel_uid IS NOT NULL
             UNION
             SELECT ael.parcel_uid FROM acquisition_event_link ael
               JOIN acquisition_event a ON a.id = ael.event_id WHERE a.document_id = %(d)s
           ) x LIMIT 50""",
        {"d": document_id},
    ).fetchall()
    linked_parcels = [r["parcel_uid"] if isinstance(r, dict) else r[0] for r in linked]
    review_items = conn.execute("SELECT count(*) AS n FROM review_queue WHERE document_id = %s",
                                (document_id,)).fetchone()
    review_items = review_items["n"] if isinstance(review_items, dict) else review_items[0]

    detail = f"{new_findings} new findings; {len(linked_parcels)} parcels linked; {review_items} review items"
    if not linked_parcels:
        detail += " (no parcel could be resolved — village may be outside the FMB layers, or facts are block-level only)"
    return {"status": "done", "detail": detail, "new_findings": new_findings,
            "linked_parcels": linked_parcels, "review_items": review_items}


# ---------------------------------------------------------------------------------- final result
def document_summary(conn: psycopg.Connection, document_id: int) -> dict:
    row = conn.execute("SELECT classified_type, pages FROM document WHERE id = %s", (document_id,)).fetchone()
    doc_type = row["classified_type"] if isinstance(row, dict) else (row[0] if row else None)
    pages = row["pages"] if isinstance(row, dict) else (row[1] if row else None)
    extraction_rows = conn.execute("SELECT count(*) AS n FROM extraction WHERE document_id = %s",
                                   (document_id,)).fetchone()
    extraction_rows = extraction_rows["n"] if isinstance(extraction_rows, dict) else extraction_rows[0]
    facts = conn.execute(
        "SELECT count(*) AS n FROM parcel_fact f JOIN extraction e ON e.id = f.extraction_id "
        "WHERE e.document_id = %s", (document_id,),
    ).fetchone()
    facts = facts["n"] if isinstance(facts, dict) else facts[0]
    events = conn.execute("SELECT count(*) AS n FROM acquisition_event WHERE document_id = %s",
                          (document_id,)).fetchone()
    events = events["n"] if isinstance(events, dict) else events[0]
    return {"document_id": document_id, "doc_type": doc_type, "pages": pages,
            "extraction_rows": extraction_rows, "facts": facts, "events": events}
