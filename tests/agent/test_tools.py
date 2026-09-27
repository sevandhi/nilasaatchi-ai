"""Every tool on the fixture KG, including the seeded defects the verifier must catch."""
import asyncio

import pytest

from app.domains import get_pack
from app.tools.context import ToolContext
from tests.agent.conftest import PLOTS, SCANNED

LAND = get_pack("land_acquisition")
AGRI = get_pack("agri_claims")


def run(pack, tool, args, kg, **kw):
    ctx = ToolContext(run_id="t", step_id="s", domain=pack.name, dsn=kg, **kw)
    return asyncio.run(pack.registry().run(tool, args, ctx))


def checks_pass(pack, res, kg):
    from app.agent.state import Claim, Producer
    bad = []
    for i, s in enumerate(res.claims):
        c = Claim(id=f"c{i}", step_id="s", kind=s.kind, subject=s.subject, field=s.field, value=s.value,
                  evidence=s.evidence, producer=Producer(kind="tool", id="t"), context=s.context)
        for n in s.checks:
            r = pack.checks[n](c, kg)
            if not r.passed:
                bad.append((s.subject, s.field, n, r.detail))
    return bad


def test_land_pack_has_the_13_tools():
    assert set(LAND.registry().names()) == {
        "catalog_search", "doc_retrieve", "extract_document", "sql_query", "spatial_query", "match_parcels",
        "lifecycle_status", "satellite_timeseries", "satellite_chip", "landuse_state", "paper_vs_planet",
        "evidence_pack", "compose_workspace", "findings_query", "parcel_timeline", "satellite_summary"}


def test_catalog_and_fts(kg_dsn):
    r = run(LAND, "catalog_search", {"village": "Melathattaparai"}, kg_dsn)
    assert r.n_rows >= 5 and not checks_pass(LAND, r, kg_dsn)
    r = run(LAND, "doc_retrieve", {"query": "மேலத்தட்டப்பாறை தொகுதி"}, kg_dsn)
    assert r.n_rows >= 1 and r.evidence[0].kind == "page_bbox"


@pytest.mark.parametrize("args", [
    {"template": "area_by_group", "group_by": "village"},
    {"template": "buffer_within", "layer": "substations", "distance_m": 3000},
    {"template": "distance_to_nearest", "layer": "major_roads", "villages": ["Ramasamypuram"]},
    {"template": "intersects_layer", "layer": "park_boundary"},
    {"template": "filter_by_raster_stat", "raster_stat": "slope_mean", "op": ">=", "value": 0}])
def test_spatial_templates(kg_dsn, args):
    r = run(LAND, "spatial_query", args, kg_dsn)
    assert r.sql and not checks_pass(LAND, r, kg_dsn)


def test_lifecycle_seeded_flags(kg_dsn):
    r = run(LAND, "lifecycle_status", {"as_of": "2026-09-27"}, kg_dsn)
    flags = {f for p in r.data["parcels"] for f in p["flags"]}
    assert {"STALLED", "STAGE_ORDER_VIOLATION", "EXEMPTION_CONFLICT"} <= flags
    assert not checks_pass(LAND, r, kg_dsn)


def test_paper_vs_planet_catches_seeded_defects(kg_dsn):
    r = run(LAND, "paper_vs_planet", {}, kg_dsn)
    cats = r.data["by_category"]
    assert cats.get("COMPENSATION_MISMATCH") == 1
    assert cats.get("EXTENT_MISMATCH") == 2
    assert cats.get("PV3_POST_POSSESSION_ACTIVITY") == 3
    assert cats.get("EXEMPTION_CONFLICT") == 1 and cats.get("STAGE_ORDER_VIOLATION") == 1
    assert not checks_pass(LAND, r, kg_dsn)


def test_match_parcels_never_crosses_villages(kg_dsn):
    r = run(LAND, "match_parcels", {"rows": [{"village": "மேலத்தட்டப்பாறை", "survey_no": "233"},
                                            {"village": "Allikulam", "survey_no": "233"}]}, kg_dsn)
    m = r.data["matches"]
    assert m[0]["parcel_uid"] == "Melathattaparai|233" and m[1]["status"] == "unmatched"


def test_evidence_pack_and_satellite(kg_dsn):
    r = run(LAND, "evidence_pack", {"parcel_uid": "Melathattaparai|233"}, kg_dsn)
    assert r.data["timeline"]["events"] and r.data["timeline"]["series"]
    assert all(e.bbox for e in r.evidence if e.kind == "page_bbox")
    r = run(LAND, "satellite_timeseries", {"parcel_uids": ["Melathattaparai|233"]}, kg_dsn)
    assert r.data["obs"] and r.data["seasons"]
    r = run(LAND, "landuse_state", {"parcel_uid": "Melathattaparai|233", "ag_year": 2023, "season": "rabi"}, kg_dsn)
    assert r.data["state"]


def test_low_text_pdf_gives_low_confidence(kg_dsn):
    from app.agent.state import Attachment
    r = run(LAND, "extract_document", {"attachment_id": "a1"}, kg_dsn,
            attachments=[Attachment(id="a1", name="scan.pdf", path=SCANNED)])
    assert r.confidence < 0.3 and any("low text coverage" in c for c in r.caveats)


def test_agri_crop_presence_and_fallow(kg_dsn):
    r = run(AGRI, "crop_presence", {"polygons_ref": PLOTS, "year_from": 2021, "year_to": 2024}, kg_dsn)
    status = {row["plot_id"]: row["status"] for row in r.data["rows"]}
    assert status["PLOT-FAR"] == "no_satellite_data"
    assert any(s in ("crop", "no_crop") for s in status.values())
    assert not checks_pass(AGRI, r, kg_dsn)
    r = run(AGRI, "fallow_streak", {"polygons_ref": PLOTS, "min_seasons": 2}, kg_dsn)
    assert not checks_pass(AGRI, r, kg_dsn)
