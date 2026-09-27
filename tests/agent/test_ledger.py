import json
import sqlite3

import pytest

from app.ledger import GENESIS, Ledger, entry_hash


def _fill(led, run="r1", n=5):
    for i in range(n):
        led.append(run, f"node{i}", "status", {"i": i, "private": {"x": 1}, "text": "நில"})


def test_chain_ok_and_genesis(tmp_path):
    led = Ledger(sqlite_path=tmp_path / "l.sqlite")
    _fill(led)
    es = led.entries("r1")
    assert es[0].prev_hash == GENESIS
    assert es[0].hash == entry_hash(GENESIS, "r1", 1, "node0", {"i": 0, "text": "நில"})
    assert "private" not in es[0].payload
    assert led.verify().ok


def test_tamper_payload_detected_at_seq(tmp_path):
    p = tmp_path / "l.sqlite"
    led = Ledger(sqlite_path=p)
    _fill(led)
    c = sqlite3.connect(p)
    c.execute("UPDATE agent_ledger SET payload = ? WHERE run_id='r1' AND seq=3", (json.dumps({"i": 999}),))
    c.commit()
    rep = Ledger(sqlite_path=p).verify("r1")
    assert not rep.ok and rep.first_bad["seq"] == 3


def test_deleted_row_detected(tmp_path):
    p = tmp_path / "l.sqlite"
    led = Ledger(sqlite_path=p)
    _fill(led)
    c = sqlite3.connect(p)
    c.execute("DELETE FROM agent_ledger WHERE seq=2")
    c.commit()
    rep = Ledger(sqlite_path=p).verify()
    assert not rep.ok and rep.first_bad["seq"] == 3


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LEDGER_BACKEND", "sqlite")
    monkeypatch.setenv("ROUTER_DATA_DIR", str(tmp_path))
    from app.ledger import set_ledger
    from app.ledger.__main__ import main
    set_ledger(None)
    from app.ledger import get_ledger
    _fill(get_ledger(), "cli")
    assert main(["verify"]) == 0
    c = sqlite3.connect(tmp_path / "ledger.sqlite")
    c.execute("UPDATE agent_ledger SET node='evil' WHERE seq=4")
    c.commit()
    set_ledger(None)
    assert main(["verify", "--run", "cli"]) == 1
    assert "first bad seq=4" in capsys.readouterr().out
    set_ledger(None)


@pytest.mark.db
def test_postgres_backend_tamper(kg_dsn):
    import uuid

    import psycopg
    led = Ledger(dsn=kg_dsn)
    run = "t-" + uuid.uuid4().hex[:8]
    _fill(led, run)
    assert led.verify(run).ok
    with psycopg.connect(kg_dsn, autocommit=True) as c:
        c.execute("UPDATE agent_ledger SET payload = payload || '{\"i\": 42}'::jsonb WHERE run_id=%s AND seq=2", (run,))
    rep = Ledger(dsn=kg_dsn).verify(run)
    assert not rep.ok and rep.first_bad["seq"] == 2
    with psycopg.connect(kg_dsn, autocommit=True) as c:
        c.execute("DELETE FROM agent_ledger WHERE run_id=%s", (run,))
