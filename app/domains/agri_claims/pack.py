"""agri_claims domain pack: crop presence / fallow streaks on uploaded plot polygons (crop-insurance claim
verification, loan-utilisation and crop-survey cross-checks). Reuses the generic graph unchanged."""
from __future__ import annotations

import re
from pathlib import Path

from app.agent.state import Attachment, MissingInfo, Slots
from app.domains import DomainPack, SlotResult
from app.domains.common_slots import attachment_kind, seasons, thresholds, years

ROOT = Path(__file__).resolve().parent
SQL_ALLOW = frozenset({"parcel_season", "parcel_obs", "s2_scene", "upload_polygon"})


def extract_slots(request: str, attachments: list[Attachment]) -> SlotResult:
    s = Slots(seasons=seasons(request), years=years(request), thresholds=thresholds(request))
    polys = [a for a in attachments if attachment_kind(a) == "geojson"]
    if polys:
        s.polygons_ref = polys[0].path
    ids = re.findall(r"\b([A-Z][A-Za-z ]+\|[0-9A-Z/]+)\b", request)
    s.parcel_uids = ids
    missing, assumptions = [], []
    if not polys and not ids:
        missing.append(MissingInfo(slot="polygons", blocking=True,
                                   question="Please upload the plot polygons (GeoJSON) to check."))
    if not s.seasons:
        assumptions.append("All seasons (kharif, rabi, summer) are checked.")
    if not s.years:
        assumptions.append("Agricultural years 2019-2025 (Jun-May) are checked.")
    return SlotResult(s, missing, assumptions)


def detect(request: str, attachments: list[Attachment]) -> float:
    low = request.lower()
    score = 0.0
    if re.search(r"\bcrop|fallow|insurance|claim|sowing|plot|harvest|பயிர்|தரிசு", low):
        score += 0.6
    if any(attachment_kind(a) == "geojson" for a in attachments):
        score += 0.35
    return min(score, 1.0)


def build_tools():
    from app.domains.agri_claims.tools import agri_tools
    from app.tools.kg_tools import spatial_query_tool
    from app.tools.satellite_tools import satellite_tools
    from app.tools.workspace import workspace_tools
    return agri_tools() + [t for t in satellite_tools() if t.name != "landuse_state"] + [spatial_query_tool()] + \
        workspace_tools()


def _checks():
    from app.tools.checks import GENERIC_CHECKS
    return dict(GENERIC_CHECKS)


PACK = DomainPack(name="agri_claims", title="Agri claims (crop presence / fallow)", root=ROOT, build_tools=build_tools,
                  sql_allowlist=SQL_ALLOW, extract_slots=extract_slots, detect=detect, checks=_checks(),
                  schema_notes="parcel_season(ag_year Jun-May start year, season kharif|rabi|summer, state, p_state).",
                  sql_examples=[], critic_tools=["crop_presence", "satellite_timeseries"],
                  default_plan=lambda s: {
                      "goal": "Crop presence and fallow streaks for the uploaded plots (deterministic fallback plan)",
                      "steps": [{"id": "s1", "tool": "crop_presence", "args": {"polygons_ref": s.polygons_ref,
                                                                               "parcel_uids": s.parcel_uids}},
                                {"id": "s2", "tool": "fallow_streak", "args": {"polygons_ref": s.polygons_ref,
                                                                               "parcel_uids": s.parcel_uids}}],
                      "outputs": ["table", "chart", "map", "narrative"]})
