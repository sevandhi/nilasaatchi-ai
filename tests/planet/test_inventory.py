"""Offline tests for planet.stac.inventory: row building, column mapping, upsert SQL, SCL stats, summaries."""
from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from planet import aoi
from planet.stac import inventory as inv
from planet.stac.search import SceneRecord

COLS_0004 = {"id": "text", "datetime": "timestamp with time zone", "tile": "text", "cloud": "real",
             "baseline": "text", "hrefs": "jsonb", "aoi_cache_path": "text", "source": "text",
             "dn_offset": "integer", "epsg": "integer", "duplicates": "ARRAY", "aoi_valid_frac": "real",
             "aoi_cloud_frac": "real", "meta": "jsonb"}
COLS_0003 = {k: v for k, v in COLS_0004.items() if k not in ("aoi_valid_frac", "aoi_cloud_frac", "meta")}


def rec(sid="S2B_43PHK_20211224_2_L2A", day=(2021, 12, 24), cloud=0.6, source="earth-search", dups=(),
        dn_offset=0, query=""):
    return SceneRecord(id=sid, datetime=datetime(*day, 5, 26, tzinfo=UTC), tile="43PHK", cloud=cloud,
                       hrefs={b: f"https://x/{sid}/{b}.tif{query}" for b in ("red", "nir", "scl")},
                       source=source, processing_baseline="05.00", dn_offset=dn_offset, epsg=32643,
                       duplicates=tuple(dups), meta={"platform": "sentinel-2b"})


USABLE = {"aoi_valid_frac": 0.91, "aoi_cloud_frac": 0.02, "aoi_nodata_frac": 0.0, "n_parcels_ge10": 990,
          "aoi_cache_path": "data/s2/x/aoi_k.tif"}


# ---------------------------------------------------------------- rows + mapping
def test_storage_href_strips_sas_token():
    assert inv.storage_href("https://pc/a/B04.tif?st=1&sig=abc") == "https://pc/a/B04.tif"
    assert inv.storage_href("https://x/a/B04.tif") == "https://x/a/B04.tif"


def test_scene_row_fields():
    r = inv.scene_row(rec(dups=("S2B_43PHK_20211224_1_L2A", "S2B_43PHK_20211224_0_L2A")), USABLE)
    assert r["id"] == "S2B_43PHK_20211224_2_L2A" and r["date"] == date(2021, 12, 24)
    assert r["collapsed_ids"] == ["S2B_43PHK_20211224_1_L2A", "S2B_43PHK_20211224_0_L2A"]
    assert r["aoi_valid_frac"] == 0.91 and r["aoi_cloud_frac"] == 0.02
    assert r["meta"]["aoi"]["n_parcels_ge10"] == 990 and "aoi_cache_path" not in r["meta"]["aoi"]
    assert r["meta"]["stac"] == {"platform": "sentinel-2b"}


def test_scene_row_planetary_computer_unsigned():
    r = inv.scene_row(rec(source="planetary-computer", dn_offset=1000, query="?sig=secret"))
    assert all("?" not in h for h in r["hrefs"].values())
    assert r["dn_offset"] == 1000 and r["meta"]["href_signing"] == "planetary_computer.sign"
    assert "aoi_valid_frac" not in r


def test_map_row_0004_columns():
    m = inv.map_row(inv.scene_row(rec(dups=("S2B_43PHK_20211224_1_L2A",)), USABLE), COLS_0004)
    assert m["duplicates"] == ["S2B_43PHK_20211224_1_L2A"] and "collapsed_ids" not in m
    assert m["aoi_valid_frac"] == 0.91 and m["aoi_cloud_frac"] == 0.02
    assert set(m["hrefs"]) == {"red", "nir", "scl"}  # hrefs = evidence only
    assert m["meta"]["aoi"]["aoi_valid_frac"] == 0.91
    assert "date" not in m and "date" not in m["meta"]


