"""`make classify` -> `python -m pipeline.classify [--limit N]` (T1.2 header pre-parse + T1.6
doc-type/scheme classifier, D-030: LLM decision step via the router)."""
from __future__ import annotations

import argparse
import json

from ..catalog.dbutil import get_conn
from .run import reset_pending, run


def main() -> None:
    ap = argparse.ArgumentParser(description="Header pre-parse + LLM doc-type/scheme classifier (D-030).")
    ap.add_argument("--limit", type=int, default=None, help="Classify only the first N documents (smoke run).")
    ap.add_argument("--no-ocr", action="store_true", help="Skip the quick-Tesseract fallback (text-layer only).")
    ap.add_argument("--llm-workers", type=int, default=4, help="Parallel LLM-call workers (default 4, per D-030).")
    ap.add_argument("--labels-only", action="store_true",
                     help="Classify only the eval/classify/labels.csv sha256 set (cheap prompt-tuning round).")
    ap.add_argument("--skip-preflight", action="store_true",
                     help="Skip the one-call Bedrock-availability check (offline/replay runs only).")
    ap.add_argument("--reset-pending", action="store_true",
                     help="One-off incident recovery: reset every status='review_queue' row "
                          "(classified_type overwritten with a fabricated OTHER by a pre-fix run) "
                          "to an honest pending state, then exit.")
    args = ap.parse_args()

    if args.reset_pending:
        with get_conn() as conn:
            n = reset_pending(conn)
        print(json.dumps({"reset_to_pending": n}, indent=2))
        return

    stats = run(limit=args.limit, use_ocr=not args.no_ocr, llm_workers=args.llm_workers,
                labels_only=args.labels_only, skip_preflight=args.skip_preflight)
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
