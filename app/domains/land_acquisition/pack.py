"""land_acquisition domain pack (Allikulam SIPCOT; TN Act 10/1999)."""
from __future__ import annotations

import re
from pathlib import Path

from app.agent.state import Attachment, MissingInfo, Slots
from app.domains import DomainPack, SlotResult
from app.domains.common_slots import attachment_kind, seasons, thresholds, years

ROOT = Path(__file__).resolve().parent

STAGE_WORDS = [
    (r"possession|handed over|ldr|delivery|ஒப்படை", "POSSESSION"), (r"award|7\(2\)|7\(3\)|form f|தீர்வு", "AWARD"),
    (r"paid|payment|disburs|deposit|compensation paid", "PAYMENT"), (r"mutation|patta transfer|chitta", "MUTATION"),
    (r"3\(1\)|gazette|notification|notified", "SEC_3_1"), (r"3\(2\)|notice to owners", "SEC_3_2"),
    (r"exempt", "EXEMPTION"), (r"dlpnc|slpnc|price negotiation|land value", "PRICE_NEGOTIATION"),
    (r"form e|possession notice", "POSSESSION_NOTICE"), (r"stall", "STALLED"),
]
SURVEY_RE = re.compile(r"(?:survey|s\.?\s?no\.?|sy\.?\s?no\.?|புல\s*எண்)\s*(?:no\.?|number)?\s*[:#]?\s*"
                       r"(\d{1,4}(?:\s*/\s*[0-9A-Za-z]{1,4})?)", re.IGNORECASE)
BLOCK_RE = re.compile(r"(?:block|தொகுதி|பிளாக்)\s*(?:no\.?)?\s*(\d{1,2})", re.IGNORECASE)
SQL_ALLOW = frozenset({
    "village", "parcel", "survey", "s2_scene", "parcel_obs", "parcel_season", "upload_polygon",
    "ref_layer_roads", "ref_layer_rail", "ref_layer_rail_stations", "ref_layer_waterbodies", "ref_layer_substations",
    "ref_layer_airport", "ref_layer_seaport", "ref_layer_schools", "ref_layer_sipcot_parks", "ref_layer_park_boundary",
    "extraction", "parcel_fact", "acquisition_event", "review_queue", "v_owner_pseudo", "planet_teacher_label",
    "parcel_event_window", "parcel_did", "v_parcel_status", "v_village_progress", "v_findings_open",
    "finding", "v_finding_summary", "v_idle_land_bank", "v_parcel_lifecycle", "v_parcel_events", "v_village_stage_matrix",
    "fmb_qa", "fmb_qa_overlap", "fmb_qa_outside_survey",
})


def _aliases():
    from pipeline.gis.aliases import fold, load_aliases
    return load_aliases(), fold


def extract_slots(request: str, attachments: list[Attachment]) -> SlotResult:
    al, fold = _aliases()
    freq = fold(request)
    villages = []
    for v in al.villages:
        names = [v.name, *(v.aliases or ()), *([v.name_ta] if v.name_ta else [])]
        if any(fold(n) and fold(n) in freq for n in names):
            villages.append(v.name)
    low = request.lower()
    stages = [code for pat, code in STAGE_WORDS if re.search(pat, low)]
    surveys = [re.sub(r"\s+", "", m.group(1)).upper() for m in SURVEY_RE.finditer(request)]
    blocks = sorted({int(b) for b in BLOCK_RE.findall(request)})
    s = Slots(villages=sorted(set(villages)), surveys=list(dict.fromkeys(surveys)), blocks=blocks,
              stages=list(dict.fromkeys(stages)), seasons=seasons(request), years=years(request),
              thresholds=thresholds(request))
    missing, assumptions = [], []
    kinds = [attachment_kind(a) for a in attachments]
    if "pdf" in kinds or "image" in kinds:
        s.extra["documents"] = [a.id for a in attachments if attachment_kind(a) in ("pdf", "image")]
    if s.surveys and not s.villages:
        missing.append(MissingInfo(slot="village", blocking=True,
                                   question="Which village is survey " + ", ".join(s.surveys) +
                                            " in? Survey numbers repeat across villages."))
    if not s.villages and not s.surveys and not attachments:
        assumptions.append("No village named: the whole park (all villages) is in scope.")
    if "STALLED" in s.stages and "days" not in s.thresholds:
        assumptions.append("Stalled = no next lifecycle event for 90 days or more.")
    return SlotResult(s, missing, assumptions)


def resolve_slots(slots: Slots, dsn: str) -> Slots:
    """Parcel uids for (village, survey[/subdiv]) slot pairs; never across villages."""
    from app.tools.db import query
    out = list(slots.parcel_uids)
    for v in slots.villages:
        for sv in slots.surveys:
            num, _, sub = sv.partition("/")
            rows = query(dsn, "SELECT p.parcel_uid FROM parcel p JOIN village vi ON vi.id = p.village_id "
                              "WHERE vi.name = %s AND p.survey_no = %s AND (%s = '' OR p.sub_div = %s) ORDER BY 1",
                         (v, num, sub, sub))
            out += [r["parcel_uid"] for r in rows]
    return slots.model_copy(update={"parcel_uids": list(dict.fromkeys(out))})


def default_plan(slots: Slots) -> dict:
    scope = {"villages": slots.villages, "blocks": slots.blocks, "parcel_uids": slots.parcel_uids}
    return {"goal": "Lifecycle status and findings for the requested scope (deterministic fallback plan)",
            "steps": [{"id": "s1", "tool": "lifecycle_status", "args": scope, "rationale": "stage status"},
                      {"id": "s2", "tool": "paper_vs_planet", "args": scope, "rationale": "findings"}],
            "outputs": ["kpis", "map", "table", "narrative"]}


