import pytest

from app.domains import get_pack
from app.tools.sql_guard import SqlGuardError, guard_sql

ALLOW = set(get_pack("land_acquisition").sql_allowlist)


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM parcel", "WITH x AS (SELECT * FROM parcel) SELECT * FROM x",
    "SELECT v.name, geo_area_ha(ST_Union(p.geom)) FROM parcel p JOIN village v ON v.id=p.village_id GROUP BY 1",
    "SELECT stage FROM acquisition_event UNION SELECT state FROM parcel_season"])
def test_allowed(sql):
    g = guard_sql(sql, ALLOW)
    assert "LIMIT" in g.sql.upper()


@pytest.mark.parametrize("sql,needle", [
    ("DELETE FROM parcel", "only SELECT"), ("UPDATE parcel SET kide='x'", "only SELECT"),
    ("DROP TABLE parcel", "only SELECT"), ("SELECT * FROM owner", "not allowed"),
    ("SELECT * FROM document", "not allowed"), ("SELECT pg_sleep(9)", "pg_sleep"),
    ("SELECT 1; SELECT 2", "one statement"), ("SELECT * INTO x FROM parcel", "Into"),
    ("SELECT * FROM pg_catalog.pg_authid", "schema"), ("SELECT set_config('role','nila',false)", "set_config"),
    ("COPY parcel TO '/tmp/x'", ""), ("", "empty")])
def test_blocked(sql, needle):
    with pytest.raises(SqlGuardError) as e:
        guard_sql(sql, ALLOW)
    assert needle in str(e.value)


def test_limit_capped():
    assert guard_sql("SELECT * FROM parcel LIMIT 999999", ALLOW).sql.endswith("LIMIT 5000")
    assert guard_sql("SELECT * FROM parcel LIMIT 10", ALLOW).sql.endswith("LIMIT 10")


def test_agent_ro_blocks_writes_and_pii_tables(kg_dsn):
    import psycopg

    from app.tools.db import query
    with pytest.raises(psycopg.Error):
        query(kg_dsn, "SELECT canonical_name FROM owner")
    with pytest.raises(psycopg.Error):
        query(kg_dsn, "SELECT raw_cells FROM extraction")
    with pytest.raises(psycopg.Error):
        query(kg_dsn, "SELECT pg_sleep(6)")          # 5 s statement_timeout
