"""`make ingest-holdout-test` — proves the new-document ingest path (app/ingest) works on "new"
input without destroying data.

Picks 2 already-processed documents (one AWARD_7_2, one CHITTA if available), copies each PDF with
a unique trailing marker appended (so its sha256/md5 differ from the original — this is what lets
the run bypass the MD5 dedup honestly, rather than special-casing it in code: to the ingest
pipeline it really is a "new" file), runs it through the same stage functions the API's background
orchestrator uses (store→catalog→classify→extract→load→match→findings, tagged
`folder_label='ingest_holdout_test:<tag>'` for identification), compares the new extraction/
parcel_fact rows against the original document's, then deletes ONLY the holdout document rows
(cascade: page/extraction/parcel_fact/acquisition_event/*_link/review_queue all key off
document_id/extraction_id ON DELETE CASCADE) and re-runs `findings` once more so the shared
`finding` table is restored to its pre-holdout state (findings are a deterministic recompute over
current facts — see pipeline/findings/rules.py). Original document rows are never touched or
deleted.

Cost: this calls the same router-backed classify/extract stages the real ingest endpoint does.
Re-ingesting the SAME page images (same PDF bytes underneath the trailing marker) means the VLM
content-hash cache (pipeline.extract.vlm's ContentCache, keyed on rendered image bytes) will
normally hit on every read (free) — this is reported honestly per page. Pass --no-cache for a
*fresh* read (temporarily points the cache at a throwaway directory) — this is the meaningful
quota-costing test and should only be run with the user's go-ahead.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import psycopg
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
HOLDOUT_DIR = REPO_ROOT / "data" / "eval" / "ingest_holdout_tmp"
OUT_JSON = REPO_ROOT / "data" / "eval" / "ingest_holdout.json"

CANDIDATE_TYPES = ["AWARD_7_2", "CHITTA"]


def _conn() -> psycopg.Connection:
    env = dotenv_values(REPO_ROOT / ".env")
    import os

    url = os.environ.get("DATABASE_URL") or env.get("DATABASE_URL")
    return psycopg.connect(url)


def pick_candidates(conn: psycopg.Connection) -> list[dict]:
    """One already-processed, already-extracted document per type in CANDIDATE_TYPES, preferring
    small page counts (keeps the test's extraction cost/time bounded) and more facts (a richer
    comparison)."""
    picks = []
    for t in CANDIDATE_TYPES:
        row = conn.execute(
            """SELECT d.id, d.path, d.pages, d.classified_type, d.scheme_relevance,
                      count(DISTINCT e.id) AS extraction_rows, count(DISTINCT f.id) AS facts
               FROM document d
               JOIN extraction e ON e.document_id = d.id
               LEFT JOIN parcel_fact f ON f.extraction_id = e.id
               WHERE d.classified_type = %s AND d.pages <= 4
               GROUP BY d.id, d.path, d.pages, d.classified_type, d.scheme_relevance
               ORDER BY facts DESC LIMIT 1""",
            (t,),
        ).fetchone()
        if row is None:
            print(f"no already-processed {t} document with <=4 pages found; skipping")
            continue
        cols = ["id", "path", "pages", "classified_type", "scheme_relevance", "extraction_rows", "facts"]
        picks.append(dict(zip(cols, row)))
    return picks


_RECORD_KEYS = ("record_type", "survey_no", "sub_div", "extent_ha", "extent_ac", "amount_rs",
               "classification", "patta_no")


def _page_records_of(document_id: int) -> list[dict]:
    """Every extraction record (pipeline.extract.page's own per-record dict) written to
    data/extract/pages/<document_id>/*.json — the extraction stage's real output, independent of
    whether the DB `load` step actually inserted a row for it (see the content-hash note below)."""
    out_dir = REPO_ROOT / "data" / "extract" / "pages" / str(document_id)
    records = []
    if out_dir.exists():
        for f in sorted(out_dir.glob("*.json")):
            page = json.loads(f.read_text())
            for r in page.get("records") or []:
                if r.get("record_type") in ("parcel_row", "form_f"):
                    records.append(r)
    return records


def _record_key(r: dict) -> tuple:
    def norm(v):
        if isinstance(v, float):
            return round(v, 4)
        return v
    return tuple(norm(r.get(k)) for k in _RECORD_KEYS)


def compare(conn: psycopg.Connection, original_id: int, holdout_id: int) -> dict:
    """Compares the extraction stage's *page-JSON* output (not DB rows) for both documents: the
    holdout's rendered pages are pixel-identical to the original's (same underlying scan, only a
    trailing comment appended after the PDF's own %%EOF, which no PDF renderer reads), so
    `pipeline.load.extractions`'s content-hash idempotency key (image bytes + schema_version, by
    design **not** document_id — see its module docstring) correctly treats the holdout's would-be
    `extraction`/`parcel_fact` rows as already loaded (by the original document) and skips
    re-inserting them. That is the loader working as intended, not a defect in this ingest path; a
    genuinely different scan of the same document would render to different bytes and load
    normally. Comparing the two documents' extraction-stage JSON output directly (produced fresh
    per document_id, regardless of the DB load outcome) is what actually tests "did extraction
    correctly re-derive the same facts from this new document" here.
    """
    orig_records = [_record_key(r) for r in _page_records_of(original_id)]
    hold_records = [_record_key(r) for r in _page_records_of(holdout_id)]
    orig_set, hold_set = set(orig_records), set(hold_records)
    overlap = orig_set & hold_set
    match_pct = round(100 * len(overlap) / len(orig_set), 1) if orig_set else None
    orig_surveys = {r[1] for r in orig_set if r[1]}
    hold_surveys = {r[1] for r in hold_set if r[1]}
    orig_db_rows = conn.execute("SELECT count(*) FROM extraction WHERE document_id = %s",
                               (original_id,)).fetchone()[0]
    hold_db_rows = conn.execute("SELECT count(*) FROM extraction WHERE document_id = %s",
                               (holdout_id,)).fetchone()[0]
    return {
        "original_document_id": original_id, "holdout_document_id": holdout_id,
        "original_page_json_records": len(orig_records), "holdout_page_json_records": len(hold_records),
        "original_unique_records": len(orig_set), "holdout_unique_records": len(hold_set),
        "records_matched_exactly": len(overlap), "record_match_pct_of_original": match_pct,
        "survey_numbers_original": sorted(orig_surveys), "survey_numbers_holdout": sorted(hold_surveys),
        "survey_numbers_identical": orig_surveys == hold_surveys,
        "original_db_extraction_rows": orig_db_rows, "holdout_db_extraction_rows": hold_db_rows,
        "note": ("holdout_db_extraction_rows is expected to be 0: the loader's content-hash "
                "idempotency key is image bytes + schema_version, not document_id, so it correctly "
                "recognised these pages as already loaded under the original document and did not "
                "duplicate them. See this function's docstring."),
    }


def run_one(conn: psycopg.Connection, original: dict, tag: str, no_cache: bool) -> dict:
    from app.ingest import document_stages as ds

    src = REPO_ROOT / original["path"]
    content = src.read_bytes() + f"\n% ingest-holdout-test:{tag}:{original['id']}\n".encode()
    HOLDOUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = HOLDOUT_DIR / f"{tag}_{Path(original['path']).name}"
    dst.write_bytes(content)

    folder_label = f"ingest_holdout_test:{tag}"
    stages: dict[str, dict] = {}
    t0 = time.time()

    cat = ds.run_catalog_stage(conn, dst, folder_label)
    stages["catalog"] = cat
    holdout_id = cat["document_id"]
    assert holdout_id != original["id"], "holdout document must be a distinct row from the original"

    stages["classify"] = ds.run_classify_stage(conn, holdout_id)

    vlm_mod = None
    old_cache_dir = None
    if no_cache:
        from pipeline.extract import vlm as vlm_mod

        old_cache_dir = vlm_mod.CACHE_DIR
        vlm_mod.CACHE_DIR = REPO_ROOT / "data" / "eval" / "ingest_holdout_tmp" / f"cache_{tag}"
    try:
        stages["extract"] = ds.run_extract_stage(conn, holdout_id)
    finally:
        if vlm_mod is not None:
            vlm_mod.CACHE_DIR = old_cache_dir

    # from_cache honesty: inspect the page JSON this stage just wrote.
    out_dir = REPO_ROOT / "data" / "extract" / "pages" / str(holdout_id)
    cache_hits, cache_misses = 0, 0
    if out_dir.exists():
        for f in out_dir.glob("*.json"):
            page = json.loads(f.read_text())
            for r in page.get("reads") or []:
                if r.get("from_cache"):
                    cache_hits += 1
                elif r.get("ok"):
                    cache_misses += 1
    stages["extract"]["cache_hits"] = cache_hits
    stages["extract"]["cache_misses_fresh_reads"] = cache_misses

    stages["load"] = ds.run_load_stage(conn, holdout_id)
    stages["match"] = ds.run_match_stage(conn)
    stages["findings"] = ds.run_findings_stage(conn, holdout_id)

    cmp = compare(conn, original["id"], holdout_id)
    elapsed = round(time.time() - t0, 1)
    return {"original": original, "holdout_document_id": holdout_id, "tag": tag,
            "no_cache": no_cache, "stages": stages, "comparison": cmp, "elapsed_s": elapsed}


def cleanup(conn: psycopg.Connection, holdout_document_ids: list[int]) -> None:
    """Delete ONLY the holdout document rows (cascades to page/extraction/parcel_fact/
    acquisition_event/*_link/review_queue), never the originals, then re-run findings once more so
    the shared `finding` table (a full-corpus recompute, not scoped to one document) is restored to
    its pre-holdout state."""
    import pipeline.findings.__main__ as findings_main

    for did in holdout_document_ids:
        conn.execute("DELETE FROM document WHERE id = %s", (did,))
    conn.commit()
    fs = findings_main.run(conn)
    findings_main.upsert(conn, fs)
    conn.commit()
    shutil.rmtree(HOLDOUT_DIR, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the VLM content cache for a fresh (quota-costing) read; only with the user's go-ahead")
    args = ap.parse_args()

    conn = _conn()
    tag = uuid.uuid4().hex[:12]
    candidates = pick_candidates(conn)
    if not candidates:
        print("no candidate documents found (need an already-extracted AWARD_7_2 or CHITTA doc)")
        raise SystemExit(1)

    results = []
    holdout_ids = []
    try:
        for original in candidates:
            print(f"--- holdout-testing {original['classified_type']} document {original['id']} "
                 f"({original['path']}) ---")
            res = run_one(conn, original, f"{tag}_{original['id']}", args.no_cache)
            results.append(res)
            holdout_ids.append(res["holdout_document_id"])
            cmp = res["comparison"]
            print(f"  page-JSON extraction records: original={cmp['original_page_json_records']} "
                 f"holdout={cmp['holdout_page_json_records']}")
            print(f"  records matched exactly: {cmp['records_matched_exactly']}/{cmp['original_unique_records']} "
                 f"unique ({cmp['record_match_pct_of_original']}%)")
            print(f"  survey numbers identical: {cmp['survey_numbers_identical']}")
            print(f"  DB extraction rows (loader dedup): original={cmp['original_db_extraction_rows']} "
                 f"holdout={cmp['holdout_db_extraction_rows']} (0 expected — see note)")
            ex = res["stages"]["extract"]
            print(f"  extraction reads: {ex.get('cache_hits', 0)} from cache, "
                 f"{ex.get('cache_misses_fresh_reads', 0)} fresh (spent ${ex.get('spent_usd', 0)})")
    finally:
        cleanup(conn, holdout_ids)
        conn.close()

    summary = {"tag": tag, "no_cache": args.no_cache, "candidates": len(candidates), "results": results}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nwrote {OUT_JSON.relative_to(REPO_ROOT)}")
    print("cleanup: deleted holdout document rows", holdout_ids, "and re-ran findings once more "
         "to restore the shared finding table (original document rows were never touched)")


if __name__ == "__main__":
    main()
