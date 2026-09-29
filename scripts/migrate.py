"""Apply db/migrations/*.sql in lexical order, tracking applied files in schema_migrations."""
import os
import pathlib

import psycopg
from dotenv import load_dotenv

load_dotenv()
ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    files = sorted((ROOT / "db/migrations").glob("*.sql"))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in files:
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
            print(f"applied {f.name}")
        # pg_restore creates materialized views but can fail to fill them (their SQL uses PostGIS types that are
        # not on the restore's search_path), leaving "has not been populated" errors: fill any empty ones here
        for schema, name in conn.execute("SELECT schemaname, matviewname FROM pg_matviews WHERE NOT ispopulated").fetchall():
            conn.execute(f'REFRESH MATERIALIZED VIEW "{schema}"."{name}"')
            print(f"refreshed materialized view {schema}.{name}")
    print(f"migrations up to date ({len(files)} files)")


if __name__ == "__main__":
    main()
