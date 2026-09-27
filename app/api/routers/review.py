"""GET /review-queue, POST /review/{id} (phase4-agentic-core skill: "written back as a verified
fact with review_status=human_verified")."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query

from app.api.db import get_conn
from app.api.schemas import ReviewDecisionRequest, ReviewDecisionResponse, ReviewItem, ReviewListResponse

router = APIRouter(tags=["review"])

# extraction.review_status is CHECK-constrained to auto|auto_unverified|queued|approved|corrected|
# rejected (db/migrations/0007_extraction.sql, owned by doc-intel-engineer): the phase4 skill's
# "human_verified" wording doesn't match that enum, so decisions map onto the existing values
# 1:1 — a human decision always overrides the auto_* status either way.


@router.get("/review-queue", response_model=ReviewListResponse)
async def review_queue(status: str = "open", reason: str | None = None,
                       limit: int = Query(100, le=1000), offset: int = 0) -> ReviewListResponse:
    where, params = ["status = %s"], [status]
    if reason:
        where.append("reason = %s")
        params.append(reason)
    clause = " AND ".join(where)

    def _run():
        with get_conn() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM review_queue WHERE {clause}", params).fetchone()["n"]
            rows = conn.execute(
                f"SELECT id, content_hash, document_id, page_id, page_ref, extraction_id, reason, "
                f"detail, status, decided_value, decided_by, decided_at, created_at "
                f"FROM review_queue WHERE {clause} ORDER BY created_at LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
            return total, rows

    total, rows = await asyncio.to_thread(_run)
    return ReviewListResponse(items=[ReviewItem(**r) for r in rows], total=total)


# Values a reviewer may correct on a row read from the page (owner names are never editable here:
# they stay masked in the UI; bank-like fields are never stored).
EDITABLE_FIELDS = {"survey_no": str, "sub_div": str, "extent_ha": float, "extent_ac": float, "amount_rs": float,
                   "tree_amount_rs": float, "land_amount_rs": float, "patta_no": str, "classification": str}
ROW_SCHEMAS = ("parcel_row", "form_f", "payment_instrument", "village_totals", "price_rate")
FACT_TYPES = {"extent_ha", "extent_ac", "amount_rs", "patta_no", "classification"}


def _clean(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if k not in EDITABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"field {k!r} cannot be edited")
        if v in (None, ""):
            out[k] = None
            continue
        try:
            out[k] = EDITABLE_FIELDS[k](v) if EDITABLE_FIELDS[k] is float else str(v).strip()
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{k} must be a number") from None
    return out


@router.get("/review/{review_id}/rows")
async def review_rows(review_id: int) -> dict:
    """The table rows the AI read from this review item's page (the values a reviewer can confirm or correct)."""
    def _run():
        with get_conn() as conn:
            item = conn.execute("SELECT page_id, extraction_id FROM review_queue WHERE id = %s", (review_id,)).fetchone()
            if item is None:
                return None
            rows = conn.execute(
                "SELECT id, record_no, schema_name, row_json, confidence, review_status FROM extraction "
                "WHERE (page_id = %s OR id = %s) AND schema_name = ANY(%s) ORDER BY record_no",
                (item["page_id"], item["extraction_id"], list(ROW_SCHEMAS))).fetchall()
            return [{"extraction_id": r["id"], "record_type": r["schema_name"], "confidence": r["confidence"],
                     "review_status": r["review_status"],
                     "values": {k: (r["row_json"] or {}).get(k) for k in EDITABLE_FIELDS},
                     "ai_original": (r["row_json"] or {}).get("ai_original")} for r in rows]
    rows = await asyncio.to_thread(_run)
    if rows is None:
        raise HTTPException(status_code=404, detail="review item not found")
    return {"review_id": review_id, "editable_fields": list(EDITABLE_FIELDS), "rows": rows}


