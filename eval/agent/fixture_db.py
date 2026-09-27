"""Create / refresh the fixture knowledge-graph database (tests/fixtures/kg_small.sql).

The fixture DB lives in the same PostGIS container as the real KG (database ``nila_fixture``). It gets the
real migrations (0001-0010 + 0012), then kg_small.sql, then db/views/*.sql (materialised FMB-QA views are
computed from the fixture parcels). A sha256 over all inputs is stored in ``fixture_meta``; the DB is
rebuilt only when an input changes.

    uv run python -m eval.agent.fixture_db [--rebuild]      -> prints the DSN
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

ROOT = Path(__file__).resolve().parents[2]
KG_SQL = ROOT / "tests" / "fixtures" / "kg_small.sql"
FIXTURE_DB = os.environ.get("FIXTURE_DB_NAME", "nila_fixture")
EXCLUDE_MIGRATIONS = {"0011"}        # backend's workspace/run tables are not needed by the KG fixture


def base_dsn() -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    d = os.environ.get("DATABASE_URL")
    if not d:
        raise RuntimeError("DATABASE_URL not set")
    return d


def with_db(dsn: str, db: str) -> str:
    u = urlsplit(dsn)
    return urlunsplit((u.scheme, u.netloc, "/" + db, u.query, u.fragment))


def _inputs() -> list[Path]:
    migs = [p for p in sorted((ROOT / "db" / "migrations").glob("*.sql")) if p.name[:4] not in EXCLUDE_MIGRATIONS]
    return migs + [KG_SQL] + sorted((ROOT / "db" / "views").glob("*.sql"))


def _digest(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def ensure_fixture_db(rebuild: bool = False) -> str:
    base = base_dsn()
    dsn = with_db(base, FIXTURE_DB)
    files = _inputs()
    digest = _digest(files)
    with psycopg.connect(base, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (FIXTURE_DB,)).fetchone()
    if exists and not rebuild:
        try:
            with psycopg.connect(dsn) as c:
                r = c.execute("SELECT sha256 FROM fixture_meta").fetchone()
            if r and r[0] == digest:
                return dsn
        except psycopg.Error:
            pass
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{FIXTURE_DB}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{FIXTURE_DB}"')
    with psycopg.connect(dsn, autocommit=True) as c:
        for p in files:
            c.execute(p.read_text(encoding="utf-8"))
        c.execute("CREATE TABLE fixture_meta (sha256 text NOT NULL)")
        c.execute("INSERT INTO fixture_meta VALUES (%s)", (digest,))
    return dsn


if __name__ == "__main__":
    print(ensure_fixture_db(rebuild="--rebuild" in sys.argv))
