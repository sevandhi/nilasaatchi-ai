"""Agent test fixtures: fixture KG (nila_fixture), isolated run store / ledger / checkpointer, scripted providers."""
from __future__ import annotations

import asyncio
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLOTS = os.path.join(REPO, "eval", "agent", "data", "plots_fixture.geojson")
SCANNED = os.path.join(REPO, "eval", "agent", "data", "scanned_award.pdf")


def _db_ok() -> bool:
    try:
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO, ".env"))
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def kg_dsn():
    if not _db_ok():
        pytest.skip("PostGIS container not reachable (make db-up)")
    from eval.agent.fixture_db import ensure_fixture_db
    return ensure_fixture_db()


@pytest.fixture
def agent_env(tmp_path, monkeypatch, kg_dsn):
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AGENT_CHECKPOINTER", "memory")
    monkeypatch.setenv("LEDGER_BACKEND", "sqlite")
    monkeypatch.setenv("AGENT_KG_URL", kg_dsn)
    from app.agent import runtime
    from app.ledger import set_ledger
    set_ledger(None)
    runtime.LATEST_STATE.clear()
    runtime.known_names.cache_clear()
    yield tmp_path
    set_ledger(None)


@pytest.fixture
def fake(agent_env, monkeypatch):
    from eval.agent.fakes import Script, install
    monkeypatch.setenv("ROUTER_MODE", "live")
    s = Script()
    install(s, agent_env / "router")
    return s


def run_events(request, **kw) -> list[dict]:
    from app.agent import run_request

    async def go():
        return [ev async for ev in run_request(request, **kw)]
    return asyncio.run(go())


def resume_events(run_id, answer) -> list[dict]:
    from app.agent import resume_run

    async def go():
        return [ev async for ev in resume_run(run_id, answer)]
    return asyncio.run(go())


def result_of(events):
    return next((e["data"] for e in events if e["type"] == "result"), None)
