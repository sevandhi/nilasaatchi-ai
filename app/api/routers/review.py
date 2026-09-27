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


@router.post("/review/{review_id}", response_model=ReviewDecisionResponse)
async def decide_review(review_id: int, body: ReviewDecisionRequest) -> ReviewDecisionResponse:
    def _run():
        with get_conn() as conn:
            row = conn.execute("SELECT id, extraction_id, status FROM review_queue WHERE id = %s",
                              (review_id,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE review_queue SET status = 'decided', decided_value = %s, decided_by = %s, "
                "decided_at = now() WHERE id = %s",
                (json.dumps(body.corrected_value) if body.corrected_value else None, body.decided_by, review_id),
            )
            review_status = None
            if row["extraction_id"] is not None:
                review_status = body.decision  # approved | corrected | rejected (matches the CHECK constraint)
                if body.decision == "corrected" and body.corrected_value:
                    conn.execute(
                        "UPDATE extraction SET row_json = row_json || %s::jsonb, review_status = %s "
                        "WHERE id = %s", (json.dumps(body.corrected_value), review_status, row["extraction_id"]),
                    )
                else:
                    conn.execute("UPDATE extraction SET review_status = %s WHERE id = %s",
                                (review_status, row["extraction_id"]))
            conn.commit()
            return row["extraction_id"], review_status

    result = await asyncio.to_thread(_run)
    if result is None:
        raise HTTPException(status_code=404, detail="review item not found")
    extraction_id, review_status = result
    return ReviewDecisionResponse(id=review_id, status="decided", extraction_id=extraction_id,
                                  review_status=review_status)
