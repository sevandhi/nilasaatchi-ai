"""T5.3 findings rules — pure functions over plain dicts (loaders live in pipeline.findings.load).

Each rule returns a list of Finding. Thresholds come from config/thresholds.yaml (`findings:`,
`discrepancy.extent_mismatch_pct`). Areas are geodesic hectares (D-023), 4 dp.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date

from pipeline.match.core import load_thresholds

RULE_VERSION = "findings-v1"
_T = load_thresholds()
F = _T.get("findings", {})
EXTENT_PCT = float(_T.get("discrepancy", {}).get("extent_mismatch_pct", 5))

SAT_CAVEATS = ["single post-possession season (lead, needs field verification)",
               "10 m Sentinel-2 pixels mix neighbouring land cover at parcel edges",
               "NE-monsoon weed flush greens fallow land (D-035)",
               "clouds/haze and observation gaps"]
BLOCK_CAVEAT = "possession evidenced only by a block-level document (no survey number on it, D-052)"


@dataclass
class Finding:
    category: str
    key: str
    severity: str
    confidence: float
    title: str
    evidence_level: str
    parcel_uid: str | None = None
    village: str | None = None
    unit_id: int | None = None
    block_id: int | None = None
    metrics: dict = field(default_factory=dict)
    paper_evidence: list = field(default_factory=list)
    planet_evidence: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)

    def row(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(max(0.0, min(1.0, float(self.confidence))), 3)
        return d


def _sev(value: float, high: float, medium: float) -> str:
    return "high" if value >= high else "medium" if value >= medium else "low"


# --------------------------------------------------------------------------- PV3
def pv3_post_possession(parcels: list[dict]) -> list[Finding]:
    """parcels: {parcel_uid, village, possession_level ('parcel'|'block'), possession_date,
    possession_evidence[], vigour: did-row|None, plough: did-row|None}. did-row: {did, ci_lo, ci_hi,
    farmland_like_p, n_pre, n_post, event_date}."""
    fl_min = float(F.get("pv3", {}).get("farmland_like_min", 0.7))
    out = []
    for p in parcels:
        lvl = p.get("possession_level")
        if lvl not in ("parcel", "block"):
            continue
        vig, pl = p.get("vigour"), p.get("plough")
        cav = list(SAT_CAVEATS) + ([BLOCK_CAVEAT] if lvl == "block" else [])
        base = 0.7 if lvl == "parcel" else 0.5
        planet = {"did_vigour_z": vig, "did_plough_z": pl, "season_scope": F.get("pv3", {}).get("season_scope", "rabi")}
        if vig and vig.get("ci_hi") is not None and vig["ci_hi"] < 0:
            out.append(Finding("PV3_POST_POSSESSION_ACTIVITY", f"PV3|drop|{p['parcel_uid']}", "medium",
                               base, "Vigour dropped vs never-acquired controls after possession: possession likely "
                               "effected / land idled", lvl, p["parcel_uid"], p.get("village"),
                               metrics={"signal": "vigour_drop", "did": vig["did"], "ci": [vig["ci_lo"], vig["ci_hi"]],
                                        "possession_date": str(p.get("possession_date"))},
                               paper_evidence=p.get("possession_evidence", []), planet_evidence=planet, caveats=cav))
        # farmland_like_p is computed on the vigour row (D-045)
        fl = (pl or {}).get("farmland_like_p") or (vig or {}).get("farmland_like_p") or 0
        if pl and pl.get("ci_lo") is not None and pl["ci_lo"] > 0 and fl >= fl_min:
            out.append(Finding("PV3_POST_POSSESSION_ACTIVITY", f"PV3|farmed|{p['parcel_uid']}", "low",
                               base - 0.15, "Ploughing-like signal stronger than controls after possession while "
                               "farmland-like: possibly still farmed (lead)", lvl, p["parcel_uid"], p.get("village"),
                               metrics={"signal": "still_farmed_lead", "did": pl["did"], "ci": [pl["ci_lo"], pl["ci_hi"]],
                                        "farmland_like_p": fl,
                                        "possession_date": str(p.get("possession_date"))},
                               paper_evidence=p.get("possession_evidence", []), planet_evidence=planet, caveats=cav))
    return out


# --------------------------------------------------------------------------- PV4
def pv4_idle_land_bank(parcels: list[dict], today: date) -> list[Finding]:
    """parcels: {parcel_uid, village, unit_id, block_id, area_ha, possession_date, possession_level,
    states_since: [state...], dist_major_road_m, dist_substation_m}. One finding per block."""
    months = float(F.get("pv4", {}).get("min_months_since_possession", 6))
    blocks: dict = defaultdict(list)
    for p in parcels:
        d = p.get("possession_date")
        if not d or (today - d).days < months * 30.44:
            continue
        if "cleared_or_built" in (p.get("states_since") or []):
            continue
        blocks[(p["village"], p.get("unit_id"), p.get("block_id"))].append(p)
    out = []
    for (v, u, b), ps in sorted(blocks.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2] or 0)):
        ha = round(sum(float(p["area_ha"] or 0) for p in ps), 4)
        lvl = "parcel" if any(p["possession_level"] == "parcel" for p in ps) else "block"
        roads = [float(p["dist_major_road_m"]) for p in ps if p.get("dist_major_road_m") is not None]
        subs = [float(p["dist_substation_m"]) for p in ps if p.get("dist_substation_m") is not None]
        oldest = min(p["possession_date"] for p in ps)
        out.append(Finding(
            "PV4_IDLE_LAND_BANK", f"PV4|{v}|{u}|{b}", _sev(ha, 20, 5), 0.75 if lvl == "parcel" else 0.6,
            f"{ha:.4f} ha possessed ≥ {months:g} months ago with no clearing/construction detected", lvl,
            None, v, u, b,
            metrics={"idle_ha_sum": ha, "n_parcels": len(ps), "oldest_possession": str(oldest),
                     "months_since_oldest": round((today - oldest).days / 30.44, 1),
                     "min_major_road_km": round(min(roads) / 1000, 3) if roads else None,
                     "min_substation_km": round(min(subs) / 1000, 3) if subs else None,
                     "parcels": sorted(p["parcel_uid"] for p in ps)},
            paper_evidence=[e for p in ps[:5] for e in p.get("possession_evidence", [])[:1]],
            planet_evidence={"states_since_possession": dict(Counter(s for p in ps for s in p.get("states_since") or []))},
            caveats=["area is the geodesic sum of FMB parcels (overlaps double-count, D-023)",
                     "no cleared_or_built season state observed; construction smaller than a 10 m pixel is missed"]
                    + ([BLOCK_CAVEAT] if lvl == "block" else [])))
    return out


# --------------------------------------------------------------------------- PV1
def ag_year(d: date) -> int:
    return d.year if d.month >= 6 else d.year - 1


def pv1_classification(parcels: list[dict]) -> list[Finding]:
    """parcels: {parcel_uid, village, classification ('DRY'|'WET'|'PORAMBOKE'), t_3_1 date,
    seasons: [{ag_year, season, state}], evidence[]}."""
    cfg = F.get("pv1", {})
    need, look = int(cfg.get("min_irrigated_years", 2)), int(cfg.get("lookback_years", 3))
    out = []
    for p in parcels:
        t31 = p.get("t_3_1")
        if not t31:
            continue
        y = ag_year(t31)
        years = set(range(y - look, y))
        irr = sorted({s["ag_year"] for s in p["seasons"] if s["ag_year"] in years and s["state"] == "irrigated_multi"})
        crop = sorted({(s["ag_year"], s["season"]) for s in p["seasons"]
                       if s["ag_year"] in years and s["state"] in ("cropped", "irrigated_multi")})
        cls = p["classification"]
        hit, why = False, ""
        if cls == "DRY" and len(irr) >= need:
            hit, why = True, f"DRY land with irrigated_multi in {len(irr)} of {look} years before 3(1)"
        elif cls == "WET" and not irr:
            hit, why = True, f"WET land with no irrigated_multi in the {look} years before 3(1)"
        elif cls == "PORAMBOKE" and len(crop) >= 2:
            hit, why = True, f"Poramboke with a crop in {len(crop)} seasons before 3(1) (encroachment signal)"
        if hit:
            out.append(Finding("PV1_CLASSIFICATION_CONFLICT", f"PV1|{p['parcel_uid']}",
                               "medium" if len(irr) >= look else "low", 0.55, why, "parcel", p["parcel_uid"], p.get("village"),
                               metrics={"classification": cls, "irrigated_years": irr, "t_3_1": str(t31)},
                               paper_evidence=p.get("evidence", []),
                               planet_evidence={"seasons": [s for s in p["seasons"] if s["ag_year"] in years]},
                               caveats=["classification wording in the award may follow the revenue record, not current use",
                                        "irrigated_multi is a model state (student/teacher, P3)", SAT_CAVEATS[1]]))
    return out


# --------------------------------------------------------------------------- EXTENT
def extent_mismatch(parcels: list[dict]) -> list[Finding]:
    """parcels: {parcel_uid, village, area_ha, claims: [{extent_ha, extraction_id, document_id, page_no,
    part, match_score}]}. Uses the claim (or per-document sum of co-owner rows) closest to the GIS area;
    a finding only when none is within the threshold."""
    ratio_err = float(F.get("extent", {}).get("unit_error_ratio", 10))
    out = []
    for p in parcels:
        area = float(p["area_ha"] or 0)
        claims = [c for c in p["claims"] if c.get("extent_ha") and not c.get("part")]
        if area <= 0 or not claims:
            continue
        per_doc = defaultdict(float)
        for c in claims:
            per_doc[c["document_id"]] += float(c["extent_ha"])
        options = [(float(c["extent_ha"]), "row", c) for c in claims] + \
                  [(v, "doc_sum", {"document_id": d}) for d, v in per_doc.items()]
        best = min(options, key=lambda o: abs(o[0] - area))
        pct = 100 * abs(best[0] - area) / area
        if pct <= EXTENT_PCT:
            continue
        r = best[0] / area
        ev = [{k: c.get(k) for k in ("extraction_id", "document_id", "page_no", "extent_ha")} for c in claims[:10]]
        score = min((c.get("match_score") or 1.0) for c in claims)
        if r > ratio_err or r < 1 / ratio_err:
            out.append(Finding("EXTRACTION_ERROR", f"EXTR|extent|{p['parcel_uid']}", "low", 0.8,
                               f"Document extent {best[0]:.4f} ha is {r:.1f}x the GIS area {area:.4f} ha: likely a unit/OCR error",
                               "parcel", p["parcel_uid"], p.get("village"),
                               metrics={"doc_ha": round(best[0], 4), "gis_ha": area, "ratio": round(r, 2)},
                               paper_evidence=ev, caveats=["review the extraction before any land finding"]))
            continue
        diff = round(best[0] - area, 4)
        out.append(Finding("EXTENT_MISMATCH", f"EXTENT|{p['parcel_uid']}",
                           "high" if abs(diff) >= 1 or pct >= 50 else "medium" if abs(diff) >= 0.2 else "low",
                           round(0.85 * score, 3),
                           f"Document extent {best[0]:.4f} ha vs GIS {area:.4f} ha ({diff:+.4f} ha, {pct:.1f}%)",
                           "parcel", p["parcel_uid"], p.get("village"),
                           metrics={"doc_ha": round(best[0], 4), "gis_ha": area, "diff_ha": diff, "pct": round(pct, 2),
                                    "basis": best[1], "n_claims": len(claims)},
                           paper_evidence=ev,
                           caveats=["FMB polygons are digitised sketches; overlaps and slivers affect area (D-023)",
                                    "co-owner rows may each print a share; the closest of row / document-sum is used"]))
    return out


# --------------------------------------------------------------------------- COMPENSATION
def document_rate(rows: list[dict]) -> float | None:
    """Modal ₹/acre across a document's rows (rounded to ₹1,000); None unless enough rows agree."""
    cfg = F.get("compensation", {})
    agree, min_rows = float(cfg.get("rate_agreement", 0.005)), int(cfg.get("min_rows_for_rate", 3))
    rates = [r["land_amount_rs"] / r["extent_ac"] for r in rows if r.get("extent_ac") and r.get("land_amount_rs")]
    if len(rates) < min_rows:
        return None
    mode, _ = Counter(round(x, -3) for x in rates).most_common(1)[0]
    if mode <= 0:
        return None
    support = sum(abs(x - mode) / mode <= agree for x in rates)
    return float(mode) if support >= max(min_rows, len(rates) / 2) else None