def test_map_row_without_meta_column_never_touches_hrefs():
    m = inv.map_row(inv.scene_row(rec(), USABLE), COLS_0003)
    assert set(m["hrefs"]) == {"red", "nir", "scl"}
    assert "aoi_valid_frac" not in m and "meta" not in m


def test_map_row_requires_id():
    with pytest.raises(RuntimeError):
        inv.map_row({"id": "x"}, {"tile": "text"})


def test_upsert_sql():
    sql = inv.upsert_sql(["id", "cloud", "hrefs"])
    assert sql.startswith("INSERT INTO s2_scene (id, cloud, hrefs) VALUES (%s, %s, %s)")
    assert "ON CONFLICT (id) DO UPDATE SET cloud = EXCLUDED.cloud, hrefs = EXCLUDED.hrefs" in sql
    assert "id = EXCLUDED.id" not in sql


# ---------------------------------------------------------------- upsert against a fake connection
class FakeCursor:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount = rows or [], rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Tiny in-memory stand-in: stores upserted rows by id, applies DELETE ... ANY."""

    def __init__(self, columns, fail_delete=False):
        self.columns, self.fail_delete = columns, fail_delete
        self.table: dict[str, dict] = {}
        self.statements: list[str] = []

    @contextlib.contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if sql.startswith("SELECT column_name"):
            return FakeCursor(list(self.columns.items()))
        if sql.startswith("INSERT INTO s2_scene"):
            cols = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
            row = dict(zip(cols, params))
            self.table[row["id"]] = row
            return FakeCursor(rowcount=1)
        if sql.startswith("DELETE FROM s2_scene"):
            if self.fail_delete:
                raise RuntimeError("violates foreign key constraint parcel_obs_scene_id_fkey")
            gone = [i for i in params[0] if self.table.pop(i, None) is not None]
            return FakeCursor(rowcount=len(gone))
        raise AssertionError(sql)


def test_upsert_idempotent_and_replaces_superseded_baseline():
    conn = FakeConn(COLS_0004)
    old = inv.scene_row(rec(sid="S2B_43PHK_20211224_1_L2A"), USABLE)
    assert inv.upsert(conn, [old])["upserted"] == 1
    # a newer baseline appears: _2 kept, _1 collapsed -> the _1 row is removed
    new = inv.scene_row(rec(dups=("S2B_43PHK_20211224_1_L2A",)), USABLE)
    res = inv.upsert(conn, [new])
    assert res == {"upserted": 1, "removed_superseded": 1, "columns": len(COLS_0004)}
    assert list(conn.table) == ["S2B_43PHK_20211224_2_L2A"]
    first = dict(conn.table)
    inv.upsert(conn, [new])  # re-run: same state
    assert list(conn.table) == list(first)
    row = conn.table["S2B_43PHK_20211224_2_L2A"]
    assert row["duplicates"] == ["S2B_43PHK_20211224_1_L2A"]
    assert row["hrefs"].obj == new["hrefs"]  # Jsonb wrapper
    assert row["aoi_valid_frac"] == 0.91


def test_upsert_keeps_going_when_superseded_row_is_referenced():
    conn = FakeConn(COLS_0004, fail_delete=True)
    res = inv.upsert(conn, [inv.scene_row(rec(dups=("S2B_43PHK_20211224_1_L2A",)))])
    assert res["upserted"] == 1 and res["removed_superseded"] == 0


def test_upsert_missing_table():
    with pytest.raises(RuntimeError, match="make migrate"):
        inv.upsert(FakeConn({}), [inv.scene_row(rec())])


# ---------------------------------------------------------------- SCL usability
def test_scl_stats():
    scl = np.array([[4, 4, 5, 8],
                    [9, 3, 0, 10],
                    [6, 7, 4, 4]], dtype="uint16")
    park = np.array([[1, 1, 1, 1],
                     [1, 1, 1, 1],
                     [0, 0, 0, 0]], dtype=bool)
    labels = np.array([[1, 1, 2, 2],
                       [1, 1, 2, 2],
                       [3, 3, 0, 0]], dtype="int32")
    s, n_valid = inv.scl_stats(scl, park, labels, 3)
    assert s["aoi_px"] == 8
    assert s["aoi_valid_frac"] == pytest.approx(3 / 8)  # 4,4,5
    assert s["aoi_cloud_frac"] == pytest.approx(2 / 8)  # 8,9
    assert s["aoi_cirrus_frac"] == pytest.approx(1 / 8)
    assert s["aoi_shadow_frac"] == pytest.approx(1 / 8)
    assert s["aoi_nodata_frac"] == pytest.approx(1 / 8)
    assert n_valid.tolist() == [0, 2, 1, 2]  # background not counted; pid1 (4,4), pid2 (5), pid3 (6,7)
    assert s["parcels_valid_frac"] == pytest.approx(5 / 10)
    assert s["n_parcels_ge10"] == 0


def test_scl_stats_empty_park():
    s, _ = inv.scl_stats(np.zeros((2, 2), "uint16"), np.zeros((2, 2), bool), np.zeros((2, 2), "int32"), 0)
    assert s["aoi_valid_frac"] == 0.0 and s["aoi_px"] == 0


# ---------------------------------------------------------------- summaries
def test_cloud_bucket_edges():
    assert inv.cloud_bucket(0) == "0-10%" and inv.cloud_bucket(10) == "10-20%"
    assert inv.cloud_bucket(100) == "80-100%" and inv.cloud_bucket(None) == "unknown"


def test_summarise():
    rows = [inv.scene_row(rec(sid=f"S2B_43PHK_2021{m:02d}01_0_L2A", day=(2021, m, 1), cloud=c,
                              dups=("x",) if m == 1 else ()), {"aoi_valid_frac": v})
            for m, c, v in ((1, 5, 0.9), (1, 15, 0.7), (2, 60, 0.1))]
    rows.append(inv.scene_row(rec(sid="S2B_43PHK_20220301_0_L2A", day=(2022, 3, 1), cloud=90)))
    s = inv.summarise(rows)
    assert s["total"] == 4 and s["raw_items"] == 6 and s["collapsed"] == 2
    assert s["by_year"] == {2021: 3, 2022: 1} and s["by_month"][2021][:3] == [2, 1, 0]
    assert s["cloud_lt20"] == 2 and s["cloud_buckets"]["80-100%"] == 1
    assert s["aoi_valid_scored"] == 3 and s["aoi_valid_ge"][">=0.6"] == 2 and s["aoi_valid_ge"][">=0.8"] == 1
    assert s["year_valid"][2021] == {"total": 3, ">=0.6": 2, ">=0.8": 1}


def test_obs_per_parcel_counts_and_gaps():
    mat = pd.DataFrame({
        "parcel_uid": ["A|1"] * 4 + ["B|2"] * 4,
        "scene_id": list("abcd") * 2,
        "date": ["2021-01-01", "2021-01-11", "2021-03-02", "2021-03-12"] * 2,
        "n_total": [40] * 4 + [8] * 4,
        "n_valid": [40, 5, 40, 20, 8, 8, 8, 8],
    })
    o = inv.obs_per_parcel(mat)
    assert o.loc["A|1", "n_obs"] == 3 and o.loc["A|1", "max_gap_days"] == 60  # Jan 1 -> Mar 2
    assert o.loc["B|2", "n_obs"] == 0 and o.loc["B|2", "max_gap_days"] == 70  # whole span
    o8 = inv.obs_per_parcel(mat, min_frac=0.8)
    assert o8.loc["A|1", "n_obs"] == 2  # 20/40 fails the 0.8 criterion


# ---------------------------------------------------------------- parcel keys
def test_parcel_uid_canonical():
    assert aoi.parcel_uid("Allikulam", "168") == "Allikulam|168"
    assert aoi.parcel_uid(" Mela Thattaparai ", " 233/1 ") == "Melathattaparai|233/1"
    assert aoi.parcel_uid("perurani", "1") == "Peroorani|1"
    with pytest.raises(ValueError):
        aoi.canonical_village("Tuticorin")
