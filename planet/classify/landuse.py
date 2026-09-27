"""Runtime tool ``landuse_state`` (T3.4): the stored student state with on-demand VLM second opinion.

Routing (phase3 skill + D-031), evaluated per call:
1. obs rule failed                          -> ``insufficient_data`` (route ``rule``)
2. p_state >= tau_accept (0.75, after caps) -> accept the student (route ``student``)
3. otherwise a VLM second opinion on the PUBLIC panel (router task ``satellite_second_opinion``; a stored
   one is reused): agrees -> route ``vlm_second_opinion``; disagrees -> route ``disagreement`` and
   ``needs_field_verification``. If no VLM is available -> ``needs_field_verification`` (router terminal).
The route and the router step id are written back to ``parcel_season`` and ``planet_teacher_label``.

Every answer is a signal needing field verification and carries its caveats and evidence (chip scene ids).
"""
from __future__ import annotations

from typing import Any

from planet.classify import student as st
from planet.classify import teacher as te

# Models known NOT to receive images through the router; their answers are never used as a visual opinion.
# (Empty: the 2026-09-26 suspicion about cohere-command-a-vision was wrong - its image tokens are reported
# separately from tokens_in; the doctor proves vision, D-036b.)
IMAGE_BLIND_MODELS: frozenset[str] = frozenset()


def _row(conn: Any, uid: str, ag_year: int, season: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT state, p_state, route, needs_field, classify_meta, features->>'data_sufficient', "
                     "features->>'gap_flag', teacher_label, teacher_agreement FROM parcel_season "
                     "WHERE parcel_uid = %s AND ag_year = %s AND season = %s", (uid, ag_year, season)).fetchone()
    if r is None:
        return None
    keys = ["state", "p_state", "route", "needs_field", "classify_meta", "data_sufficient", "gap_flag",
            "teacher_label", "teacher_agreement"]
    return dict(zip(keys, r, strict=True))


def landuse_state(parcel_uid: str, ag_year: int, season: str, *, conn: Any = None, call_vlm: bool = True,
                  tau: float = st.TAU_ACCEPT) -> dict[str, Any]:
    """State of one parcel-season with evidence. ``call_vlm=False`` never spends a model call."""
    import psycopg

    from planet.classify import panel as pn
    from planet.extract.run import dsn

    own = conn is None
    conn = conn or psycopg.connect(dsn())
    try:
        row = _row(conn, parcel_uid, int(ag_year), season)
        if row is None or row["state"] is None:
            raise LookupError(f"no classified parcel_season for {parcel_uid} {ag_year} {season}")
        meta = row["classify_meta"] or {}
        out: dict[str, Any] = {"parcel_uid": parcel_uid, "ag_year": int(ag_year), "season": season,
                               "student_state": row["state"], "p_state": row["p_state"],
                               "teacher_label": row["teacher_label"], "teacher_agreement": row["teacher_agreement"],
                               "caveats": meta.get("caveats", []), "model_version": st.MODEL_VERSION}
        if row["state"] == "insufficient_data" or season == "annual":
            return {**out, "state": row["state"], "route": row["route"], "needs_field_verification": row["needs_field"]}
        if row["p_state"] is not None and row["p_state"] >= tau:
            return {**out, "state": row["state"], "route": "student", "needs_field_verification": False}
        item = te.item_id(parcel_uid, ag_year, season)
        so = conn.execute("SELECT state, model_id, confidence, rationale, provenance FROM planet_teacher_label "
                          "WHERE item_id = %s AND ok AND teacher IN ('second_opinion', 'cohere') ORDER BY teacher DESC LIMIT 1",
                          (item,)).fetchone()
        evidence = None
        if so is None and call_vlm:
            ctx = pn.Context.load(conn)
            pm = pn.render_panel(ctx, parcel_uid, int(ag_year), season,
                                 pn.PANEL_DIR / "items" / f"{pn.cr.safe_name(item)}.png")
            res = _second_opinion(item, parcel_uid, int(ag_year), season, pm)
            te.upsert_label(conn, res)
            if res["ok"]:
                so = (res["state"], res["model_id"], res["confidence"], res["rationale"], res["provenance"])
            evidence = pm["chips"]
        if so is None and not call_vlm:  # dry run: report, never persist a route without an opinion
            return {**out, "state": row["state"], "route": "pending_second_opinion",
                    "needs_field_verification": False, "second_opinion": None, "evidence": None}
        if so is None:
            route, state, needs = "needs_field_verification", row["state"], True
        elif so[0] == row["state"]:
            route, state, needs = "vlm_second_opinion", row["state"], False
        else:
            route, state, needs = "disagreement", row["state"], True
        conn.execute("UPDATE parcel_season SET route = %s, needs_field = %s, classify_meta = classify_meta || "
                     "jsonb_build_object('second_opinion', %s::jsonb) WHERE parcel_uid = %s AND ag_year = %s AND season = %s",
                     (route, needs, _json({"state": so[0], "model_id": so[1], "confidence": so[2]} if so else None),
                      parcel_uid, int(ag_year), season))
        conn.commit()
        return {**out, "state": state, "route": route, "needs_field_verification": needs,
                "second_opinion": ({"state": so[0], "model_id": so[1], "confidence": so[2], "rationale": so[3]}
                                   if so else None),
                "evidence": evidence or ((so[4] or {}).get("chips") if so else None)}
    finally:
        if own:
            conn.close()


def _json(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


def _second_opinion(item: str, uid: str, ag_year: int, season: str, pm: dict[str, Any]) -> dict[str, Any]:
    """Unpinned router task ``satellite_second_opinion`` (runtime path; the router picks the model)."""
    from pathlib import Path

    from app.router import call, vendor_of

    raw = te.PROMPT_PATH.read_text()
    system = te.prompt_text(raw)

    res = call("satellite_second_opinion", {"system": system, "prompt": te.USER_MSG, "max_tokens": 500,
                                            "temperature": 0, "step_id": f"so-{abs(hash(item)) % 10**10}"},
               schema=te.SCHEMA, privacy_tier="PUBLIC", images=[Path(pm["path"]).read_bytes()])
    base = {"item_id": item, "parcel_uid": uid, "ag_year": ag_year, "season": season, "teacher": "second_opinion",
            "task": "satellite_second_opinion", "model_id": res.model_id,
            "resolved_model": (res.route_log or {}).get("resolved_model"), "prompt_version": te.prompt_version(raw),
            "panel_sha256": pm["sha256"], "panel_path": pm["path"],
            "provenance": {"chips": pm["chips"], "step_id": (res.route_log or {}).get("step_id"),
                           "latency_ms": res.latency_ms, "vendor": vendor_of(res.model_id) if res.model_id else None}}
    if not res.ok:
        return {**base, "ok": False, "error": str(res.error)[:1000]}
    if res.model_id in IMAGE_BLIND_MODELS:
        return {**base, "ok": False, "error": f"{res.model_id} does not receive images via the router (IMAGE_BLIND_MODELS)"}
    try:
        return {**base, **te.parse_answer(res.data), "ok": True}
    except (ValueError, TypeError) as exc:
        return {**base, "ok": False, "error": f"bad answer: {exc}"}
