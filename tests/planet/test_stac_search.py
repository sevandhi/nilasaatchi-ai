"""Offline tests for planet.stac.search (fake STAC items) + opt-in live tests."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from planet.stac import search as st


def _asset(href: str) -> SimpleNamespace:
    return SimpleNamespace(href=href, extra_fields={})


def es_item(item_id: str, baseline: str = "05.00", cloud: float = 1.0, applied: bool | None = True,
            hour: int = 5) -> SimpleNamespace:
    ymd = item_id.split("_")[2]
    dt = datetime(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]), hour, 26, tzinfo=UTC)
    props = {"s2:processing_baseline": baseline, "eo:cloud_cover": cloud, "grid:code": "MGRS-43PHK",
             "proj:epsg": 32643, "datetime": dt.isoformat()}
    if applied is not None:
        props["earthsearch:boa_offset_applied"] = applied
    assets = {b: _asset(f"https://x/{item_id}/{b}.tif") for b in st.BANDS}
    return SimpleNamespace(id=item_id, datetime=dt, properties=props, assets=assets)


def pc_item(item_id: str, baseline: str, day: str = "20211224") -> SimpleNamespace:
    dt = datetime(int(day[:4]), int(day[4:6]), int(day[6:]), 5, 12, tzinfo=UTC)
    props = {"s2:processing_baseline": baseline, "eo:cloud_cover": 0.6, "s2:mgrs_tile": "43PHK",
             "proj:epsg": 32643}
    assets = {k: _asset(f"https://pc/{item_id}/{k}.tif?sig=1") for k in st.PC_ASSET_KEYS.values()}
    return SimpleNamespace(id=item_id, datetime=dt, properties=props, assets=assets)


def test_parse_es_id():
    assert st.parse_es_id("S2B_43PHK_20211224_2_L2A") == ("S2B", "43PHK", "20211224", 2)
    assert st.parse_es_id("S2B_MSIL2A_20211224T051219_R019_T43PHK_20230128T135148") is None


def test_dedup_keeps_highest_suffix_per_date_and_tile():
    items = [
        es_item("S2B_43PHK_20211224_0_L2A", "03.01", applied=False),
        es_item("S2B_43PHK_20211224_2_L2A"),
        es_item("S2B_43PHK_20211224_1_L2A"),
        es_item("S2A_43PHK_20211229_0_L2A", "03.01", applied=False),
        es_item("S2A_43PHK_20211229_1_L2A"),
        es_item("S2B_43PHK_20190109_0_L2A", "02.11", applied=False),
        es_item("S2B_43PHL_20211224_0_L2A"),  # other tile, same date -> separate group
    ]
    out = st.dedup_baselines(items)
    kept = [it.id for it, _ in out]
    assert kept == ["S2B_43PHK_20190109_0_L2A", "S2B_43PHK_20211224_2_L2A", "S2B_43PHL_20211224_0_L2A",
                    "S2A_43PHK_20211229_1_L2A"]
    dropped = {it.id: d for it, d in out}
    assert set(dropped["S2B_43PHK_20211224_2_L2A"]) == {"S2B_43PHK_20211224_0_L2A", "S2B_43PHK_20211224_1_L2A"}


def test_dedup_suffix_10_beats_9():
    out = st.dedup_baselines([es_item("S2B_43PHK_20211224_9_L2A"), es_item("S2B_43PHK_20211224_10_L2A")])
    assert [it.id for it, _ in out] == ["S2B_43PHK_20211224_10_L2A"]


def test_dedup_planetary_computer_by_baseline():
    out = st.dedup_baselines([pc_item("S2B_MSIL2A_20211224T051219_R019_T43PHK_20211224T205113", "03.01"),
                              pc_item("S2B_MSIL2A_20211224T051219_R019_T43PHK_20230128T135148", "04.00")])
    assert len(out) == 1 and out[0][0].id.endswith("20230128T135148")


def test_record_dn_offset_rules():
    new = st._to_record(es_item("S2C_43PHK_20250711_0_L2A", "05.11", applied=True), "earth-search", [])
    old = st._to_record(es_item("S2B_43PHK_20190109_0_L2A", "02.11", applied=False), "earth-search", [])
    raw = st._to_record(es_item("S2C_43PHK_20250711_0_L2A", "05.11", applied=False), "earth-search", [])
    pc_new = st._to_record(pc_item("X_T43PHK_1", "05.11"), "planetary-computer", [])
    pc_old = st._to_record(pc_item("X_T43PHK_0", "03.01"), "planetary-computer", [])
    assert (new.dn_offset, old.dn_offset, raw.dn_offset, pc_new.dn_offset, pc_old.dn_offset) == (0, 0, 1000, 1000, 0)
    assert set(new.hrefs) == set(st.BANDS) and set(pc_new.hrefs) == set(st.BANDS)
    assert pc_new.hrefs["red"].endswith("B04.tif?sig=1")
    assert new.tile == "43PHK" and new.epsg == 32643 and new.to_dict()["datetime"].startswith("2025-07-11")


def test_search_falls_back_to_planetary_computer(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("earth search down")

    monkeypatch.setattr(st, "_search", boom)
    monkeypatch.setattr(st, "_search_pc", lambda *a, **k: [pc_item("S2B_MSIL2A_X_T43PHK_Y", "05.11", "20251223")])
    recs = st.search_scenes((78.0, 8.7, 78.1, 8.8), "2025-12-23", "2025-12-23")
    assert len(recs) == 1 and recs[0].source == "planetary-computer" and recs[0].dn_offset == 1000
    with pytest.raises(ConnectionError):
        st.search_scenes((78.0, 8.7, 78.1, 8.8), "2025-12-23", "2025-12-23", source="earth-search")


def test_scene_for_date_suffix_and_tile_filter(monkeypatch):
    items = [es_item("S2B_43PHK_20210528_0_L2A", "03.00", applied=False), es_item("S2B_43PHK_20210528_1_L2A"),
             es_item("S2B_43PHL_20210528_3_L2A")]
    monkeypatch.setattr(st, "_search", lambda *a, **k: list(items))
    assert st.scene_for_date((0, 0, 1, 1), "2021-05-28", "43PHK").id == "S2B_43PHK_20210528_1_L2A"
    pinned = st.scene_for_date((0, 0, 1, 1), "2021-05-28", "43PHK", suffix=0)
    assert pinned.id == "S2B_43PHK_20210528_0_L2A" and pinned.duplicates == ("S2B_43PHK_20210528_1_L2A",)
    with pytest.raises(LookupError):
        st.scene_for_date((0, 0, 1, 1), "2021-05-28", "43PHK", suffix=5)


live = pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to call real STAC APIs")
AOI = (77.985, 8.755, 78.045, 8.815)


@pytest.mark.live
@live
def test_live_earth_search_dedups_20211224():
    rec = st.scene_for_date(AOI, "2021-12-24", "43PHK", source="earth-search")
    assert rec.id == "S2B_43PHK_20211224_2_L2A" and len(rec.duplicates) == 2 and rec.dn_offset == 0


@pytest.mark.live
@live
def test_live_planetary_computer_mirror():
    recs = st.search_scenes(AOI, "2025-12-23", "2025-12-23", source="planetary-computer", tiles=["43PHK"])
    assert len(recs) == 1 and "sig=" in recs[0].hrefs["red"] and recs[0].dn_offset == 1000
