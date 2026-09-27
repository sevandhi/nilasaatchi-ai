"""agri_claims tools: crop_presence and fallow_streak for uploaded plot polygons (or known parcel ids).

Season states come from the satellite land-use classifier (parcel_season). For an uploaded polygon:
  1. rows keyed by polygon_id in parcel_season (on-demand extraction; TODO eo-engineer planet polygon support), else
  2. an area-weighted overlay with classified 10 m-grid footprints that intersect the polygon (proxy), with
     coverage reported; coverage < 50 % -> 'insufficient_coverage'.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import Evidence
from app.tools.db import query
from app.tools.registry import ClaimSeed, Tool, ToolError, ToolResult

CROP_STATES = ("cropped", "irrigated_multi")
SEASON_ORDER = {"kharif": 0, "rabi": 1, "summer": 2}
CAVEAT = "satellite signal requiring field verification"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlotsIn(_In):
    polygons_ref: str | None = Field(None, description="path of an uploaded GeoJSON FeatureCollection (from slots)")
    parcel_uids: list[str] = Field(default_factory=list, description="known plot ids instead of polygons")
    seasons: list[str] = Field(default_factory=lambda: ["kharif", "rabi", "summer"])
    year_from: int = Field(2019, ge=2018, le=2026, description="first ag-year (Jun-May, start year)")
    year_to: int = Field(2025, ge=2018, le=2026)


class FallowIn(PlotsIn):
    min_seasons: int = Field(2, ge=1, le=12)


def _plots(a: PlotsIn, ctx) -> list[dict]:
    if a.polygons_ref:
        p = Path(a.polygons_ref)
        if not p.exists():
            raise ToolError("not_found", f"polygon file not found: {p.name}")
        fc = json.loads(p.read_text(encoding="utf-8"))
        feats = fc.get("features", []) if fc.get("type") == "FeatureCollection" else [fc]
        out = []
        for i, f in enumerate(feats):
            props = f.get("properties") or {}
            out.append({"plot_id": str(props.get("plot_id") or props.get("name") or props.get("id") or f"plot_{i + 1}"),
                        "geometry": json.dumps(f["geometry"])})
        if not out:
            raise ToolError("bad_args", "no polygon features in upload")
        return out
    if a.parcel_uids:
        return [{"plot_id": u, "uid": u} for u in a.parcel_uids]
    if ctx.slots.polygons_ref:
        return _plots(a.model_copy(update={"polygons_ref": ctx.slots.polygons_ref}), ctx)
    raise ToolError("bad_args", "need polygons_ref or parcel_uids")


def season_table(a: PlotsIn, ctx) -> tuple[list[dict], list[str]]:
    rows, caveats = [], [CAVEAT, "NE-monsoon weed flush can look like a rabi crop"]
    for pl in _plots(a, ctx):
        if "uid" in pl:
            q = query(ctx.kg, "SELECT ag_year, season, state, 1.0::float AS share, 1.0::float AS coverage FROM parcel_season "
                              "WHERE parcel_uid = %s AND season = ANY(%s) AND ag_year BETWEEN %s AND %s",
                      (pl["uid"], a.seasons, a.year_from, a.year_to))
            src = "classified plot"
        else:
            q = query(ctx.kg, """
                WITH g AS (SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 32644) AS g),
                ov AS (SELECT p.parcel_uid, ST_Area(ST_Intersection(p.geom_utm, g.g)) AS a, ST_Area(g.g) AS ga
                       FROM parcel p, g WHERE ST_Intersects(p.geom_utm, g.g))
                SELECT s.ag_year, s.season, s.state, sum(ov.a) / max(ov.ga) AS share,
                       (SELECT least(1.0, sum(a) / max(ga)) FROM ov) AS coverage
                FROM ov JOIN parcel_season s ON s.parcel_uid = ov.parcel_uid
                WHERE s.season = ANY(%s) AND s.ag_year BETWEEN %s AND %s
                GROUP BY 1, 2, 3""", (pl["geometry"], a.seasons, a.year_from, a.year_to))
            src = "overlay proxy"
        by: dict[tuple, dict] = {}
        for r in q:
            k = (r["ag_year"], r["season"])
            d = by.setdefault(k, {"shares": {}, "coverage": float(r["coverage"] or 0)})
            d["shares"][r["state"]] = d["shares"].get(r["state"], 0) + float(r["share"] or 0)
        for (y, s), d in sorted(by.items(), key=lambda kv: (kv[0][0], SEASON_ORDER.get(kv[0][1], 9))):
            tot = sum(d["shares"].values()) or 1.0
            crop = sum(v for k, v in d["shares"].items() if k in CROP_STATES) / tot
            if d["coverage"] < 0.5:
                status = "insufficient_coverage"
            elif d["shares"].get("insufficient_data", 0) / tot > 0.5:
                status = "insufficient_data"
            else:
                status = "crop" if crop >= 0.5 else "no_crop"
            rows.append({"plot_id": pl["plot_id"], "ag_year": y, "season": s, "crop_share": round(crop, 3),
                         "coverage": round(d["coverage"], 3), "status": status, "source": src,
                         "dominant_state": max(d["shares"], key=d["shares"].get)})
        if not by:
            rows.append({"plot_id": pl["plot_id"], "ag_year": None, "season": None, "crop_share": None, "coverage": 0.0,
                         "status": "no_satellite_data", "source": src, "dominant_state": None})
        if src == "overlay proxy":
            caveats.append("uploaded polygons use an area-weighted overlay with classified footprints (proxy; "
                           "on-demand polygon extraction pending)")
    return rows, list(dict.fromkeys(caveats))


def crop_presence(a: PlotsIn, ctx) -> ToolResult:
    rows, cav = season_table(a, ctx)
    if all(r["status"] == "no_satellite_data" for r in rows):
        raise ToolError("not_found", "no satellite season states cover these plots")
    ev = [Evidence(kind="raster", ref=f"parcel_season:{r['plot_id']}|{r['ag_year']}|{r['season']}",
                   excerpt=f"{r['status']} crop_share={r['crop_share']}") for r in rows[:30]]
    n_crop = sum(1 for r in rows if r["status"] == "crop")
    claims = [ClaimSeed(kind="table_cell", subject=f"{r['plot_id']} {r['ag_year']} {r['season']}", field="crop_status",
                        value=r["status"], checks=["crop_share_consistent"],
                        context={"crop_share": r["crop_share"], "coverage": r["coverage"]}, confidence=0.7)
              for r in rows if r["ag_year"] is not None][:40]
    claims.append(ClaimSeed(kind="kpi", subject="plots", field="n_crop_seasons", value=n_crop,
                            checks=["count_matches_rows"], context={"rows": n_crop}))
    return ToolResult(data={"rows": rows}, evidence=ev, n_rows=len(rows), confidence=0.7, caveats=cav,
                      summary=f"{n_crop}/{len(rows)} plot-seasons with a crop", claims=claims)


def fallow_streak(a: FallowIn, ctx) -> ToolResult:
    rows, cav = season_table(a, ctx)
    streaks = []
    for pid in dict.fromkeys(r["plot_id"] for r in rows):
        seq = [r for r in rows if r["plot_id"] == pid and r["ag_year"] is not None]
        cur: list[dict] = []
        for r in seq + [None]:
            if r is not None and r["status"] == "no_crop":
                cur.append(r)
                continue
            if len(cur) >= a.min_seasons:
                streaks.append({"plot_id": pid, "n_seasons": len(cur), "from": f"{cur[0]['ag_year']} {cur[0]['season']}",
                                "to": f"{cur[-1]['ag_year']} {cur[-1]['season']}"})
            cur = []
    ev = [Evidence(kind="raster", ref=f"parcel_season:{s['plot_id']}", excerpt=f"{s['n_seasons']} seasons {s['from']}..{s['to']}")
          for s in streaks[:30]]
    claims = [ClaimSeed(kind="table_cell", subject=s["plot_id"], field="fallow_streak_seasons", value=s["n_seasons"],
                        checks=["fallow_streak_recount"], context={"seasons_table": [r for r in rows if r["plot_id"] == s["plot_id"]],
                                                                   "min_seasons": a.min_seasons}) for s in streaks[:30]]
    claims.append(ClaimSeed(kind="kpi", subject="plots", field="n_fallow_streaks", value=len(streaks),
                            checks=["count_matches_rows"], context={"rows": len(streaks)}))
    return ToolResult(data={"streaks": streaks, "seasons": rows}, evidence=ev, n_rows=len(streaks), confidence=0.7,
                      caveats=cav, summary=f"{len(streaks)} fallow streaks >= {a.min_seasons} seasons", claims=claims)


def agri_tools() -> list[Tool]:
    return [
        Tool("crop_presence", "Per plot and season (kharif/rabi/summer, Jun-May ag-years): crop present / no crop / "
             "insufficient data, from Sentinel-2 season states.", PlotsIn, crop_presence, timeout_s=60),
        Tool("fallow_streak", "Runs of consecutive seasons without a crop per plot (>= min_seasons).", FallowIn,
             fallow_streak, timeout_s=60),
    ]
