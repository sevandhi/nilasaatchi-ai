"""Shared layer registry + GeoJSON builder for `GET /layers/{name}.geojson` (routers/layers.py)
and the workspace GeoJSON export (app/export/geojson.py) — one code path, so a saved workspace's
layer list always reproduces the same features it showed on the map.

Table/column names below are a fixed allow-list (plan.md §5.10); `name` is validated against this
registry before it ever reaches SQL, so there is no injection surface even though the query is
built by string formatting.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg


def _jsonable(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v

LAYER_REGISTRY: dict[str, dict[str, Any]] = {
    "parcel": {"table": "parcel", "geom": "geom", "columns": [
        "parcel_uid", "kide", "survey_no", "sub_div", "unit_id", "block_id", "area_ha_gis",
        "inside_park_boundary", "dist_major_road_m", "dist_any_road_m", "dist_substation_m",
        "dist_rail_station_m", "intersects_waterbody"]},
    "survey": {"table": "survey", "geom": "geom", "columns": [
        "village_id", "survey_no", "unit_id", "block_id", "area_ha_gis"]},
    "ref_layer_roads": {"table": "ref_layer_roads", "geom": "geom", "columns": [
        "name", "ref", "fclass", "is_major", "is_vehicular"]},
    "ref_layer_rail": {"table": "ref_layer_rail", "geom": "geom", "columns": ["name"]},
    "ref_layer_rail_stations": {"table": "ref_layer_rail_stations", "geom": "geom", "columns": ["name"]},
    "ref_layer_waterbodies": {"table": "ref_layer_waterbodies", "geom": "geom", "columns": ["name"]},
    "ref_layer_substations": {"table": "ref_layer_substations", "geom": "geom", "columns": ["name", "voltage"]},
    "ref_layer_airport": {"table": "ref_layer_airport", "geom": "geom", "columns": ["name"]},
    "ref_layer_seaport": {"table": "ref_layer_seaport", "geom": "geom", "columns": ["name"]},
    "ref_layer_schools": {"table": "ref_layer_schools", "geom": "geom", "columns": ["name"]},
    "ref_layer_sipcot_parks": {"table": "ref_layer_sipcot_parks", "geom": "geom", "columns": ["name"]},
    "ref_layer_park_boundary": {"table": "ref_layer_park_boundary", "geom": "geom",
                               "columns": ["name", "area_ha_gis"]},
    "controls": {"table": "control_cell", "geom": "geom", "columns": [
        "cell_id", "sector", "dist_km", "cell_version"]},
}
# fmb_qa (db/views/040_fmb_qa.sql) has no geometry of its own; it is joined to `parcel` on
# parcel_uid so overlap/outside-survey findings can be drawn on the map (D-023).
FMB_QA_COLUMNS = ["issue", "parcel_uid", "other_parcel_uid", "area_ha", "cross_village"]
# Single-issue views of fmb_qa (ui-spec.md: separate toggle-able layers, not one combined layer).
FMB_OVERLAPS_COLUMNS = ["uid_a", "uid_b", "overlap_ha", "cross_village"]
OUTSIDE_SURVEY_COLUMNS = ["uid", "village", "survey_no", "outside_ha", "outside_pct"]

# Parcel properties added on top of LAYER_REGISTRY["parcel"]["columns"] (ui-spec.md: "parcel
# properties enriched with stage, finding counts, the season state ... and the DiD signal").
PARCEL_ENRICH_COLUMNS = ["current_stage", "evidence_level", "stalled_flag", "n_findings"]

LAYER_NAMES = sorted({*LAYER_REGISTRY, "fmb_qa", "fmb_overlaps", "outside_survey"})
DEFAULT_LIMIT = 5000


def simplify_tolerance(zoom: int | None) -> float:
    """Degrees for ST_SimplifyPreserveTopology; 0 = no simplification (full fidelity, used for
    reproducible exports). Tuned so zoom 10 ~ 0.0002 deg (~20 m), zoom 16 ~ 0.000003 deg (~0.3 m)."""
    if zoom is None:
        return 0.0
    z = max(0, min(int(zoom), 20))
    return round(0.2 / (2**z), 8)


_FMB_ISSUE_LAYERS = {
    # name -> (source table/view, join column on parcel = join column on the view, its own columns)
    "fmb_overlaps": ("fmb_qa_overlap", "uid_a", FMB_OVERLAPS_COLUMNS),
    "outside_survey": ("fmb_qa_outside_survey", "uid", OUTSIDE_SURVEY_COLUMNS),
}


def layer_geojson(conn: psycopg.Connection, name: str, *, bbox: tuple[float, float, float, float] | None = None,
                  zoom: int | None = None, limit: int = DEFAULT_LIMIT,
                  season: str | None = None) -> dict[str, Any]:
    """Returns a GeoJSON FeatureCollection for one registry layer.

    `season` (e.g. "2024-rabi") only applies to the `parcel` layer: it adds that parcel-season's
    land-use state alongside the always-on lifecycle stage / finding-count enrichment (ui-spec.md
    "parcel properties enriched with stage, finding counts, the season state ... and the DiD
    signal" — DiD itself is per-parcel/per-event/per-metric, so it stays on `/parcels/{uid}/satellite`
    rather than being flattened onto every map feature; `season_state`/`season_p_state` here already
    cover the map's colour-by-season-state use case).
    """
    tol = simplify_tolerance(zoom)
    params: list[Any] = []
    geom_expr = "geom"
    if tol:
        geom_expr = "ST_SimplifyPreserveTopology(geom, %s)"
        params.append(tol)

    if name == "fmb_qa":
        select_cols = ", ".join(f"f.{c}" for c in FMB_QA_COLUMNS)
        geom_ref = "p.geom"
        sql_from = "fmb_qa f JOIN parcel p ON p.parcel_uid = f.parcel_uid"
        columns = list(FMB_QA_COLUMNS)
    elif name in _FMB_ISSUE_LAYERS:
        table, join_col, cols = _FMB_ISSUE_LAYERS[name]
        select_cols = ", ".join(f"f.{c}" for c in cols)
        geom_ref = "p.geom"
        sql_from = f"{table} f JOIN parcel p ON p.parcel_uid = f.{join_col}"
        columns = list(cols)
    else:
        spec = LAYER_REGISTRY.get(name)
        if spec is None:
            raise KeyError(name)
        columns = list(spec["columns"])
        if name == "parcel":
            geom_ref = f"p.{spec['geom']}"
            select_cols = ", ".join(f"p.{c}" for c in spec["columns"])
            sql_from = (f"{spec['table']} p "
                       "LEFT JOIN v_parcel_lifecycle lc ON lc.parcel_uid = p.parcel_uid "
                       "LEFT JOIN (SELECT parcel_uid, count(*) AS n_findings FROM finding "
                       "WHERE status = 'open' GROUP BY 1) fc ON fc.parcel_uid = p.parcel_uid")
            select_cols += (", lc.current_stage AS current_stage, lc.evidence_level AS evidence_level, "
                           "lc.stalled_flag AS stalled_flag, COALESCE(fc.n_findings, 0) AS n_findings")
            columns += PARCEL_ENRICH_COLUMNS
            if season and "-" in season:
                ag_year_s, _, season_name = season.partition("-")
                try:
                    ag_year = int(ag_year_s)
                except ValueError:
                    ag_year = None
                if ag_year is not None:
                    sql_from += (" LEFT JOIN parcel_season ps ON ps.parcel_uid = p.parcel_uid "
                                "AND ps.ag_year = %s AND ps.season = %s")
                    params.extend([ag_year, season_name])
                    select_cols += ", ps.state AS season_state, ps.p_state AS season_p_state"
                    columns += ["season_state", "season_p_state"]
        else:
            geom_ref = spec["geom"]
            select_cols = ", ".join(spec["columns"])
            sql_from = spec["table"]

    sql = f"SELECT {select_cols}, ST_AsGeoJSON({geom_expr.replace('geom', geom_ref)})::json AS __geom FROM {sql_from}"

    where = []
    if bbox:
        where.append(f"ST_Intersects({geom_ref}, ST_MakeEnvelope(%s, %s, %s, %s, 4326))")
        params.extend(bbox)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT %s"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    features = [{
        "type": "Feature", "geometry": r["__geom"],
        "properties": {c: _jsonable(r[c]) for c in columns},
    } for r in rows]
    return {"type": "FeatureCollection", "features": features,
           "properties": {"layer": name, "count": len(features), "simplify_tolerance_deg": tol}}
