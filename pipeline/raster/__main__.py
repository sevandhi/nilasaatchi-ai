"""`make raster` -> `python -m pipeline.raster [--limit N]` (T1.5)."""
from __future__ import annotations

import argparse
import json

from .run import run


def main() -> None:
    ap = argparse.ArgumentParser(description="DEM/slope/JRC/GloFAS fetch + per-parcel zonal stats.")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N parcels (smoke run).")
    args = ap.parse_args()

    stats = run(limit=args.limit)
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
