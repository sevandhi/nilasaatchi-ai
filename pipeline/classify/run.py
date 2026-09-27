"""T1.2 + T1.6 orchestration (D-030: the doc-type/stage/scheme *decision* is an LLM call, not
keyword rules -- see `.llm_classifier`). Text prep is unchanged: text-layer first, quick
Tesseract OCR second (T1.2); `.llm_classifier` escalates to a page-1 vision call only when that
text is still poor.

Idempotent and resumable: every LLM call is cached by content hash (`.llm_cache`), and every
document is re-classified in place (UPDATE by id) with segments replaced (DELETE + INSERT), so
a full run interrupted partway through can simply be re-invoked -- already-cached documents cost
no additional API calls, and the DB state after any partial run is always self-consistent.
"""
from __future__ import annotations

import csv
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from psycopg.types.json import Jsonb

from ..catalog.dbutil import get_conn
from .folder_priors import folder_disagrees
from .headers import normalize_llm_date, parse_headers
from .llm_classifier import classify_document_llm, preflight_bedrock
from .quick_ocr import quick_ocr_text
from .taxonomy import stage_of

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_CSV = REPO_ROOT / "eval" / "classify" / "labels.csv"

TEXT_OK_MIN_CHARS = 50
HEADER_PAGES = 2  # classify + header-parse over the first 2 pages, per T1.2


@dataclass
class DocInput:
    id: int
    sha256: str
    path: str
    folder_label: str | None
    pages: list[tuple[int, str, bool, str | None]] = field(default_factory=list)  # (page_no, text, text_layer_ok, preview_path)


def labelled_sha256s() -> set[str]:
    if not LABELS_CSV.exists():
        return set()
    with open(LABELS_CSV, newline="", encoding="utf-8") as f:
        return {row["sha256"] for row in csv.DictReader(f)}


def _fetch_docs(conn, limit: int | None, only_sha256s: set[str] | None) -> list[DocInput]:
    where = ""
    params: list = []
    if only_sha256s:
        where = " WHERE sha256 = ANY(%s)"
        params.append(list(only_sha256s))
    id_rows = conn.execute(
        f"SELECT id FROM document{where} ORDER BY id" + (" LIMIT %s" if limit else ""),
        (*params, limit) if limit else tuple(params),
    ).fetchall()
    ids = [r[0] for r in id_rows]
    if not ids:
        return []
    docs = {i: DocInput(id=i, sha256="", path="", folder_label=None) for i in ids}
    for sha256, path, folder_label, doc_id in conn.execute(
        "SELECT sha256, path, folder_label, id FROM document WHERE id = ANY(%s)", (ids,)
    ).fetchall():
        docs[doc_id].sha256 = sha256
        docs[doc_id].path = path
        docs[doc_id].folder_label = folder_label
    for doc_id, page_no, text, text_layer_ok, preview_path in conn.execute(
        "SELECT document_id, page_no, text, text_layer_ok, preview_path FROM page "
        "WHERE document_id = ANY(%s) ORDER BY document_id, page_no",
        (ids,),
    ).fetchall():
        docs[doc_id].pages.append((page_no, text or "", text_layer_ok, preview_path))
    return [docs[i] for i in ids]


def _resolve_header_pages_text(doc: DocInput, use_ocr: bool) -> dict[int, str]:
    """text to use for classification/header-parsing, per page_no, for the first HEADER_PAGES
    pages: the text layer if it looks usable, else a quick Tesseract OCR pass (T1.2). This is
    still the D-030 "text-layer or cached Tesseract text" input; `.llm_classifier` decides
    whether it's good enough or a page-1 image is needed instead."""
    out: dict[int, str] = {}
    for page_no, text, text_layer_ok, preview_path in doc.pages[:HEADER_PAGES]:
        usable = text_layer_ok and len(text.replace(" ", "").replace("\n", "")) >= TEXT_OK_MIN_CHARS
        if usable or not use_ocr or not preview_path:
            out[page_no] = text
        else:
            ocr_text = quick_ocr_text(preview_path)
            out[page_no] = ocr_text or text
    return out


