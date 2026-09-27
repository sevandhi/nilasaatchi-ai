"""Agent-facing satellite tools. They wrap the eo-engineer's ``app.tools.satellite`` / ``planet`` package
(called, never edited) and the KG satellite tables. Every output is a signal needing field verification."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import Evidence
from app.tools.db import query
from app.tools.registry import ClaimSeed, Tool, ToolError, ToolResult

FIELD_CAVEAT = "satellite signal requiring field verification"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeseriesIn(_In):
    parcel_uids: list[str] = Field(min_length=1, max_length=40, description="parcel_uid values '<village>|<KIDE>'")
    date_from: str = Field("2019-01-01")
    date_to: str = Field("2026-12-31")
    include_seasons: bool = True


def satellite_timeseries(a: TimeseriesIn, ctx) -> ToolResult:
    obs = query(ctx.kg, "SELECT parcel_uid, date, scene_id, round(ndvi::numeric, 4) AS ndvi, round(bsi::numeric, 4) AS bsi, "
                        "round(ndwi::numeric, 4) AS ndwi, round(valid_frac::numeric, 3) AS valid_frac, low_support "
                        "FROM parcel_obs WHERE parcel_uid = ANY(%s) AND date BETWEEN %s AND %s AND valid_frac >= 0.5 "
                        "ORDER BY parcel_uid, date", (a.parcel_uids, a.date_from, a.date_to))
    seasons = []
    if a.include_seasons:
        seasons = query(ctx.kg, "SELECT parcel_uid, ag_year, season, state, round(p_state::numeric, 3) AS p_state, route, "
                                "needs_field, features FROM parcel_season WHERE parcel_uid = ANY(%s) AND season <> 'annual' "
                                "ORDER BY parcel_uid, ag_year, season", (a.parcel_uids,))
    if not obs and not seasons:
        raise ToolError("not_found", f"no satellite observations for {a.parcel_uids[:3]}")
    ev = [Evidence(kind="timeseries", ref=f"parcel_obs:{u}", excerpt=f"{sum(1 for o in obs if o['parcel_uid'] == u)} obs")
          for u in a.parcel_uids]
    claims = []
    for s in seasons[-60:]:
        f = s.get("features") or {}
        claims.append(ClaimSeed(kind="entity", subject=f"{s['parcel_uid']} {s['ag_year']} {s['season']}",
                                field="landuse_state", value=s["state"], evidence=ev[:1],
                                checks=["satellite_min_obs", "satellite_indices_agree"],
                                context={"features": {k: f.get(k) for k in ("n_obs", "amplitude", "bsi_dry_mean",
                                                                            "max_gap_days", "gap_flag", "data_sufficient",
                                                                            "ndvi_max", "dry_mean")},
                                         "p_state": s["p_state"]},
                                confidence=float(s["p_state"] or 0.5)))
    for s in seasons:
        s.pop("features", None)
    return ToolResult(data={"obs": obs, "seasons": seasons}, evidence=ev, n_rows=len(obs), confidence=0.75,
                      caveats=[FIELD_CAVEAT, "NE-monsoon weed flush can mimic a crop cycle (D-035)"],
                      summary=f"{len(obs)} obs, {len(seasons)} parcel-seasons for {len(a.parcel_uids)} parcels",
                      claims=claims[:30])


class ChipIn(_In):
    parcel_uid: str
    date: str = Field(description="ISO date; nearest usable scene within max_days")
    max_days: int = Field(16, ge=1, le=60)


def satellite_chip(a: ChipIn, ctx) -> ToolResult:
    from app.tools import satellite as sat
    try:
        out = sat.satellite_chip(a.parcel_uid, a.date, max_days=a.max_days)
    except Exception as e:  # noqa: BLE001 - scene cache missing, no scene in window ...
        raise ToolError("unavailable", f"satellite_chip: {type(e).__name__}: {str(e)[:200]}") from None
    refs = [str(out.get(k)) for k in ("truecolor", "ndvi") if out.get(k)]
    ev = [Evidence(kind="satellite_chip", ref=r, excerpt=f"scene {out.get('scene_id', '?')} {out.get('date', '')}")
          for r in refs]
    return ToolResult(data=out, evidence=ev, confidence=0.9, caveats=[FIELD_CAVEAT],
                      summary=f"chips for {a.parcel_uid} at {out.get('date', a.date)}")


class LanduseIn(_In):
    parcel_uid: str
    ag_year: int = Field(ge=2018, le=2026, description="start year of the Jun-May agricultural year")
    season: str = Field(description="kharif | rabi | summer")
    call_vlm: bool = Field(False, description="allow a VLM second opinion when the student is unsure (uses quota)")


def landuse_state(a: LanduseIn, ctx) -> ToolResult:
    import psycopg

    from app.tools import satellite as sat
    try:
        with psycopg.connect(ctx.kg) as conn:
            out = sat.landuse_state(a.parcel_uid, a.ag_year, a.season, call_vlm=a.call_vlm, conn=conn)
    except LookupError as e:
        raise ToolError("not_found", str(e)) from None
    ev = [Evidence(kind="raster", ref=f"parcel_season:{a.parcel_uid}|{a.ag_year}|{a.season}",
                   excerpt=f"state={out.get('state')} route={out.get('route')}")]
    conf = float(out.get("p_state") or 0.5)
    return ToolResult(data=out, evidence=ev, confidence=conf,
                      caveats=[FIELD_CAVEAT, *list(out.get("caveats") or [])][:5],
                      summary=f"{a.parcel_uid} {a.ag_year} {a.season}: {out.get('state')} ({out.get('route')})",
                      claims=[ClaimSeed(kind="entity", subject=f"{a.parcel_uid} {a.ag_year} {a.season}",
                                        field="landuse_state", value=out.get("state"), evidence=ev,
                                        checks=["satellite_route_consistent"],
                                        context={"route": out.get("route"), "p_state": out.get("p_state"),
                                                 "needs_field": out.get("needs_field_verification")},
                                        confidence=conf)])


def satellite_tools() -> list[Tool]:
    return [
        Tool("satellite_timeseries", "Sentinel-2 per-parcel NDVI/BSI/NDWI observations (valid_frac>=0.5) and "
             "per-season land-use states (student model) for up to 40 parcels.", TimeseriesIn, satellite_timeseries,
             timeout_s=30),
        Tool("satellite_chip", "True-colour + NDVI chip PNGs for one parcel near a date (evidence / VLM input).",
             ChipIn, satellite_chip, timeout_s=60),
        Tool("landuse_state", "Land-use state of one parcel-season with probability, route and caveats; optional "
             "VLM second opinion when uncertain.", LanduseIn, landuse_state, timeout_s=150),
    ]
