"""Satellite evidence tools (owner: eo-engineer) for the P4 agent core.

Thin, typed entry points over ``planet``; every result carries evidence (scene ids / chip paths) and
caveats, and is a signal needing field verification.

- ``satellite_chip(parcel_uid, date)``          true-colour + NDVI chip PNGs with scene provenance
- ``landuse_state(parcel_uid, ag_year, season)`` student state + routing (VLM second opinion on demand)
- ``event_windows(parcel_uid)``                  state distribution per acquisition-event window (T3.6)
"""
from __future__ import annotations

from typing import Any

__all__ = ["event_windows", "landuse_state", "satellite_chip"]


def satellite_chip(parcel_uid: str, date: str, **kw: Any) -> dict[str, Any]:
    from planet.chips.render import satellite_chip as _chip

    out = _chip(parcel_uid, date, **kw)
    return {k: (str(v) if k in ("truecolor", "ndvi") else v) for k, v in out.items()}


def landuse_state(parcel_uid: str, ag_year: int, season: str, *, call_vlm: bool = True, **kw: Any) -> dict[str, Any]:
    from planet.classify.landuse import landuse_state as _state

    return _state(parcel_uid, ag_year, season, call_vlm=call_vlm, **kw)


def event_windows(parcel_uid: str, conn: Any = None) -> list[dict[str, Any]]:
    import psycopg

    from planet.extract.run import dsn

    own = conn is None
    conn = conn or psycopg.connect(dsn())
    try:
        rows = conn.execute(
            "SELECT window_name, start_date, end_date, date_basis, n_seasons, state_counts, state_share, "
            "dominant_state, seasons, caveats FROM parcel_event_window WHERE parcel_uid = %s "
            "ORDER BY array_position(ARRAY['pre_notification','pending','post_award','post_possession'], window_name)",
            (parcel_uid,)).fetchall()
    finally:
        if own:
            conn.close()
    keys = ["window", "start_date", "end_date", "date_basis", "n_seasons", "state_counts", "state_share",
            "dominant_state", "seasons", "caveats"]
    return [{**dict(zip(keys, r, strict=True)), "start_date": r[1].isoformat(), "end_date": r[2].isoformat()}
            for r in rows]