def compensation_mismatch(docs: dict[int, list[dict]]) -> list[Finding]:
    """docs: document_id -> rows {extraction_id, page_no, extent_ac, land_amount_rs, parcel_uid, village}.
    Expected = extent_ac × document rate. Tolerance: ±₹tolerance_rs plus the rounding of a printed
    2-dp acre figure (0.005 ac × rate)."""
    tol_rs = float(F.get("compensation", {}).get("tolerance_rs", 1))
    out = []
    for doc_id, rows in docs.items():
        rate = document_rate(rows)
        if not rate:
            continue
        for r in rows:
            ac, amt = r.get("extent_ac"), r.get("land_amount_rs")
            if not ac or not amt:
                continue
            exp = ac * rate
            tol = tol_rs + 0.005 * rate
            diff = amt - exp
            if abs(diff) <= tol:
                continue
            if amt / exp > 10 or amt / exp < 0.1:
                out.append(Finding("EXTRACTION_ERROR", f"EXTR|amount|{r['extraction_id']}", "low", 0.8,
                                   f"Amount ₹{amt:,.0f} is {amt / exp:.2g}x of {ac} ac × ₹{rate:,.0f}/ac: likely a misread amount/extent",
                                   "parcel" if r.get("parcel_uid") else "block", r.get("parcel_uid"), r.get("village"),
                                   metrics={"amount_rs": amt, "extent_ac": ac, "rate_per_ac": rate},
                                   paper_evidence=[{"extraction_id": r["extraction_id"], "document_id": doc_id, "page_no": r.get("page_no")}],
                                   caveats=["review the extraction before any compensation finding"]))
                continue
            out.append(Finding("COMPENSATION_MISMATCH", f"COMP|{r['extraction_id']}",
                               _sev(abs(diff), 100_000, 10_000), 0.7 if r.get("parcel_uid") else 0.55,
                               f"Amount ₹{amt:,.0f} ≠ {ac} ac × ₹{rate:,.0f}/ac = ₹{exp:,.0f} ({diff:+,.0f})",
                               "parcel" if r.get("parcel_uid") else "block", r.get("parcel_uid"), r.get("village"),
                               metrics={"amount_rs": amt, "extent_ac": ac, "rate_per_ac": rate, "expected_rs": round(exp),
                                        "diff_rs": round(diff), "rate_source": f"modal rate of document {doc_id}"},
                               paper_evidence=[{"extraction_id": r["extraction_id"], "document_id": doc_id, "page_no": r.get("page_no")}],
                               caveats=["rate inferred as the modal ₹/acre of the same document's rows",
                                        "rows may include tree/structure compensation not itemised",
                                        "the document's own arithmetic may be wrong (§11b)"]))
    return out


