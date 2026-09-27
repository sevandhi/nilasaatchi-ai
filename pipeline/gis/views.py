"""(Re)apply db/views/*.sql in lexical order. Idempotent (CREATE OR REPLACE + GRANT)."""
from __future__ import annotations

import os
import pathlib

import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]
VIEWS_DIR = ROOT / "db" / "views"


def apply_views(conn: psycopg.Connection) -> list[str]:
    applied = []
    for f in sorted(VIEWS_DIR.glob("*.sql")):
        conn.execute(f.read_text(encoding="utf-8"))
        applied.append(f.name)
    return applied


def main() -> None:
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        names = apply_views(conn)
        conn.commit()
    print("views applied:", ", ".join(names))


if __name__ == "__main__":
    main()
