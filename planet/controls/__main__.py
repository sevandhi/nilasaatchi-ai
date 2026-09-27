"""``python -m planet.controls [cells|extract|features|did|report|all] [--limit N] [--workers N] [--no-db]``"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.controls")
    ap.add_argument("step", nargs="?", default="all", choices=["cells", "extract", "features", "did", "report", "all"])
    ap.add_argument("--limit", type=int, default=None, help="scenes (extract) / parcels (did) for a smoke run")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--rebuild-cells", action="store_true")
    a = ap.parse_args(argv)
    import psycopg

    from planet.controls import extract as cx
    from planet.extract.run import dsn

    with psycopg.connect(dsn()) as conn:
        if a.step in ("cells", "extract", "all"):
            g = cx.build_cells(conn) if (a.rebuild_cells or not cx.CELLS_PARQUET.exists()) else cx.load_cells()
            print(f"control cells {len(g)}: {g.sector.value_counts().to_dict()}")
            if not a.no_db:
                cx.upsert_cells(conn, g)
        if a.step in ("extract", "all"):
            obs = cx.extract(conn, g, a.workers, a.limit)
            if not a.no_db:
                print("control_obs upserted", cx.upsert_obs(conn, obs))
        if a.step in ("features", "all"):
            from planet.controls import relative as rel

            rel.run_features(conn, no_db=a.no_db)
        if a.step in ("did", "all"):
            from planet.controls import relative as rel

            rel.run_did(conn, no_db=a.no_db, limit=a.limit)
        if a.step in ("report", "all"):
            from planet.controls import relative as rel

            rel.run_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