# --------------------------------------------------------------------------- DOC VERSION
def doc_version_conflict(sources: list[dict], fmb_union: dict[str, float]) -> list[Finding]:
    """sources: {village, total_ha, doc_type, document_id, page_no, path, extraction_id} from
    scheme-wide village tables. One finding per village whose sources disagree; GO rows are the
    sanction, other rows are labelled by document type and file/page."""
    cfg = F.get("doc_version", {})
    tol = float(cfg.get("tolerance_ha", 0.01))
    min_v = int(cfg.get("min_villages_on_page", 5))
    go = {s["village"]: s["total_ha"] for s in sources if s["doc_type"] == "GO"}
    near = Counter((s["document_id"], s["page_no"]) for s in sources
                   if s["village"] in go and abs(s["total_ha"] - go[s["village"]]) <= 0.05 * go[s["village"]])
    sources = [s for s in sources if near[(s["document_id"], s["page_no"])] >= min_v]
    byv: dict = defaultdict(list)
    for s in sources:
        byv[s["village"]].append(s)
    out = []
    for v, ss in sorted(byv.items()):
        sanction = [s for s in ss if s["doc_type"] == "GO"]
        ref = sanction[0]["total_ha"] if sanction else None
        vals = sorted({round(s["total_ha"], 4) for s in ss})
        if len(vals) < 2 or vals[-1] - vals[0] <= tol:
            continue
        spread = round(vals[-1] - vals[0], 4)
        diffs = [{"source": f"{s['path'].rsplit('/', 1)[-1]} p{s['page_no']}",
                  "role": "sanction (GO)" if s["doc_type"] == "GO" else f"{s.get('folder') or s['doc_type']} (page type {s['doc_type']})",
                  "total_ha": round(s["total_ha"], 4),
                  "vs_sanction_ha": round(s["total_ha"] - ref, 4) if ref is not None else None} for s in ss]
        out.append(Finding("DOC_VERSION_CONFLICT", f"DOCVER|{v}", "high" if spread >= 1 else "medium", 0.8,
                           f"{v}: village totals disagree across documents (spread {spread:.4f} ha)", "village",
                           None, v, metrics={"sanction_ha": ref, "values_ha": vals, "spread_ha": spread,
                                             "fmb_union_ha": fmb_union.get(v), "sources": diffs},
                           paper_evidence=[{"extraction_id": s["extraction_id"], "document_id": s["document_id"],
                                            "page_no": s["page_no"]} for s in ss],
                           caveats=["a proposal page is not a sanction: read each source's role before concluding",
                                    "hectare notation parsed from OCR/VLM (e.g. 106.01.01 vs 106.01.00)"]))
    return out


