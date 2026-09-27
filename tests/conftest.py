"""Shared pytest setup: offline by default (ROUTER_MODE=replay), isolated router state per test.

Live tests (`@pytest.mark.live`) run only with LIVE=1.
"""
from __future__ import annotations

import os

import pytest

LIVE = os.environ.get("LIVE") == "1"


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live test: set LIVE=1 to run (uses real quota)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _router_isolation(tmp_path, monkeypatch):
    """Every test gets its own data dir, no chaos, no doctor file, no .env, replay mode."""
    monkeypatch.setenv("ROUTER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROUTER_USE_DOCTOR", "0")
    monkeypatch.delenv("ROUTER_CHAOS", raising=False)
    monkeypatch.delenv("ROUTER_USE_DEMO_RESERVE", raising=False)
    monkeypatch.delenv("MISTRAL_TRAINING_OPTOUT_CONFIRMED", raising=False)
    monkeypatch.setenv("ROUTER_AWS_CE_REFRESH", "0")      # never call Cost Explorer from tests
    monkeypatch.setenv("AWS_SPEND_CAP_USD", "15")
    if not LIVE:
        monkeypatch.setenv("ROUTER_MODE", "replay")
        monkeypatch.setenv("ROUTER_NO_DOTENV", "1")
    try:
        from app.router import chaos, core
    except ImportError:   # router not importable -> nothing to reset
        yield
        return
    from app.router import embeddings
    chaos.set_chaos(None)
    core.reset_default_router()
    embeddings.reset_default_embedder()
    yield
    chaos.set_chaos(None)
    core.reset_default_router()
    embeddings.reset_default_embedder()