def classify_one(doc: DocInput, header_text_by_page: dict[int, str]) -> dict:
    class_text = "\n".join(header_text_by_page.get(pn, "") for pn, *_ in doc.pages[:HEADER_PAGES])
    page1_preview = doc.pages[0][3] if doc.pages else None
    filename = doc.path.rsplit("/", 1)[-1] if doc.path else doc.sha256

    llm = classify_document_llm(
        class_text=class_text, folder_label=doc.folder_label, filename=filename,
        page_count=len(doc.pages), page1_preview_path=page1_preview,
    )

    classified_type = llm.get("doc_type")
    meta = llm.get("_meta", {})

    # Incident (2026-09-27, post-mortem in docs/decisions.md): a failed or non-Bedrock-fallback
    # call must NEVER overwrite an existing (or absent) classification with a fabricated "OTHER".
    # `classified_type` stays None here; the caller (`run`) leaves every other column of this
    # document untouched and only marks it `pending` for retry.
    if classified_type is None:
        return {
            "document_id": doc.id,
            "classified_type": None,
            "status": "pending",
            "type_evidence": {
                "model_id": meta.get("model_id"), "task": meta.get("task"),
                "error": meta.get("error"), "fallback_model": meta.get("fallback_model", False),
                "sso_expired": meta.get("sso_expired", False),
            },
            "segments": [],
        }

    confidence = float(llm.get("confidence") or 0.0)
    # stage is always derived from doc_type via the fixed D-025 taxonomy table, never trusted
    # from the LLM's own "stage" field directly: the model can (and did, in round 1: FUNDS docs
    # coming back stage=PAYMENT instead of PRICE_NEGOTIATION) return an internally inconsistent
    # doc_type/stage pair, since it predicts both independently.
    stage = stage_of(classified_type)
    scheme_relevance = llm.get("scheme_relevance") or "unknown"

    raw_segments = llm.get("segments") or [{
        "page_from": 1, "page_to": len(doc.pages) or 1,
        "doc_type": classified_type, "scheme_relevance": scheme_relevance,
    }]
    segments = []
    for i, seg in enumerate(raw_segments, start=1):
        seg_type = seg.get("doc_type") or classified_type
        segments.append({
            "seg_no": i,
            "page_start": int(seg.get("page_from") or 1),
            "page_end": int(seg.get("page_to") or seg.get("page_from") or 1),
            "doc_type": seg_type,
            "type_confidence": confidence,
            "type_evidence": {"rationale": llm.get("rationale")},
            "scheme_relevance": seg.get("scheme_relevance") or scheme_relevance,
            "stage": stage_of(seg_type),
        })

    # local regex header-parse fills date_precision/lang (not in the LLM schema) and backstops
    # unit/block/village/date if the LLM left them null.
    local_headers = parse_headers(class_text)

    return {
        "document_id": doc.id,
        "classified_type": classified_type,
        "type_confidence": round(confidence, 4),
        "type_evidence": {
            "model_id": meta.get("model_id"), "task": meta.get("task"),
            "from_cache": meta.get("from_cache"), "rule_hints": meta.get("rule_hints"),
            "rationale": llm.get("rationale"),
        },
        "scheme_relevance": scheme_relevance,
        "stage": stage,
        "payee_kind": llm.get("payee_kind"),
        "committee_level": llm.get("committee_level"),
        "unit_no": llm.get("unit_no") or local_headers.unit_no,
        "block_no": llm.get("block_no") or local_headers.block_no,
        "village": llm.get("village") or local_headers.village,
        "doc_no": local_headers.doc_no,
        "doc_date": normalize_llm_date(llm.get("doc_date")) or local_headers.doc_date,
        "date_precision": local_headers.date_precision,
        "lang": local_headers.lang,
        "status": "classified",
        "segments": segments,
    }


DOC_UPDATE = """
UPDATE document SET
  classified_type = %(classified_type)s, type_confidence = %(type_confidence)s,
  type_evidence = %(type_evidence)s, scheme_relevance = %(scheme_relevance)s,
  stage = %(stage)s, payee_kind = %(payee_kind)s, committee_level = %(committee_level)s,
  unit_no = %(unit_no)s, block_no = %(block_no)s, village = %(village)s, doc_no = %(doc_no)s,
  doc_date = %(doc_date)s, date_precision = %(date_precision)s, lang = %(lang)s,
  status = %(status)s, updated_at = now()
WHERE id = %(document_id)s;
"""

# A failed / non-Bedrock-fallback call touches ONLY status + type_evidence (for diagnostics);
# classified_type, stage, scheme_relevance, unit/block/village/date and segments are left exactly
# as they were (incident, 2026-09-27: a prior version of this UPDATE overwrote 2,257 documents'
# real classifications with a fabricated "OTHER" when Bedrock calls failed after an SSO expiry).
PENDING_UPDATE = """
UPDATE document SET
  status = 'pending', type_evidence = %(type_evidence)s, updated_at = now()
WHERE id = %(document_id)s;
"""

SEGMENT_DELETE = "DELETE FROM document_segment WHERE document_id = %s;"
SEGMENT_INSERT = """
INSERT INTO document_segment (document_id, seg_no, page_start, page_end, doc_type,
                               type_confidence, type_evidence, scheme_relevance, stage)
VALUES (%(document_id)s, %(seg_no)s, %(page_start)s, %(page_end)s, %(doc_type)s,
        %(type_confidence)s, %(type_evidence)s, %(scheme_relevance)s, %(stage)s);
"""


