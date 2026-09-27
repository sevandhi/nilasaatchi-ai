"""Load all 12 GeoJSON layers into PostGIS (``make load-gis``).

Idempotent: the whole load runs in one transaction.
- village / parcel / survey are upserted by key (parcel_uid, (village_id, survey_no)), and
  rows that disappeared from the source are deleted. Parcels are never truncated, because
  parcel_obs / parcel_season reference them.
- ref_layer_* tables are truncated and reloaded.

Geometry hygiene happens in SQL: ST_Force2D -> ST_MakeValid -> ST_CollectionExtract -> ST_Multi.
Reported areas are geodesic (ST_Area on geography, D-023). Distances, buffers and overlays use
EPSG:32644 via the generated ``geom_utm`` columns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from shapely.geometry import shape

from pipeline.gis.aliases import load_aliases, normalise_kide, split_kide
from pipeline.gis.views import apply_views

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEO_DIR = ROOT / "Dataset" / "Geospatial_Layer"

# Road classes (OSM fclass). Major = trunk/primary/secondary (+ links, motorway) or ref NH/SH/MDR.
MAJOR_FCLASS = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                "secondary", "secondary_link")
NON_VEHICULAR_FCLASS = ("footway", "path", "steps", "pedestrian")
MAJOR_REF_REGEX = r"^\s*(NH|SH|MDR)"


@dataclass(frozen=True)
class Layer:
    key: str
    path: str               # relative to GEO_DIR
    table: str
    kind: str               # parcel | survey | polygon | line | point
    name_field: str | None = None


LAYERS: tuple[Layer, ...] = (
    Layer("fmb", "Park_fmb_Map.geojson", "parcel", "parcel"),
    Layer("cadastral", "Park_Cadastral_Map.geojson", "survey", "survey"),
    Layer("park_boundary", "Park_Boundary.geojson", "ref_layer_park_boundary", "polygon", "park_name"),
    Layer("sipcot_parks", "Thoothukudi_Parks.geojson", "ref_layer_sipcot_parks", "point", "park_name"),
    Layer("roads", "Basic_GIS_Layers/Road_network.geojson", "ref_layer_roads", "line", "name"),
    Layer("rail", "Basic_GIS_Layers/Railway_network.geojson", "ref_layer_rail", "line", "name"),
    Layer("rail_stations", "Basic_GIS_Layers/Railway_Stations.geojson", "ref_layer_rail_stations", "point", "name"),
    Layer("waterbodies", "Basic_GIS_Layers/Waterbodies.geojson", "ref_layer_waterbodies", "polygon", "Tank_name"),
    Layer("substations", "Basic_GIS_Layers/SubStations.geojson", "ref_layer_substations", "polygon", "name"),
    Layer("airport", "Basic_GIS_Layers/Airport.geojson", "ref_layer_airport", "polygon", "name"),
    Layer("seaport", "Basic_GIS_Layers/Seaport.geojson", "ref_layer_seaport", "point", "name"),
    Layer("schools", "Basic_GIS_Layers/Educational_Institution.geojson", "ref_layer_schools", "point", "name"),
)

GEOM_SQL = {
    "polygon": "ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(gj), 4326))), 3))",
    "line": "ST_Multi(ST_CollectionExtract(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(gj), 4326)), 2))",
    "point": "ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(gj), 4326))",
}
GEOM_SQL["parcel"] = GEOM_SQL["survey"] = GEOM_SQL["polygon"]


# ------------------------------------------------------------------ helpers
def _clean_json(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, dict):
        return {k: _clean_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_json(x) for x in v]
    return v


def _int_or_none(v: Any) -> int | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return int(float(v))


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_features(p: pathlib.Path) -> tuple[list[dict], dict]:
    """Return (features, stats) with geometry hygiene counters measured in shapely."""
    data = json.loads(p.read_text(encoding="utf-8"))
    feats = data["features"]
    n_z = n_invalid = n_null = 0
    for f in feats:
        if f.get("geometry") is None:
            n_null += 1
            continue
        g = shape(f["geometry"])
        n_z += bool(g.has_z)
        if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_valid:
            n_invalid += 1
    return feats, {"n_z": n_z, "n_invalid": n_invalid, "n_null_geom": n_null,
                   "crs": (data.get("crs") or {}).get("properties", {}).get("name")}


def fmb_rows(feats: list[dict]) -> tuple[list[tuple], dict]:
    """Normalise FMB features to parcel rows; returns (rows, anomalies)."""
    aliases = load_aliases()
    rows, anomalies = [], {"sub_div_differs_from_kide": [], "survey_no_differs_from_kide": [],
                           "null_block_id": [], "district_variants": Counter(), "unknown_village": []}
    for f in feats:
        p = _clean_json(f["properties"])
        canon = aliases.canonical_village(p.get("vil_name"))
        if canon is None:
            anomalies["unknown_village"].append(p.get("vil_name"))
            continue
        anomalies["district_variants"][f'{p.get("dist_name")} -> {aliases.canonical_district(p.get("dist_name"))}'] += 1
        kide = normalise_kide(p["KIDE"])
        survey, sub = split_kide(kide)
        raw_sub = None if p.get("sub_div") in (None, "") else normalise_kide(p["sub_div"])
        uid = f"{canon}|{kide}"
        if raw_sub != sub:
            anomalies["sub_div_differs_from_kide"].append({"parcel_uid": uid, "sub_div": p.get("sub_div")})
        if str(p.get("survey_no")) != survey:
            anomalies["survey_no_differs_from_kide"].append({"parcel_uid": uid, "survey_no": p.get("survey_no")})
        if p.get("block_id") is None:
            anomalies["null_block_id"].append(uid)
        rows.append((uid, kide, canon, survey, sub, _int_or_none(p.get("unit_id")),
                     _int_or_none(p.get("block_id")), None if p.get("Land_id") is None else str(p["Land_id"]),
                     Jsonb(p), json.dumps(f["geometry"])))
    anomalies["district_variants"] = dict(anomalies["district_variants"])
    return rows, anomalies


def cadastral_rows(feats: list[dict]) -> tuple[list[tuple], dict]:
    aliases = load_aliases()
    rows, anomalies = [], {"unknown_village": [], "district_variants": Counter()}
    for f in feats:
        p = _clean_json(f["properties"])
        canon = aliases.canonical_village(p.get("vil_name"))
        if canon is None:
            anomalies["unknown_village"].append(p.get("vil_name"))
            continue
        anomalies["district_variants"][f'{p.get("dist_name")} -> {aliases.canonical_district(p.get("dist_name"))}'] += 1
        rows.append((canon, str(p["survey_no"]).strip(), _int_or_none(p.get("unit_id")),
                     _int_or_none(p.get("block_id")), Jsonb(p), json.dumps(f["geometry"])))
    anomalies["district_variants"] = dict(anomalies["district_variants"])
    return rows, anomalies


# ------------------------------------------------------------------ SQL steps
def upsert_villages(conn: psycopg.Connection) -> None:
    for v in load_aliases().villages:
        conn.execute(
            """INSERT INTO village (name, name_ta, aliases) VALUES (%s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET name_ta = EXCLUDED.name_ta, aliases = EXCLUDED.aliases""",
            (v.name, v.name_ta, list(v.aliases)),
        )


def load_parcels(conn: psycopg.Connection, rows: list[tuple]) -> None:
    conn.execute("""CREATE TEMP TABLE stg_parcel (parcel_uid text, kide text, village text, survey_no text,
                    sub_div text, unit_id int, block_id int, land_id text, raw jsonb, gj text) ON COMMIT DROP""")
    with conn.cursor().copy("COPY stg_parcel FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    dup = conn.execute("SELECT parcel_uid, count(*) FROM stg_parcel GROUP BY 1 HAVING count(*) > 1").fetchall()
    if dup:
        raise RuntimeError(f"duplicate parcel_uid in FMB source: {dup[:10]}")
    conn.execute(f"""
        INSERT INTO parcel (parcel_uid, kide, village_id, survey_no, sub_div, unit_id, block_id, land_id, raw, geom,
                            loaded_at)
        SELECT s.parcel_uid, s.kide, v.id, s.survey_no, s.sub_div, s.unit_id, s.block_id, s.land_id, s.raw,
               {GEOM_SQL['parcel']}, now()
        FROM stg_parcel s JOIN village v ON v.name = s.village
        ON CONFLICT (parcel_uid) DO UPDATE SET
            kide = EXCLUDED.kide, village_id = EXCLUDED.village_id, survey_no = EXCLUDED.survey_no,
            sub_div = EXCLUDED.sub_div, unit_id = EXCLUDED.unit_id, block_id = EXCLUDED.block_id,
            land_id = EXCLUDED.land_id, raw = EXCLUDED.raw, geom = EXCLUDED.geom, loaded_at = now()""")
    conn.execute("DELETE FROM parcel WHERE parcel_uid NOT IN (SELECT parcel_uid FROM stg_parcel)")
    conn.execute("UPDATE parcel SET area_ha_gis = round((ST_Area(geom::geography) / 1e4)::numeric, 4)")


def load_surveys(conn: psycopg.Connection, rows: list[tuple]) -> None:
    conn.execute("""CREATE TEMP TABLE stg_survey (village text, survey_no text, unit_id int, block_id int,
                    raw jsonb, gj text) ON COMMIT DROP""")
    with conn.cursor().copy("COPY stg_survey FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    dup = conn.execute("SELECT village, survey_no FROM stg_survey GROUP BY 1, 2 HAVING count(*) > 1").fetchall()
    if dup:
        raise RuntimeError(f"duplicate (village, survey_no) in cadastral source: {dup[:10]}")
    conn.execute(f"""
        INSERT INTO survey (village_id, survey_no, unit_id, block_id, raw, geom, loaded_at)
        SELECT v.id, s.survey_no, s.unit_id, s.block_id, s.raw, {GEOM_SQL['survey']}, now()
        FROM stg_survey s JOIN village v ON v.name = s.village
        ON CONFLICT (village_id, survey_no) DO UPDATE SET
            unit_id = EXCLUDED.unit_id, block_id = EXCLUDED.block_id, raw = EXCLUDED.raw,
            geom = EXCLUDED.geom, loaded_at = now()""")
    conn.execute("""DELETE FROM survey s WHERE NOT EXISTS (
                        SELECT 1 FROM stg_survey t JOIN village v ON v.name = t.village
                        WHERE v.id = s.village_id AND t.survey_no = s.survey_no)""")
    conn.execute("UPDATE survey SET area_ha_gis = round((ST_Area(geom::geography) / 1e4)::numeric, 4)")


def load_ref_layer(conn: psycopg.Connection, layer: Layer, feats: list[dict]) -> None:
    stg = f"stg_{layer.key}"
    conn.execute(f"CREATE TEMP TABLE {stg} (raw jsonb, gj text) ON COMMIT DROP")
    with conn.cursor().copy(f"COPY {stg} FROM STDIN") as cp:
        for f in feats:
            if f.get("geometry") is None:
                continue
            cp.write_row((Jsonb(_clean_json(f["properties"] or {})), json.dumps(f["geometry"])))
    conn.execute(f"TRUNCATE {layer.table} RESTART IDENTITY")
    name = f"raw->>'{layer.name_field}'" if layer.name_field else "NULL"
    geom = GEOM_SQL[layer.kind]
    if layer.key == "roads":
        conn.execute(f"""
            INSERT INTO ref_layer_roads (osm_id, fclass, name, ref, is_major, is_vehicular, raw, geom)
            SELECT raw->>'osm_id', raw->>'fclass', raw->>'name', raw->>'ref',
                   (raw->>'fclass') = ANY(%s) OR coalesce(upper(raw->>'ref'), '') ~ %s,
                   NOT ((raw->>'fclass') = ANY(%s)),
                   raw, {geom}
            FROM {stg}""", (list(MAJOR_FCLASS), MAJOR_REF_REGEX, list(NON_VEHICULAR_FCLASS)))
    elif layer.key == "substations":
        conn.execute(f"""INSERT INTO ref_layer_substations (name, voltage, raw, geom)
                         SELECT {name}, raw->>'voltage', raw, {geom} FROM {stg}""")
    else:
        conn.execute(f"INSERT INTO {layer.table} (name, raw, geom) SELECT {name}, raw, {geom} FROM {stg}")
    if layer.key == "park_boundary":
        conn.execute("UPDATE ref_layer_park_boundary SET area_ha_gis = round((ST_Area(geom::geography) / 1e4)::numeric, 4)")


# Overlays run in EPSG:32644; resulting areas are reported geodesically via geo_area_ha()
# (defined in db/views/000_functions.sql, D-023).
DERIVED_SQL = """
WITH b AS (SELECT ST_Union(geom_utm) AS g FROM ref_layer_park_boundary)
UPDATE parcel p SET
    dist_major_road_m = (SELECT round(ST_Distance(p.geom_utm, r.geom_utm)::numeric, 1)
                         FROM ref_layer_roads r WHERE r.is_major ORDER BY r.geom_utm <-> p.geom_utm LIMIT 1),
    dist_any_road_m   = (SELECT round(ST_Distance(p.geom_utm, r.geom_utm)::numeric, 1)
                         FROM ref_layer_roads r WHERE r.is_vehicular ORDER BY r.geom_utm <-> p.geom_utm LIMIT 1),
    dist_substation_m = (SELECT round(ST_Distance(p.geom_utm, s.geom_utm)::numeric, 1)
                         FROM ref_layer_substations s ORDER BY s.geom_utm <-> p.geom_utm LIMIT 1),
    dist_rail_station_m = (SELECT round(ST_Distance(p.geom_utm, s.geom_utm)::numeric, 1)
                           FROM ref_layer_rail_stations s ORDER BY s.geom_utm <-> p.geom_utm LIMIT 1),
    intersects_waterbody = EXISTS (SELECT 1 FROM ref_layer_waterbodies w WHERE ST_Intersects(w.geom_utm, p.geom_utm)),
    waterbody_overlap_ha = round((coalesce((SELECT sum(geo_area_ha(ST_Intersection(w.geom_utm, p.geom_utm)))
                                            FROM ref_layer_waterbodies w
                                            WHERE ST_Intersects(w.geom_utm, p.geom_utm)), 0))::numeric, 4),
    area_outside_boundary_ha = round(geo_area_ha(ST_Difference(p.geom_utm, b.g))::numeric, 4),
    inside_park_boundary = ST_Intersects(b.g, ST_PointOnSurface(p.geom_utm))
FROM b
"""


def record_layer(conn: psycopg.Connection, layer: Layer, path: pathlib.Path, n: int, stats: dict,
                 notes: dict) -> None:
    conn.execute(
        """INSERT INTO gis_layer_load (layer, target_table, source_path, sha256, n_features, n_z_stripped,
                                       n_invalid_fixed, notes, loaded_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (layer) DO UPDATE SET target_table = EXCLUDED.target_table,
               source_path = EXCLUDED.source_path, sha256 = EXCLUDED.sha256, n_features = EXCLUDED.n_features,
               n_z_stripped = EXCLUDED.n_z_stripped, n_invalid_fixed = EXCLUDED.n_invalid_fixed,
               notes = EXCLUDED.notes, loaded_at = now()""",
        (layer.key, layer.table, str(path.relative_to(ROOT)), sha256_file(path), n, stats["n_z"],
         stats["n_invalid"], Jsonb({**notes, "source_crs": stats["crs"], "n_null_geom": stats["n_null_geom"]})),
    )


def run(dsn: str, geo_dir: pathlib.Path = GEO_DIR) -> dict[str, int]:
    t0 = time.time()
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            upsert_villages(conn)
            for layer in LAYERS:
                path = geo_dir / layer.path
                feats, stats = read_features(path)
                notes: dict = {}
                if layer.kind == "parcel":
                    rows, notes = fmb_rows(feats)
                    if notes["unknown_village"]:
                        raise RuntimeError(f"FMB has unknown villages: {set(notes['unknown_village'])}")
                    load_parcels(conn, rows)
                elif layer.kind == "survey":
                    rows, notes = cadastral_rows(feats)
                    if notes["unknown_village"]:
                        raise RuntimeError(f"cadastral has unknown villages: {set(notes['unknown_village'])}")
                    load_surveys(conn, rows)
                else:
                    load_ref_layer(conn, layer, feats)
                counts[layer.table] = conn.execute(f"SELECT count(*) FROM {layer.table}").fetchone()[0]
                record_layer(conn, layer, path, len(feats), stats, notes)
                print(f"  {layer.key:<14} -> {layer.table:<26} {counts[layer.table]:>6} rows"
                      f"  (z-stripped {stats['n_z']}, invalid fixed {stats['n_invalid']})")
            apply_views(conn)          # also defines geo_area_ha() used below and builds fmb_qa
            conn.execute(DERIVED_SQL)
        counts["village"] = conn.execute("SELECT count(*) FROM village").fetchone()[0]
    print(f"load-gis done in {time.time() - t0:.1f}s")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--geo-dir", type=pathlib.Path, default=GEO_DIR)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    run(os.environ["DATABASE_URL"], args.geo_dir)


if __name__ == "__main__":
    main()
