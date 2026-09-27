"""Integration test on the real loaded KG (needs PostGIS + `make migrate load-gis`)."""
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.db

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def conn():
    from dotenv import dotenv_values
    dsn = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    try:
        c = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"PostGIS not reachable: {e}")
    if c.execute("SELECT to_regclass('parcel')").fetchone()[0] is None or \
            c.execute("SELECT count(*) FROM parcel").fetchone()[0] == 0:
        pytest.skip("parcel not loaded (run make migrate load-gis)")
    yield c
    c.close()


def test_real_parcels_loaded_clean(conn):
    n, n_uid, bad = conn.execute("""
        SELECT count(*), count(DISTINCT parcel_uid),
               count(*) FILTER (WHERE NOT ST_IsValid(geom) OR ST_NDims(geom) <> 2 OR ST_SRID(geom) <> 4326)
        FROM parcel""").fetchone()
    assert (n, n_uid, bad) == (1242, 1242, 0)
    # the one invalid source polygon was repaired
    assert conn.execute("SELECT ST_IsValid(geom) FROM parcel WHERE parcel_uid = 'Keelathattaparai|443/1'").fetchone()[0]


def test_real_fmb_totals_geodesic_sum_and_union(conn):
    # D-023: geodesic areas; §9 anchors are geodesic sums; the dissolved union is the primary total
    total_sum, union = conn.execute("SELECT sum(area_ha_gis)::float8, geo_area_ha(ST_Union(geom)) FROM parcel").fetchone()
    assert abs(total_sum - 908.51) <= 0.005 * 908.51
    assert abs(union - 901.50) <= 0.005 * 901.50
    assert union < 904.40 < total_sum      # the withdrawn "map exceeds sanction" claim stays withdrawn


def test_area_ha_gis_is_geodesic(conn):
    bad = conn.execute("""SELECT count(*) FROM parcel
                          WHERE abs(area_ha_gis - (ST_Area(geom::geography) / 1e4)::numeric) > 0.00005""").fetchone()[0]
    assert bad == 0


def test_sql_geodesic_area_of_synthetic_equal_area_square(conn):
    # 100 m square in a park-centred Lambert equal-area CRS -> 4326 -> geography area = 1 ha exactly
    laea = "+proj=laea +lat_0=8.78 +lon_0=78.02 +datum=WGS84 +units=m +no_defs"
    ha = conn.execute("""SELECT geo_area_ha(ST_Transform(ST_SetSRID(ST_MakeEnvelope(0, 0, 100, 100), 0), %s, 4326))""",
                      (laea,)).fetchone()[0]
    assert ha == pytest.approx(1.0, abs=1e-4)


def test_fmb_qa_lists_overlaps_and_outside_survey(conn):
    counts = dict(conn.execute("SELECT issue, count(*) FROM fmb_qa GROUP BY issue").fetchall())
    assert counts["OVERLAP"] == 366
    assert 25 <= counts["OUTSIDE_SURVEY"] <= 30        # 28 geodesic (> 100 m2); 29 with UTM areas
    n_cross, largest = conn.execute("""SELECT count(*) FILTER (WHERE cross_village),
                                              (SELECT uid_a || ' x ' || uid_b FROM fmb_qa_overlap
                                               ORDER BY overlap_ha DESC LIMIT 1)
                                       FROM fmb_qa_overlap""").fetchone()
    assert n_cross == 110 and largest == "Melathattaparai|175 x Melathattaparai|176/1A"


def test_agent_ro_is_read_only(conn):
    with conn.transaction():
        conn.execute("SET LOCAL ROLE agent_ro")
        assert conn.execute("SELECT count(*) FROM v_parcel_status").fetchone()[0] == 1242
        assert conn.execute("SELECT count(*) FROM fmb_qa").fetchone()[0] > 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SET LOCAL ROLE agent_ro")
        conn.execute("UPDATE parcel SET land_id = land_id WHERE false")
    if conn.execute("SELECT to_regclass('document')").fetchone()[0] is not None:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SET LOCAL ROLE agent_ro")
            conn.execute("SELECT 1 FROM document LIMIT 1")
