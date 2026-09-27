"""T5.2 lifecycle views: synthetic events on a synthetic parcel (rolled back) + real-data sanity."""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UID = "Allikulam|TEST/1"


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    from dotenv import dotenv_values
    dsn = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not dsn:
        pytest.skip("no DATABASE_URL")
    try:
        c = psycopg.connect(dsn, connect_timeout=3)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"db down: {e}")
    if not c.execute("SELECT to_regclass('v_parcel_lifecycle')").fetchone()[0]:
        pytest.skip("views not applied")
    yield c
    c.rollback()
    c.close()


def _setup(c):
    c.execute("""INSERT INTO parcel (parcel_uid, kide, village_id, survey_no, sub_div, unit_id, block_id, geom, area_ha_gis)
                 VALUES (%s, 'TEST/1', 1, 'TEST', '1', 99, 99,
                         ST_Multi(ST_GeomFromText('POLYGON((78 8.8,78.001 8.8,78.001 8.801,78 8.801,78 8.8))',4326)), 1.2000)""",
              (UID,))


def _event(c, stage, date, key, kind="parcel"):
    eid = c.execute("""INSERT INTO acquisition_event (village, stage, event_date, date_precision, source, event_key, match_status)
                       VALUES ('Allikulam', %s, %s, 'day', 'test', %s, 'accepted') RETURNING id""",
                    (stage, date, key)).fetchone()[0]
    c.execute("INSERT INTO acquisition_event_link VALUES (%s, %s, %s, 1.0)", (eid, UID, kind))


def _row(c):
    return c.execute("""SELECT current_stage, stalled_flag, missing_mandatory, exemption_conflict, n_events,
                               next_expected_stage FROM v_parcel_lifecycle WHERE parcel_uid = %s""", (UID,)).fetchone()


def test_no_events(conn):
    _setup(conn)
    assert _row(conn) == (None, None, [], False, 0, "SEC_3_1")


def test_award_not_paid_is_stalled(conn):
    _setup(conn)
    _event(conn, "SEC_3_1", "2023-01-01", "t1")
    _event(conn, "AWARD", "2024-01-01", "t2")
    stage, flag, missing, exc, n, nxt = _row(conn)
    assert (stage, flag, n, nxt) == ("AWARD", "AWARD_NOT_PAID", 2, "PAYMENT")
    assert missing == ["SEC_3_2"]


def test_recent_payment_not_stalled_then_possession(conn):
    _setup(conn)
    _event(conn, "AWARD", "2024-01-01", "t1")
    _event(conn, "PAYMENT", "2999-01-01", "t2")     # future date -> negative days -> not stalled
    assert _row(conn)[:2] == ("PAYMENT", None)
    _event(conn, "POSSESSION", "2020-01-01", "t3")
    assert _row(conn)[:2] == ("POSSESSION", "POSSESSION_NO_MUTATION")


def test_block_level_evidence_is_flagged(conn):
    _setup(conn)
    _event(conn, "AWARD", "2024-01-01", "t1")
    _event(conn, "PAYMENT", "2024-02-01", "t2", kind="block_level")
    r = conn.execute("""SELECT current_stage, current_stage_evidence, current_stage_parcel_only, stalled_flag,
                               block_level_stages FROM v_parcel_lifecycle WHERE parcel_uid = %s""", (UID,)).fetchone()
    assert r == ("PAYMENT", "block_level", "AWARD", "PAID_NO_POSSESSION", ["PAYMENT"])


def test_exemption_conflict(conn):
    _setup(conn)
    _event(conn, "EXEMPTION", "2023-01-01", "t1")
    _event(conn, "AWARD", "2024-01-01", "t2")
    assert _row(conn)[3] is True


def test_real_data_matrix_covers_every_parcel(conn):
    n_parcels, ha = conn.execute("SELECT count(*), sum(area_ha_gis) FROM parcel").fetchone()
    assert conn.execute("SELECT count(*) FROM v_parcel_lifecycle").fetchone()[0] == n_parcels
    m_parcels, m_ha = conn.execute("SELECT sum(parcels), sum(ha_sum) FROM v_village_stage_matrix").fetchone()
    assert m_parcels == n_parcels
    assert abs(float(m_ha) - float(ha)) < 0.01