def run(limit: int | None = None, use_ocr: bool = True, ocr_workers: int = 12,
        llm_workers: int = 4, labels_only: bool = False, skip_preflight: bool = False) -> dict:
    t0 = time.time()

    # Preflight (incident, 2026-09-27): confirm Bedrock Ministral is actually answering before
    # spending any OCR time -- an expired AWS SSO session must stop the run cleanly, not burn
    # hours producing thousands of `pending` documents.
    if not skip_preflight:
        ok, reason = preflight_bedrock()
        if not ok:
            return {"stopped": True, "stop_reason": reason, "n_documents": 0}

    only_sha256s = labelled_sha256s() if labels_only else None
    with get_conn() as conn:
        docs = _fetch_docs(conn, limit, only_sha256s)

    if use_ocr and any(
        not (tl and len(t.replace(" ", "")) >= TEXT_OK_MIN_CHARS)
        for d in docs for _, t, tl, _ in d.pages[:HEADER_PAGES]
    ):
        with ThreadPoolExecutor(ocr_workers) as pool:
            header_texts = list(pool.map(lambda d: _resolve_header_pages_text(d, use_ocr), docs))
    else:
        header_texts = [_resolve_header_pages_text(d, False) for d in docs]

    # the LLM step is network-bound (Bedrock/Groq latency): parallelise with a thread pool.
    # `llm_workers=4` per D-030; the content-hash cache (.llm_cache) makes concurrent workers
    # and re-runs safe (each unique input is only ever billed once).
    with ThreadPoolExecutor(llm_workers) as pool:
        results = list(pool.map(lambda pair: classify_one(*pair), zip(docs, header_texts)))

    n_disagree = 0
    n_pending = 0
    n_fallback_seen = 0
    n_sso_expired_seen = 0
    scheme_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    write_errors: list[dict] = []
    with get_conn() as conn:
        for doc, res in zip(docs, results):
            row = dict(res)
            row["type_evidence"] = Jsonb(_json_safe(row["type_evidence"]))
            segments = row.pop("segments")
            try:
                with conn.transaction():  # a savepoint: one bad row must never abort the batch
                    if row["classified_type"] is None:
                        # never overwrite an existing classification with a failure (incident,
                        # 2026-09-27): only status + type_evidence are touched.
                        conn.execute(PENDING_UPDATE, row)
                    else:
                        conn.execute(DOC_UPDATE, row)
                        conn.execute(SEGMENT_DELETE, (doc.id,))
                        for seg in segments:
                            seg = dict(seg)
                            seg["document_id"] = doc.id
                            seg["type_evidence"] = Jsonb(_json_safe(seg["type_evidence"]))
                            conn.execute(SEGMENT_INSERT, seg)
            except Exception as exc:  # noqa: BLE001 - never let one row crash the whole batch
                write_errors.append({"document_id": doc.id, "sha256": doc.sha256, "error": str(exc)})
                continue
            if res["status"] == "pending":
                n_pending += 1
                if res["type_evidence"].get("fallback_model"):
                    n_fallback_seen += 1
                if res["type_evidence"].get("sso_expired"):
                    n_sso_expired_seen += 1
                continue  # a pending doc's classified_type/scheme_relevance are untouched/stale
            if folder_disagrees(doc.folder_label, res["classified_type"]):
                n_disagree += 1
            type_counts[res["classified_type"]] = type_counts.get(res["classified_type"], 0) + 1
            scheme_counts[res["scheme_relevance"]] = scheme_counts.get(res["scheme_relevance"], 0) + 1
        conn.commit()

    from . import quick_ocr
    quick_ocr.shutdown()

    return {
        "n_documents": len(docs),
        "write_errors": write_errors,
        "pending": n_pending,
        "fallback_model_seen": n_fallback_seen,
        "sso_expired_seen": n_sso_expired_seen,
        "folder_vs_class_disagreements": n_disagree,
        "classified_type_counts": type_counts,
        "scheme_relevance_counts": scheme_counts,
        "elapsed_s": round(time.time() - t0, 1),
    }


def reset_pending(conn) -> int:
    """One-off incident recovery (2026-09-27): documents whose `classified_type` was
    incorrectly overwritten with a fabricated "OTHER" by a pre-fix run (marked by
    `status='review_queue'`, the old sentinel) are reset to an honest `pending` state --
    `classified_type`/`stage`/`scheme_relevance` cleared rather than left as a wrong value, since
    the original (pre-LLM, rule-based v1) classification was not preserved anywhere and cannot
    be reliably restored. Returns the number of rows reset."""
    result = conn.execute(
        "UPDATE document SET classified_type = NULL, stage = NULL, scheme_relevance = NULL, "
        "type_confidence = NULL, status = 'pending' WHERE status = 'review_queue'"
    )
    conn.commit()
    return result.rowcount


def _json_safe(d: dict) -> dict:
    """jsonb-safe copy: drop keys with non-JSON-serialisable values (e.g. a stray exception)."""
    import json
    out = {}
    for k, v in d.items():
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out
