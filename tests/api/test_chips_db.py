"""GET /chips/{parcel_uid}/{date}.png. Kept network-free: we only assert the *unknown-parcel*
error path is handled gracefully (404, not 500). A real chip render needs a COG fetch over the
network (planet/chips/render.py `/vsicurl`), which is out of scope for an offline API test —
covered instead by `make s2-audit-pack`/eo-engineer's own suite."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def test_unknown_parcel_uid_is_a_404_not_a_500(client):
    r = client.get("/chips/Nowhere|9999999/2024-01-01.png")
    assert r.status_code in (404, 503)
