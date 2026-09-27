"""Real-provider cases.

* `test_live_cases` (@live): runs the cases against real providers. Record fixtures with
    LIVE=1 ROUTER_MODE=record uv run pytest -m live tests/router/test_live_and_recorded.py
* `test_replay_recorded_cases`: offline replay of those recorded fixtures (runs in `make test`).
"""
from __future__ import annotations

import io
import os

import pytest

from app.router import call
from app.router.fixtures import fixture_dir

SQL_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
              "additionalProperties": False}
VISION_SCHEMA = {"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"],
                 "additionalProperties": False}


def _green_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (20, 160, 40)).save(buf, "PNG")
    return buf.getvalue()


CASES = [
    ("sql", {"payload": {"prompt": "Write one PostgreSQL query counting rows in table parcel.",
                          "max_tokens": 200, "step_id": "rec-sql"},
                 "schema": SQL_SCHEMA, "privacy_tier": "PII"}),
    ("satellite_second_opinion", {"payload": {"prompt": "Name the dominant colour of the image in one word.",
                                               "max_tokens": 200, "step_id": "rec-sat"},
                                      "schema": VISION_SCHEMA, "privacy_tier": "PUBLIC", "images": "green"}),
]


def _run(task, kw):
    kw = dict(kw)
    if kw.get("images") == "green":
        kw["images"] = [_green_png()]
    return call(task, **kw)


def _check(task, res):
    assert res.ok, res.error
    if task == "sql":
        assert "parcel" in res.data["sql"].lower()
    else:
        assert "green" in res.data["color"].lower()


@pytest.mark.live
@pytest.mark.parametrize("task,kw", CASES, ids=[c[0] for c in CASES])
def test_live_cases(task, kw):
    res = _run(task, kw)
    _check(task, res)
    assert res.latency_ms > 0 and res.route_log["mode"] in ("live", "record")


@pytest.mark.parametrize("task,kw", CASES, ids=[c[0] for c in CASES])
def test_replay_recorded_cases(task, kw):
    if os.environ.get("ROUTER_MODE") != "replay":
        pytest.skip("replay-only test")
    if not any(fixture_dir().rglob("*.json")):
        pytest.skip("no recorded fixtures")
    a = _run(task, kw)
    b = _run(task, kw)
    _check(task, a)
    assert (a.model_id, a.text, a.data, a.latency_ms) == (b.model_id, b.text, b.data, b.latency_ms)
    assert a.route_log["mode"] == "replay"
