"""Village-total reconciliation and KG integrity checks (``make kg-check``).

Prints FMB (dissolved union = primary, sum alongside) vs cadastral vs sanctioned (GO No.100 = English
AS letter, 904.40 ha) vs Tamil proposal (911.395 ha). Areas are geodesic (D-023). Exits non-zero only on genuine failures:
  - layer row counts differ from land-domain-knowledge §10
  - invalid / 3-D / non-4326 / empty geometries, bad parcel_uid construction
  - (village, survey) sets differ between FMB and cadastral
  - FMB village/total geodesic sums outside +-0.5 % of the §9 anchors, or the dissolved union
    outside +-0.5 % of 901.50 ha (D-023)
  - SQL geodesic area (PostGIS geography) disagrees with an independent pyproj Geod recomputation
  - precomputed distance/intersection columns missing
Proposal-vs-sanction deltas are EXPECTED discrepancies (demo findings). GIS-vs-sanction is printed as
information only: the "map exceeds sanction" claim is withdrawn (D-023).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field

import psycopg
import shapely
from dotenv import load_dotenv
from shapely import wkb

from pipeline.gis import ground_truth as gt
from pipeline.gis.geom import area_ha, polygonal_part

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY_AREA_TOL_HA = 0.0005


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected: list[dict] = field(default_factory=list)
    anomalies: dict = field(default_factory=dict)
    table: list[dict] = field(default_factory=list)

    def check(self, ok: bool, msg: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
        if not ok:
            self.failures.append(msg)


def _one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]


def check_counts(conn, rep: Report) -> None:
    print("\n== Layer row counts (expected from land-domain-knowledge §10)")
    for table, exp in gt.EXPECTED_COUNTS.items():
        n = _one(conn, f"SELECT count(*) FROM {table}")
        rep.check(n == exp, f"{table}: {n} (expected {exp})")
    n = _one(conn, "SELECT count(*) FROM village")
    rep.check(n == 7, f"village: {n} (expected 7)")


def check_geometry(conn, rep: Report) -> None:
    print("\n== Geometry hygiene")
    for table in ("parcel", "survey", "ref_layer_park_boundary", "ref_layer_waterbodies"):
        bad = _one(conn, f"""SELECT count(*) FROM {table} WHERE NOT ST_IsValid(geom) OR ST_IsEmpty(geom)
                               OR ST_NDims(geom) <> 2 OR ST_SRID(geom) <> 4326
                               OR GeometryType(geom) <> 'MULTIPOLYGON'
                               OR NOT ST_IsValid(geom_utm) OR ST_IsEmpty(geom_utm)""")
        rep.check(bad == 0, f"{table}: {bad} invalid/3-D/empty/non-multipolygon geometries")
    bad = _one(conn, """SELECT count(*) FROM parcel p JOIN village v ON v.id = p.village_id
                        WHERE p.parcel_uid <> v.name || '|' || p.kide""")
    rep.check(bad == 0, f"parcel_uid = '<canonical village>|<KIDE>' (D-020): {bad} violations")


def check_keys(conn, rep: Report) -> None:
    print("\n== FMB <-> cadastral key sets")
    only_fmb = conn.execute("""SELECT DISTINCT v.name, p.survey_no FROM parcel p JOIN village v ON v.id = p.village_id
                               WHERE NOT EXISTS (SELECT 1 FROM survey s WHERE s.village_id = p.village_id
                                                 AND s.survey_no = p.survey_no)""").fetchall()
    only_cad = conn.execute("""SELECT v.name, s.survey_no FROM survey s JOIN village v ON v.id = s.village_id
                               WHERE NOT EXISTS (SELECT 1 FROM parcel p WHERE p.village_id = s.village_id
                                                 AND p.survey_no = s.survey_no)""").fetchall()
    rep.check(not only_fmb and not only_cad,
              f"(village, survey) sets identical: {len(only_fmb)} FMB-only, {len(only_cad)} cadastral-only")


def check_python_area(conn, rep: Report) -> None:
    print("\n== Independent area recomputation (pyproj Geod, WGS84 geodesic)")
    worst = (0.0, None)
    n = 0
    for uid, g, a in conn.execute("SELECT parcel_uid, ST_AsBinary(geom), area_ha_gis FROM parcel"):
        geom = wkb.loads(bytes(g))
        m = shapely.make_valid(geom) if not geom.is_valid else geom
        d = abs(area_ha(polygonal_part(m)) - float(a))
        n += 1
        if d > worst[0]:
            worst = (d, uid)
    rep.check(worst[0] <= PY_AREA_TOL_HA,
              f"{n} parcels: max |python - postgis| = {worst[0]:.4f} ha ({worst[1]}), tol {PY_AREA_TOL_HA}")


def check_derived(conn, rep: Report) -> None:
    print("\n== Precomputed parcel attributes")
    nulls = _one(conn, """SELECT count(*) FROM parcel WHERE area_ha_gis IS NULL OR dist_major_road_m IS NULL
                           OR dist_any_road_m IS NULL OR dist_substation_m IS NULL OR dist_rail_station_m IS NULL
                           OR intersects_waterbody IS NULL OR inside_park_boundary IS NULL""")
    rep.check(nulls == 0, f"{nulls} parcels with a NULL precomputed column")
    r = conn.execute("""SELECT min(dist_major_road_m), percentile_cont(0.5) WITHIN GROUP (ORDER BY dist_major_road_m),
                               max(dist_major_road_m), max(dist_any_road_m),
                               min(dist_substation_m), max(dist_substation_m),
                               min(dist_rail_station_m), max(dist_rail_station_m),
                               count(*) FILTER (WHERE intersects_waterbody),
                               count(*) FILTER (WHERE NOT inside_park_boundary)
                        FROM parcel""").fetchone()
    print(f"  major road m: min {r[0]} / median {r[1]:.1f} / max {r[2]}; any road max {r[3]}")
    print(f"  substation m: {r[4]}..{r[5]}; rail station m: {r[6]}..{r[7]}")
    print(f"  intersects_waterbody: {r[8]} parcels; outside park boundary (point-on-surface): {r[9]}")


def _union_totals(conn, table: str) -> tuple[dict[str, float], dict[str, float], float, float]:
    """Per-village geodesic (sum, union) and overall (sum, union) for parcel or survey."""
    rows = conn.execute(f"""SELECT v.name, sum(t.area_ha_gis)::float8, geo_area_ha(ST_Union(t.geom))
                            FROM {table} t JOIN village v ON v.id = t.village_id GROUP BY v.name""").fetchall()
    tot_sum, tot_union = conn.execute(
        f"SELECT sum(area_ha_gis)::float8, geo_area_ha(ST_Union(geom)) FROM {table}").fetchone()
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}, tot_sum, tot_union


def reconcile(conn, rep: Report) -> None:
    fsum, funion, ftot_sum, ftot_union = _union_totals(conn, "parcel")
    csum, cunion, ctot_sum, ctot_union = _union_totals(conn, "survey")
    hdr = (f"{'Village':<20}{'GO/AS sanct.':>13}{'TA proposal':>12}{'FMB union':>11}{'FMB sum':>11}"
           f"{'§9 FMB':>9}{'Δ% sum':>8}{'Cad. union':>11}{'Cad. sum':>10}{'§9 cad':>8}"
           f"{'union−sanct':>12}{'prop−sanct':>11}")
    print("\n== Village reconciliation (geodesic ha, 4 dp; union = dissolved total, primary — D-023)")
    print(hdr)
    print("-" * len(hdr))
    ts = tp = 0.0
    for v in gt.SANCTIONED_HA:
        s, p = gt.SANCTIONED_HA[v], gt.PROPOSAL_HA[v]
        fu, fs, cu, cs = funion.get(v, 0.0), fsum.get(v, 0.0), cunion.get(v, 0.0), csum.get(v, 0.0)
        ef, ec = gt.FMB_EXPECTED_HA[v], gt.CADASTRAL_EXPECTED_HA[v]
        dpct = (fs - ef) / ef * 100
        ts += s
        tp += p
        print(f"{v:<20}{s:>13.4f}{p:>12.4f}{fu:>11.4f}{fs:>11.4f}{ef:>9.2f}{dpct:>+8.2f}{cu:>11.4f}{cs:>10.4f}"
              f"{ec:>8.2f}{fu - s:>+12.4f}{p - s:>+11.4f}")
        rep.table.append({"village": v, "sanctioned_ha": s, "proposal_ha": p, "fmb_union_ha": round(fu, 4),
                          "fmb_sum_ha": round(fs, 4), "fmb_expected_sum_ha": ef, "fmb_sum_delta_pct": round(dpct, 3),
                          "cadastral_union_ha": round(cu, 4), "cadastral_sum_ha": round(cs, 4),
                          "cadastral_expected_sum_ha": ec, "fmb_union_minus_sanctioned_ha": round(fu - s, 4),
                          "proposal_minus_sanctioned_ha": round(p - s, 4)})
        if abs(fs - ef) > gt.FMB_TOLERANCE * ef:
            rep.failures.append(f"FMB {v} sum {fs:.4f} ha outside ±0.5% of §9 {ef}")
        if abs(cs - ec) > gt.FMB_TOLERANCE * ec:
            rep.warnings.append(f"cadastral {v} sum {cs:.4f} ha outside ±0.5% of §9 {ec}")
        if abs(p - s) > 1e-9:
            rep.expected.append({"kind": "PROPOSAL_VS_SANCTION", "village": v, "delta_ha": round(p - s, 4)})
    print("-" * len(hdr))
    dpct = (ftot_sum - gt.FMB_EXPECTED_TOTAL_HA) / gt.FMB_EXPECTED_TOTAL_HA * 100
    print(f"{'Total (dissolved)':<20}{ts:>13.4f}{tp:>12.4f}{ftot_union:>11.4f}{ftot_sum:>11.4f}"
          f"{gt.FMB_EXPECTED_TOTAL_HA:>9.2f}{dpct:>+8.2f}{ctot_union:>11.4f}{ctot_sum:>10.4f}"
          f"{gt.CADASTRAL_EXPECTED_TOTAL_HA:>8.2f}{ftot_union - ts:>+12.4f}{tp - ts:>+11.4f}")
    print("  (village unions add up to more than the overall union: 110 overlapping pairs cross village lines)")
    rep.table.append({"village": "Total", "sanctioned_ha": round(ts, 4), "proposal_ha": round(tp, 4),
                      "fmb_union_ha": round(ftot_union, 4), "fmb_sum_ha": round(ftot_sum, 4),
                      "fmb_expected_sum_ha": gt.FMB_EXPECTED_TOTAL_HA, "fmb_sum_delta_pct": round(dpct, 3),
                      "cadastral_union_ha": round(ctot_union, 4), "cadastral_sum_ha": round(ctot_sum, 4),
                      "cadastral_expected_sum_ha": gt.CADASTRAL_EXPECTED_TOTAL_HA,
                      "fmb_union_minus_sanctioned_ha": round(ftot_union - ts, 4),
                      "proposal_minus_sanctioned_ha": round(tp - ts, 4)})

    print()
    rep.check(abs(ts - gt.SANCTIONED_TOTAL_HA) < 1e-6, f"sanctioned village sum = {ts:.4f} (904.40)")
    rep.check(abs(tp - gt.PROPOSAL_TOTAL_HA) < 1e-6, f"proposal village sum = {tp:.4f} (911.395)")
    upct = (ftot_union - gt.FMB_UNION_EXPECTED_TOTAL_HA) / gt.FMB_UNION_EXPECTED_TOTAL_HA * 100
    rep.check(abs(upct) <= gt.FMB_TOLERANCE * 100,
              f"FMB dissolved union {ftot_union:.4f} ha within ±0.5% of {gt.FMB_UNION_EXPECTED_TOTAL_HA} ({upct:+.3f}%)")
    rep.check(abs(dpct) <= gt.FMB_TOLERANCE * 100,
              f"FMB geodesic sum {ftot_sum:.4f} ha within ±0.5% of §9 {gt.FMB_EXPECTED_TOTAL_HA} ({dpct:+.3f}%)")
    per_village_fail = [m for m in rep.failures if m.startswith("FMB ") and "outside" in m]
    rep.check(not per_village_fail, "FMB village geodesic sums each within ±0.5% of §9"
              + (f": {per_village_fail}" if per_village_fail else ""))
    b = float(_one(conn, "SELECT geo_area_ha(ST_Union(geom)) FROM ref_layer_park_boundary"))
    bp = (b - gt.PARK_BOUNDARY_EXPECTED_HA) / gt.PARK_BOUNDARY_EXPECTED_HA * 100
    print(f"  [INFO] park boundary {b:.4f} ha (geodesic) vs §9 {gt.PARK_BOUNDARY_EXPECTED_HA} ({bp:+.3f}%)")
    if abs(bp) > 0.5:
        rep.warnings.append(f"park boundary {b:.4f} ha outside ±0.5% of {gt.PARK_BOUNDARY_EXPECTED_HA}")
    utm_sum = float(_one(conn, "SELECT sum(ST_Area(geom_utm)) / 1e4 FROM parcel"))
    print(f"  [INFO] GIS vs sanction (informational only; claim withdrawn, D-023): union "
          f"{ftot_union - ts:+.4f} ha, geodesic sum {ftot_sum - ts:+.4f} ha, UTM44 planar sum {utm_sum - ts:+.4f} ha")
    rep.anomalies["area_method_sensitivity"] = {"fmb_union_geodesic_ha": round(ftot_union, 4),
                                                "fmb_sum_geodesic_ha": round(ftot_sum, 4),
                                                "fmb_sum_utm44_ha": round(utm_sum, 4)}
    rep.expected.insert(0, {"kind": "PROPOSAL_VS_SANCTION", "village": "Total", "delta_ha": round(tp - ts, 4),
                            "note": "Tamil proposal AS.pdf p2 vs GO/AS sanction (skill §9: +6.995 ha)"})


def anomalies(conn, rep: Report) -> None:
    print("\n== Data anomalies / FMB quality (informational; fmb_qa feeds P5 findings)")
    a: dict = rep.anomalies
    notes = dict(conn.execute("SELECT layer, notes FROM gis_layer_load").fetchall())
    fmb_notes = notes.get("fmb", {})
    a["fmb_sub_div_differs_from_kide"] = fmb_notes.get("sub_div_differs_from_kide", [])
    a["fmb_survey_no_differs_from_kide"] = fmb_notes.get("survey_no_differs_from_kide", [])
    a["fmb_null_block_id"] = fmb_notes.get("null_block_id", [])
    a["district_variants"] = {k: notes.get(k, {}).get("district_variants") for k in ("fmb", "cadastral")}
    a["layers_z_stripped"] = dict(conn.execute(
        "SELECT layer, n_z_stripped FROM gis_layer_load WHERE n_z_stripped > 0").fetchall())
    a["invalid_source_geoms_fixed"] = dict(conn.execute(
        "SELECT layer, n_invalid_fixed FROM gis_layer_load WHERE n_invalid_fixed > 0").fetchall())
    a["kide_repeated_across_villages"] = _one(conn, """SELECT count(*) FROM (SELECT kide FROM parcel GROUP BY kide
                                                        HAVING count(DISTINCT village_id) > 1) t""")
    a["unit_block_mismatch_fmb_vs_cadastral"] = [
        {"village": r[0], "survey_no": r[1], "cadastral": [r[2], r[3]], "fmb": [r[4], r[5]], "n_parcels": r[6]}
        for r in conn.execute("""SELECT v.name, s.survey_no, s.unit_id, s.block_id, p.unit_id, p.block_id, count(*)
                                 FROM parcel p JOIN survey s USING (village_id, survey_no)
                                 JOIN village v ON v.id = p.village_id
                                 WHERE (p.unit_id, p.block_id) IS DISTINCT FROM (s.unit_id, s.block_id)
                                 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2""")]
    r = conn.execute("""SELECT count(*) FILTER (WHERE area_outside_boundary_ha > 0.0001),
                               coalesce(sum(area_outside_boundary_ha), 0), coalesce(max(area_outside_boundary_ha), 0),
                               count(*) FILTER (WHERE NOT inside_park_boundary) FROM parcel""").fetchone()
    a["parcels_outside_boundary"] = {"n_with_area_outside_gt_1m2": r[0], "total_outside_ha": float(r[1]),
                                     "max_outside_ha": float(r[2]), "n_point_on_surface_outside": r[3]}
    r = conn.execute("""SELECT count(*), coalesce(sum(overlap_ha), 0), coalesce(max(overlap_ha), 0),
                               count(*) FILTER (WHERE cross_village) FROM fmb_qa_overlap""").fetchone()
    a["fmb_qa_overlap"] = {"n_pairs_gt_1m2": r[0], "overlap_ha": float(r[1]), "max_pair_ha": float(r[2]),
                           "n_cross_village": r[3]}
    r = conn.execute("""SELECT count(*), coalesce(sum(outside_ha), 0), coalesce(max(outside_ha), 0)
                        FROM fmb_qa_outside_survey""").fetchone()
    a["fmb_qa_outside_survey_gt_100m2"] = {"n_parcels": r[0], "total_ha": float(r[1]), "max_ha": float(r[2])}
    for k, v in a.items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)}")


def run(dsn: str) -> Report:
    rep = Report()
    with psycopg.connect(dsn) as conn:
        check_counts(conn, rep)
        check_geometry(conn, rep)
        check_keys(conn, rep)
        check_python_area(conn, rep)
        check_derived(conn, rep)
        reconcile(conn, rep)
        anomalies(conn, rep)
    print("\n== EXPECTED discrepancies (known demo findings, not failures)")
    for e in rep.expected:
        print(f"  [EXPECTED] {e['kind']:<21} {e['village']:<18} {e['delta_ha']:+.4f} ha  {e.get('note', '')}")
    for w in rep.warnings:
        print(f"  [WARN] {w}")
    print(f"\nkg-check: {'FAILED' if rep.failures else 'OK'} "
          f"({len(rep.failures)} failures, {len(rep.warnings)} warnings, {len(rep.expected)} expected discrepancies)")
    for f in rep.failures:
        print(f"  FAIL: {f}")
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="KG reconciliation vs land-domain-knowledge §9")
    ap.add_argument("--json", type=pathlib.Path, help="also write the report as JSON to this path")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    rep = run(os.environ["DATABASE_URL"])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep.__dict__, indent=2, ensure_ascii=False, default=str))
    sys.exit(1 if rep.failures else 0)


if __name__ == "__main__":
    main()
