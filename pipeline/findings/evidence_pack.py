"""T5.5 evidence_pack(finding_id) -> JSON-serialisable dict.

    python -m pipeline.findings.evidence_pack <finding_id> [--render-chips]

Paper: each cited extraction row with document path, page, bbox (+ method), confidence and the
row values with owner fields masked (§13). Planet: parcel_did DiD rows, season states, and chip
paths (cached chips from planet.chips; --render-chips renders pre/post-possession chips).
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from dotenv import load_dotenv

MASK = "[masked]"


def mask_owner(obj: Any) -> Any:
    """Recursively replace any value whose key mentions owner / account with a mask."""
    if isinstance(obj, dict):
        return {k: (MASK if any(t in k.lower() for t in ("owner", "account", "heir")) else mask_owner(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_owner(v) for v in obj]
    return obj


def _one(conn, sql, params):
    cur = conn.execute(sql, params)
    r = cur.fetchone()
    return dict(zip([d.name for d in cur.description], r)) if r else None


def _all(conn, sql, params):
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def paper_item(conn, ref: dict) -> dict:
    ext_id = ref.get("extraction_id")
    if not ext_id and ref.get("event_id"):
        ev = _one(conn, "SELECT extraction_id, document_id, stage, event_date FROM acquisition_event WHERE id = %s",
                  (ref["event_id"],))
        ext_id = ev and ev["extraction_id"]
        if not ext_id and ev:
            doc = _one(conn, "SELECT path, folder_label FROM document WHERE id = %s", (ev["document_id"],)) if ev["document_id"] else None
            return {**ref, "stage": ev["stage"], "event_date": str(ev["event_date"]), "document": doc,
                    "note": "document-level event (no row extraction)"}
    if not ext_id:
        return ref
    r = _one(conn, """SELECT e.id, e.doc_type, e.page_no, e.bbox, e.bbox_method, e.confidence, e.self_consistency,
                             e.review_status, e.row_json, d.path, d.folder_label
                      FROM extraction e LEFT JOIN document d ON d.id = e.document_id WHERE e.id = %s""", (ext_id,))
    if not r:
        return {**ref, "missing": True}
    row = dict(r["row_json"] or {})
    row.pop("evidence", None)
    return {**ref, "extraction_id": r["id"], "doc_type": r["doc_type"], "document": r["path"], "folder": r["folder_label"],
            "page_no": r["page_no"], "bbox": r["bbox"], "bbox_method": r["bbox_method"],
            "confidence": r["confidence"], "self_consistency": r["self_consistency"], "review_status": r["review_status"],
            "values": mask_owner(row)}


def chip_refs(uid: str, dates: list[str], render: bool) -> dict:
    try:
        from planet.chips.render import CHIP_DIR, safe_name
    except Exception as e:  # pragma: no cover - optional heavy deps
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    d = CHIP_DIR / safe_name(uid)
    out = {"available": True, "cached": sorted(str(p) for p in d.glob("*.png")) if d.exists() else [], "rendered": []}
    if render:
        from planet.chips.render import satellite_chip
        for day in dates:
            try:
                out["rendered"].append(satellite_chip(uid, day))
            except Exception as e:  # noqa: BLE001 - report, never fail the pack
                out["rendered"].append({"date": day, "error": f"{type(e).__name__}: {e}"})
    return out


def evidence_pack(finding_id: int, conn=None, render_chips: bool = False) -> dict:
    own = conn is None
    if own:
        import psycopg
        load_dotenv()
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        f = _one(conn, "SELECT * FROM finding WHERE id = %s", (finding_id,))
        if not f:
            raise KeyError(f"finding {finding_id} not found")
        paper = [paper_item(conn, ref) for ref in (f["paper_evidence"] or [])[:10]]
        planet: dict = dict(f["planet_evidence"] or {})
        uid = f["parcel_uid"]
        if uid:
            planet["parcel_did"] = _all(conn, """SELECT event, metric, season_scope, event_date::text, n_pre, n_post, did,
                                                        ci_lo, ci_hi, farmland_like_p FROM parcel_did WHERE parcel_uid = %s
                                                 ORDER BY event, metric, season_scope""", (uid,))
            planet["season_states"] = _all(conn, """SELECT ag_year, season, state, p_state FROM parcel_season
                                                    WHERE parcel_uid = %s ORDER BY ag_year, season""", (uid,))
            pd_ = (f["metrics"] or {}).get("possession_date")
            dates = []
            if pd_ and pd_ != "None":
                from datetime import date, timedelta
                d0 = date.fromisoformat(pd_)
                dates = [str(d0 - timedelta(days=120)), str(d0), str(d0 + timedelta(days=240))]
            planet["chips"] = chip_refs(uid, dates, render_chips)
            planet["parcel"] = _one(conn, """SELECT parcel_uid, kide, unit_id, block_id, area_ha_gis,
                                                    ST_AsGeoJSON(geom, 6)::json AS geometry FROM parcel WHERE parcel_uid = %s""", (uid,))
        return {"finding_id": f["id"], "finding_key": f["finding_key"], "category": f["category"],
                "severity": f["severity"], "title": f["title"], "parcel_uid": uid, "village": f["village"],
                "unit_id": f["unit_id"], "block_id": f["block_id"], "evidence_level": f["evidence_level"],
                "metrics": f["metrics"], "paper": paper, "planet": planet, "verdict": f["verdict"],
                "confidence": f["confidence"], "caveats": f["caveats"], "rule_version": f["rule_version"],
                "status": f["status"]}
    finally:
        if own:
            conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("finding_id", type=int)
    ap.add_argument("--render-chips", action="store_true")
    a = ap.parse_args()
    print(json.dumps(evidence_pack(a.finding_id, render_chips=a.render_chips), indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
