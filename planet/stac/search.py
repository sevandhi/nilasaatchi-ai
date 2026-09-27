"""Sentinel-2 L2A STAC search with processing-baseline de-duplication.

Primary source: Element84 Earth Search (anonymous, AWS open data).
Mirror: Microsoft Planetary Computer (anonymous SAS signing via ``planetary-computer``).

Every returned :class:`SceneRecord` carries the scene id and the asset hrefs, because the
scene id + href *is* the evidence for any per-parcel observation derived from it.

Radiometry note (verified 2026-09-26 on tile 43PHK): Earth Search items with
``s2:processing_baseline >= 04.00`` carry ``earthsearch:boa_offset_applied: true`` and their
COG pixels are already harmonised (no +1000 DN offset; e.g. 1st-percentile red DN = 135 on
S2B_43PHK_20211224_1_L2A). Their ``raster:bands`` still advertise ``offset: -0.1``; applying it
would double-correct. Planetary Computer serves the raw ESA product, so baseline >= 04.00
needs the 1000 DN offset removed. :attr:`SceneRecord.dn_offset` records what to subtract.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"

BANDS = ("red", "green", "blue", "nir", "swir16", "scl")
# Planetary Computer uses ESA band names as asset keys.
PC_ASSET_KEYS = {"red": "B04", "green": "B03", "blue": "B02", "nir": "B08", "swir16": "B11", "scl": "SCL"}

# STAC properties kept on every record (provenance + cheap scene-level quality hints).
META_KEYS = ("platform", "s2:product_uri", "s2:nodata_pixel_percentage", "s2:cloud_shadow_percentage",
             "s2:high_proba_clouds_percentage", "s2:medium_proba_clouds_percentage", "s2:thin_cirrus_percentage",
             "s2:degraded_msi_data_percentage", "earthsearch:boa_offset_applied", "view:sun_elevation",
             "s2:generation_time", "sat:relative_orbit", "s2:datatake_id")

# Earth Search ids: S2B_43PHK_20211224_1_L2A  ->  sat, tile, yyyymmdd, suffix
_ES_ID = re.compile(r"^(?P<sat>S2[A-D])_(?P<tile>\d{2}[A-Z]{3})_(?P<date>\d{8})_(?P<n>\d+)_L2A$")


@dataclass(frozen=True)
class SceneRecord:
    id: str
    datetime: datetime
    tile: str
    cloud: float | None
    hrefs: dict[str, str]
    source: str = "earth-search"
    processing_baseline: str | None = None
    dn_offset: int = 0  # DN to subtract before scaling by 1e-4
    epsg: int | None = None
    duplicates: tuple[str, ...] = field(default_factory=tuple)  # collapsed baseline ids
    meta: dict[str, Any] = field(default_factory=dict, compare=False)  # selected STAC properties

    @property
    def date(self) -> date:
        return self.datetime.date()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["datetime"] = self.datetime.isoformat()
        d["duplicates"] = list(self.duplicates)
        return d


def parse_es_id(item_id: str) -> tuple[str, str, str, int] | None:
    """Return (satellite, tile, yyyymmdd, suffix) for an Earth Search id, else None."""
    m = _ES_ID.match(item_id)
    if not m:
        return None
    return m["sat"], m["tile"], m["date"], int(m["n"])


def _baseline_float(b: str | None) -> float:
    try:
        return float(b) if b is not None else 0.0
    except ValueError:
        return 0.0


def _tile_of(item: Any) -> str:
    p = item.properties
    parsed = parse_es_id(item.id)
    if parsed:
        return parsed[1]
    if p.get("s2:mgrs_tile"):
        return str(p["s2:mgrs_tile"])
    grid = str(p.get("grid:code", ""))
    if grid.startswith("MGRS-"):
        return grid[5:]
    m = re.search(r"_T(\d{2}[A-Z]{3})_", item.id)
    return m.group(1) if m else "UNKNOWN"


def _suffix_rank(item: Any) -> tuple[int, float, str]:
    """Rank for choosing among baseline duplicates: highest ``_N`` suffix, then baseline, then id."""
    parsed = parse_es_id(item.id)
    n = parsed[3] if parsed else -1
    return (n, _baseline_float(item.properties.get("s2:processing_baseline")), item.id)


def dedup_baselines(items: Iterable[Any]) -> list[tuple[Any, list[str]]]:
    """Collapse processing-baseline duplicates: same acquisition date and tile -> keep the highest
    ``_N`` suffix (Earth Search) or highest processing baseline (Planetary Computer).

    Returns ``[(kept_item, [dropped ids...]), ...]`` sorted by datetime then tile.
    """
    groups: dict[tuple[str, str], list[Any]] = {}
    for it in items:
        dt = it.datetime or datetime.fromisoformat(str(it.properties["datetime"]))
        groups.setdefault((dt.date().isoformat(), _tile_of(it)), []).append(it)
    out = []
    for key in sorted(groups):
        members = sorted(groups[key], key=_suffix_rank, reverse=True)
        out.append((members[0], [m.id for m in members[1:]]))
    return out


def _epsg_of(item: Any) -> int | None:
    p = item.properties
    for k in ("proj:epsg",):
        if p.get(k):
            return int(p[k])
    code = p.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        return int(code.split(":")[1])
    for a in item.assets.values():
        e = a.extra_fields.get("proj:epsg")
        if e:
            return int(e)
        c = a.extra_fields.get("proj:code")
        if isinstance(c, str) and c.upper().startswith("EPSG:"):
            return int(c.split(":")[1])
    tile = _tile_of(item)
    if re.match(r"^\d{2}[N-X]", tile):  # northern hemisphere MGRS band letter N..X
        return 32600 + int(tile[:2])
    return None


def _to_record(item: Any, source: str, dropped: Sequence[str]) -> SceneRecord:
    p = item.properties
    baseline = p.get("s2:processing_baseline")
    if source == "earth-search":
        hrefs = {b: item.assets[b].href for b in BANDS if b in item.assets}
        applied = p.get("earthsearch:boa_offset_applied")
        dn_offset = 1000 if (_baseline_float(baseline) >= 4.0 and applied is False) else 0
    else:
        hrefs = {b: item.assets[k].href for b, k in PC_ASSET_KEYS.items() if k in item.assets}
        dn_offset = 1000 if _baseline_float(baseline) >= 4.0 else 0
    return SceneRecord(
        id=item.id,
        datetime=item.datetime,
        tile=_tile_of(item),
        cloud=p.get("eo:cloud_cover"),
        hrefs=hrefs,
        source=source,
        processing_baseline=baseline,
        dn_offset=dn_offset,
        epsg=_epsg_of(item),
        duplicates=tuple(dropped),
        meta={k: p[k] for k in META_KEYS if k in p},
    )


def _search(url: str, bbox: Sequence[float], dt_range: str, max_cloud: float | None,
            modifier: Any = None) -> list[Any]:
    import pystac_client

    client = pystac_client.Client.open(url, modifier=modifier)
    kwargs: dict[str, Any] = {"collections": [COLLECTION], "bbox": list(bbox), "datetime": dt_range, "limit": 200}
    if max_cloud is not None:
        kwargs["query"] = {"eo:cloud_cover": {"lt": max_cloud}}
    return list(client.search(**kwargs).items())


def _dt_range(start: date | str, end: date | str) -> str:
    return f"{start}/{end}" if str(start) != str(end) else f"{start}"


def search_scenes(
    bbox: Sequence[float],
    start: date | str,
    end: date | str,
    *,
    max_cloud: float | None = None,
    tiles: Sequence[str] | None = None,
    source: str = "auto",
    dedup: bool = True,
) -> list[SceneRecord]:
    """Search Sentinel-2 L2A scenes intersecting ``bbox`` (lon/lat) between ``start`` and ``end``
    (inclusive ISO dates).

    ``source``: ``"auto"`` (Earth Search, falling back to Planetary Computer on error),
    ``"earth-search"`` or ``"planetary-computer"``. ``max_cloud`` filters on scene cloud cover
    (production leaves it ``None``: SCL decides per pixel). ``tiles`` keeps only given MGRS tiles.
    """
    dt_range = _dt_range(start, end)
    items: list[Any]
    used = source
    if source in ("auto", "earth-search"):
        try:
            items = _search(EARTH_SEARCH_URL, bbox, dt_range, max_cloud)
            used = "earth-search"
        except Exception as exc:  # network / API errors
            if source == "earth-search":
                raise
            log.warning("Earth Search failed (%s); falling back to Planetary Computer", exc)
            items = _search_pc(bbox, dt_range, max_cloud)
            used = "planetary-computer"
    elif source == "planetary-computer":
        items = _search_pc(bbox, dt_range, max_cloud)
    else:
        raise ValueError(f"unknown source {source!r}")

    if tiles:
        wanted = {t.upper() for t in tiles}
        items = [it for it in items if _tile_of(it).upper() in wanted]
    pairs = dedup_baselines(items) if dedup else [(it, []) for it in sorted(items, key=lambda i: i.datetime)]
    return [_to_record(it, used, dropped) for it, dropped in pairs]


def _search_pc(bbox: Sequence[float], dt_range: str, max_cloud: float | None) -> list[Any]:
    import planetary_computer

    return _search(PLANETARY_COMPUTER_URL, bbox, dt_range, max_cloud, modifier=planetary_computer.sign_inplace)


def scene_for_date(bbox: Sequence[float], day: date | str, tile: str, *, source: str = "auto",
                   suffix: int | None = None) -> SceneRecord:
    """Resolve the scene acquired on ``day`` over ``tile``.

    Default: the baseline-deduplicated record (highest ``_N`` suffix). ``suffix`` instead selects a
    specific Earth Search processing-baseline variant (e.g. to reproduce a historical analysis that
    used an older baseline); the other variants are listed in ``duplicates``.
    """
    if suffix is None:
        recs = search_scenes(bbox, day, day, tiles=[tile], source=source)
        if not recs:
            raise LookupError(f"no Sentinel-2 L2A scene for tile {tile} on {day}")
        return recs[0]
    recs = search_scenes(bbox, day, day, tiles=[tile], source=source, dedup=False)
    pick = [r for r in recs if (p := parse_es_id(r.id)) and p[3] == suffix]
    if not pick:
        raise LookupError(f"no baseline variant _{suffix} for tile {tile} on {day}: {[r.id for r in recs]}")
    others = tuple(r.id for r in recs if r.id != pick[0].id)
    return replace(pick[0], duplicates=others)