@router.post("/review/{review_id}", response_model=ReviewDecisionResponse)
async def decide_review(review_id: int, body: ReviewDecisionRequest) -> ReviewDecisionResponse:
    """corrected = SAVE the reviewer's value fixes on the edited rows (AI originals kept under
    row_json.ai_original); the page stays open. approved = FINISH the page: rows the reviewer corrected stay
    'corrected', every other row becomes 'approved'. rejected = the page is unreadable: all rows 'rejected'."""
    if body.decision == "corrected" and not (body.corrections or body.corrected_value):
        raise HTTPException(status_code=400, detail="a correction needs at least one changed value")
    fixes = {c.extraction_id: _clean(c.values) for c in body.corrections}

    def _run():
        with get_conn() as conn:
            item = conn.execute("SELECT id, page_id, extraction_id FROM review_queue WHERE id = %s",
                                (review_id,)).fetchone()
            if item is None:
                return None
            page_rows = {r["id"]: r["row_json"] or {} for r in conn.execute(
                "SELECT id, row_json FROM extraction WHERE (page_id = %s OR id = %s) AND schema_name = ANY(%s)",
                (item["page_id"], item["extraction_id"], list(ROW_SCHEMAS))).fetchall()}
            if body.corrected_value and item["extraction_id"] and not fixes:
                fixes[item["extraction_id"]] = _clean(body.corrected_value)
            unknown = set(fixes) - set(page_rows)
            if unknown:
                raise HTTPException(status_code=400, detail=f"rows {sorted(unknown)} are not on this page")
            current = {r["id"]: r["review_status"] for r in conn.execute(
                "SELECT id, review_status FROM extraction WHERE id = ANY(%s)", (list(page_rows),)).fetchall()}
            for eid, rj in page_rows.items():
                if body.decision == "corrected" and eid not in fixes:
                    continue                                   # saving corrections touches only the edited rows
                if body.decision == "rejected":
                    status = "rejected"
                elif eid in fixes:
                    status = "corrected"
                    original = dict(rj.get("ai_original") or {})
                    for k in fixes[eid]:
                        original.setdefault(k, rj.get(k))
                    conn.execute("UPDATE extraction SET row_json = row_json || %s::jsonb WHERE id = %s",
                                 (json.dumps({**fixes[eid], "ai_original": original}), eid))
                    for k, v in fixes[eid].items():     # keep the derived parcel facts in step
                        if k in FACT_TYPES:
                            col = "value_num" if isinstance(v, float) else "value_text"
                            conn.execute(f"UPDATE parcel_fact SET {col} = %s WHERE extraction_id = %s AND fact_type = %s",
                                         (v, eid, k))
                else:
                    status = "corrected" if current.get(eid) == "corrected" else "approved"   # keep saved corrections
                conn.execute("UPDATE extraction SET review_status = %s WHERE id = %s", (status, eid))
            prev = conn.execute("SELECT decided_value FROM review_queue WHERE id = %s", (review_id,)).fetchone()["decided_value"] or {}
            saved = {c["extraction_id"]: c["values"] for c in prev.get("corrections", [])}
            for k, v in fixes.items():
                saved[k] = {**saved.get(k, {}), **v}
            record = {"decision": body.decision, "rows_on_page": len(page_rows),
                      "corrections": [{"extraction_id": k, "values": v} for k, v in saved.items()]}
            if body.decision == "corrected":
                # step 1: corrections are saved on the rows, but the page stays OPEN until someone approves it
                conn.execute("UPDATE review_queue SET decided_value = %s WHERE id = %s",
                             (json.dumps({**record, "pending_approval": True}), review_id))
                state = "open"
            else:
                conn.execute("UPDATE review_queue SET status = 'decided', decided_value = %s, decided_by = %s, "
                             "decided_at = now() WHERE id = %s", (json.dumps(record), body.decided_by, review_id))
                state = "decided"
            conn.commit()
            return item["extraction_id"], body.decision, state

    result = await asyncio.to_thread(_run)
    if result is None:
        raise HTTPException(status_code=404, detail="review item not found")
    extraction_id, review_status, state = result
    return ReviewDecisionResponse(id=review_id, status=state, extraction_id=extraction_id,
                                  review_status=review_status)


@router.get("/review-queue/facets")
async def review_facets(status: str = "open") -> dict:
    """Reasons present in the review queue (with counts), live from the database, for the reason dropdown."""
    def _run():
        with get_conn() as conn:
            rows = conn.execute("SELECT reason AS v, count(*) AS n FROM review_queue WHERE status = %s "
                                "GROUP BY 1 ORDER BY 2 DESC, 1", (status,)).fetchall()
            return {"reason": [{"value": r["v"], "count": r["n"]} for r in rows]}
    return await asyncio.to_thread(_run)
