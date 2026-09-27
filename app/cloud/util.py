"""Small stateless helpers shared by the exporter and the cloud app. Deliberately duplicated
(not imported) from `planet.chips.render.safe_name` / the parcel-uid path convention in
`app/api/routers/parcels.py`, so `app/cloud` never has to import the heavy `planet` package."""
from __future__ import annotations

import re


def safe_name(uid: str) -> str:
    """Same algorithm as `planet.chips.render.safe_name` — chip cache directory names on disk
    already use it, so a chip lookup must reproduce it exactly."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", uid.replace("|", "__").replace("/", "-"))


def safe_parcel_key(parcel_uid: str) -> str:
    """Filename-safe key for a parcel's pre-rendered snapshot files. Same alphabet as
    `safe_name` (kept as a separate function since the two happen to coincide today but serve
    different callers: chip cache dirs on disk vs. our own snapshot file names)."""
    return safe_name(parcel_uid)