def detect(request: str, attachments: list[Attachment]) -> float:
    low = request.lower()
    score = 0.3
    if re.search(r"acqui|award|possession|survey|parcel|patta|gazette|compensation|sipcot|village|block|fmb|"
                 r"stage|lifecycle|notification|நில|புல எண்|கிராம", low):
        score += 0.5
    if any(attachment_kind(a) == "pdf" for a in attachments):
        score += 0.2
    return min(score, 1.0)


def build_tools():
    from app.domains.land_acquisition.kg_views import kg_view_tools
    from app.domains.land_acquisition.tools import land_tools
    from app.tools.kg_tools import generic_tools
    from app.tools.satellite_tools import satellite_tools
    from app.tools.workspace import workspace_tools
    return generic_tools() + satellite_tools() + land_tools() + kg_view_tools() + workspace_tools()


SCHEMA_NOTES = """- parcel_uid = '<Village>|<KIDE>' (e.g. 'Melathattaparai|233', 'Allikulam|16/3'); KIDE repeats across villages,
  so always filter by village. village.name holds canonical English names.
- Areas: parcel.area_ha_gis is geodesic ha. For totals use geo_area_ha(ST_Union(geom)) (dissolved union) and show the
  sum alongside. Distances/buffers use geom_utm (EPSG:32644, metres). parcel has dist_major_road_m, dist_substation_m.
- acquisition_event.stage in (GO_AS_LPS, SEC_3_1, SEC_3_2, EXEMPTION, PRICE_NEGOTIATION, POSSESSION_NOTICE, AWARD,
  PAYMENT, POSSESSION, MUTATION); event_date; parcel_uid NULL = document-level event.
- parcel_fact(fact_type in extent_ha, extent_ac, classification (dry|wet|poramboke), amount_rs, rate_per_acre,
  owner, patta_no). NEVER select owner/patta_no values; use v_owner_pseudo tokens if owners must be counted.
- parcel_season(ag_year = start year of Jun-May year, season in kharif|rabi|summer|annual, state in cropped,
  irrigated_multi, perennial_veg, bare_fallow, cleared_or_built, water, insufficient_data, p_state).
- parcel_did(event='t_possession', metric='vigour_z', did, ci_lo, ci_hi, farmland_like_p): post-possession
  difference-in-differences vs control farmland.
- parcel_event_window(window_name in pre_notification, pending, post_award, post_possession; dominant_state).
- fmb_qa(issue OVERLAP|OUTSIDE_SURVEY, parcel_uid, other_parcel_uid, area_ha).
- v_village_progress(village, n_parcels, fmb_area_ha, fmb_union_ha, cadastral_area_ha)."""

SQL_EXAMPLES = [
    {"q": "Number of FMB parcels and dissolved area per village",
     "sql": "SELECT village, n_parcels, fmb_union_ha, fmb_area_ha FROM v_village_progress ORDER BY village;"},
    {"q": "Umarikottai parcels in block 3 more than 1 km from a major road",
     "sql": "SELECT p.parcel_uid, p.dist_major_road_m FROM parcel p JOIN village v ON v.id = p.village_id "
            "WHERE v.name = 'Umarikottai' AND p.block_id = 3 AND p.dist_major_road_m > 1000 ORDER BY 2 DESC;"},
    {"q": "Acquisition events per stage in Peroorani",
     "sql": "SELECT stage, count(*) AS n_events FROM acquisition_event WHERE village = 'Peroorani' GROUP BY stage ORDER BY 2 DESC;"},
    {"q": "Parcels classified cleared_or_built in rabi 2024",
     "sql": "SELECT parcel_uid, p_state FROM parcel_season WHERE ag_year = 2024 AND season = 'rabi' "
            "AND state = 'cleared_or_built' ORDER BY p_state DESC;"},
    {"q": "Document extent vs GIS area per block in Allikulam",
     "sql": "SELECT p.block_id, round(sum(f.value_num), 4) AS doc_ha, round(sum(p.area_ha_gis), 4) AS gis_ha "
            "FROM parcel_fact f JOIN parcel p ON p.parcel_uid = f.parcel_uid JOIN village v ON v.id = p.village_id "
            "WHERE v.name = 'Allikulam' AND f.fact_type = 'extent_ha' GROUP BY 1 ORDER BY 1;"},
]


def _checks():
    from app.domains.land_acquisition.checks import LAND_CHECKS
    from app.tools.checks import GENERIC_CHECKS
    return {**GENERIC_CHECKS, **LAND_CHECKS}


PACK = DomainPack(name="land_acquisition", title="Land acquisition (Paper vs Planet)", root=ROOT,
                  build_tools=build_tools, sql_allowlist=SQL_ALLOW, extract_slots=extract_slots, detect=detect,
                  checks=_checks(), schema_notes=SCHEMA_NOTES, sql_examples=SQL_EXAMPLES,
                  critic_tools=["sql_query", "spatial_query", "lifecycle_status", "satellite_timeseries",
                                "landuse_state", "paper_vs_planet", "findings_query",
                                "parcel_timeline", "satellite_summary"],
                  resolve_slots=resolve_slots, default_plan=default_plan,
                  non_name_words=frozenset({"தொகை", "இழப்பீட்டுத்", "இழப்பீடு", "இழப்பீட்டு", "ரூபாய்", "மட்டும்",
                                            "நிலம்", "கிராமம்", "கிராமத்தில்", "முரண்பாடுகள்", "அபராத", "ஆறுதல்",
                                            "ஆதரவுத்", "Amount", "Compensation", "Total", "Extent", "Village"}))
