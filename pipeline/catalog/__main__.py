"""`make catalog` -> `python -m pipeline.catalog [--limit N]` (T1.1)."""
from __future__ import annotations

import argparse
import json

from .run import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Catalog, dedup and render Dataset/Land_documents PDFs.")
    ap.add_argument("--limit", type=int, default=None, help="Process only the first N discovered files (smoke run).")
    ap.add_argument("--workers", type=int, default=12, help="Process-pool size (default 12).")
    args = ap.parse_args()

    stats = run(limit=args.limit, workers=args.workers)
    print(json.dumps(stats, indent=2, default=str))
    if stats["hash_errors"] or stats["process_errors"]:
        print(f"WARNING: {len(stats['hash_errors'])} hash errors, {len(stats['process_errors'])} process errors")


if __name__ == "__main__":
    main()