# --------------------------------------------------------------------------- FMB QUALITY
def fmb_quality(overlaps: list[dict], outside: list[dict]) -> list[Finding]:
    mn = float(F.get("fmb_quality", {}).get("min_overlap_ha", 0.001))
    out = []
    for o in overlaps:
        ha = float(o["overlap_ha"])
        if ha < mn:
            continue
        out.append(Finding("FMB_QUALITY", f"FMB|overlap|{o['uid_a']}|{o['uid_b']}", _sev(ha, 0.25, 0.05), 0.95,
                           f"FMB parcels {o['uid_a']} and {o['uid_b']} overlap by {ha:.4f} ha"
                           + (" (across a village line)" if o["cross_village"] else ""), "fmb", o["uid_a"], o["village_a"],
                           metrics={"issue": "OVERLAP", "overlap_ha": ha, "other_parcel_uid": o["uid_b"],
                                    "cross_village": o["cross_village"]},
                           caveats=["geodesic area of the geometric intersection (D-023)"]))
    for o in outside:
        ha = float(o["outside_ha"])
        out.append(Finding("FMB_QUALITY", f"FMB|outside|{o['uid']}", _sev(ha, 0.25, 0.05), 0.9,
                           f"FMB parcel {o['uid']} extends {ha:.4f} ha ({float(o['outside_pct']):.1f}%) beyond cadastral survey {o['survey_no']}",
                           "fmb", o["uid"], o["village"],
                           metrics={"issue": "OUTSIDE_SURVEY", "outside_ha": ha, "outside_pct": float(o["outside_pct"])},
                           caveats=["the cadastral layer is at survey level and has its own digitising error"]))
    return out
