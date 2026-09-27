"""`make ingest FILE=path/to.pdf [VILLAGE=hint]` — ingest one PDF headlessly (no API server needed),
driving the exact same stages the API's background orchestrator does, synchronously in this
process, printing each stage's outcome as it completes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.ingest import document_stages as ds
from app.ingest.db import get_conn


def ingest_one(pdf_path: Path, village: str | None = None) -> dict:
    content = pdf_path.read_bytes()
    if not content.startswith(b"%PDF"):
        raise SystemExit(f"{pdf_path} does not look like a PDF (missing %PDF header)")
    md5, stored_path = ds.store_pdf(content)

    with get_conn() as conn:
        dup_id = ds.find_duplicate(conn, md5)
    if dup_id is not None:
        print(f"duplicate of document {dup_id} (md5 {md5}); not reprocessing")
        return {"status": "duplicate", "duplicate_of": dup_id}

    rel_path = str(stored_path.relative_to(ds.REPO_ROOT))
    print(f"store: saved to {rel_path} (md5 {md5})")

    with get_conn() as conn:
        cat = ds.run_catalog_stage(conn, stored_path, village or "ingest_upload")
    print(f"catalog: {cat['status']} — {cat.get('detail')}")
    document_id = cat["document_id"]

    for name, fn in (
        ("classify", lambda conn: ds.run_classify_stage(conn, document_id)),
        ("extract", lambda conn: ds.run_extract_stage(conn, document_id)),
        ("load", lambda conn: ds.run_load_stage(conn, document_id)),
        ("match", lambda conn: ds.run_match_stage(conn)),
        ("findings", lambda conn: ds.run_findings_stage(conn, document_id)),
    ):
        with get_conn() as conn:
            res = fn(conn)
        print(f"{name}: {res['status']} — {res.get('detail')}")
        if res["status"] == "failed":
            print(f"stopping: {name} failed", file=sys.stderr)
            return {"status": "failed", "stage": name, "detail": res.get("detail"), "document_id": document_id}

    with get_conn() as conn:
        summary = ds.document_summary(conn, document_id)
    return {"status": "done", **summary}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", required=True, help="path to the PDF to ingest")
    ap.add_argument("--village", default=None, help="optional village hint (used as a classifier prior)")
    args = ap.parse_args()

    path = Path(args.file).resolve()
    if not path.exists():
        raise SystemExit(f"no such file: {path}")
    result = ingest_one(path, args.village)
    print(json.dumps(result, indent=2, default=str))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
