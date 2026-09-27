"""Generate tests/fixtures/kg_small.sql — the P4 fixture knowledge graph.

What is real and what is synthetic:
  * REAL (copied from the local KG; public data, no personal data): 7 village rows, 40 FMB parcels
    (Melathattaparai block 2 + Ramasamypuram blocks 1-2) with geometry and precomputed distances, their
    cadastral surveys, the reference layers near them, Sentinel-2 scenes/observations (best scene per
    month), parcel_season states and parcel_event_window rows.
  * SYNTHETIC (deterministic, seed 42): documents, pages, extractions, owners (fake names), parcel facts,
    acquisition events, review-queue rows and parcel_did rows. Seeded defects exercise the verifier:
    a compensation mismatch, an extent mismatch, a stage-order violation, an exemption conflict, a stalled
    award and an amount-in-words disagreement.

Run:  uv run python -m eval.agent.make_kg_small     (needs the real KG in DATABASE_URL)
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "fixtures" / "kg_small.sql"

MELA_UIDS_SQL = """
SELECT p.parcel_uid FROM parcel p JOIN village v ON v.id = p.village_id
WHERE v.name = 'Melathattaparai' AND p.block_id = 2 ORDER BY p.parcel_uid"""
RAMA_UIDS_SQL = """
(SELECT p.parcel_uid FROM parcel p JOIN village v ON v.id = p.village_id
 WHERE v.name = 'Ramasamypuram' AND p.block_id = 1 ORDER BY p.parcel_uid)
UNION ALL
(SELECT p.parcel_uid FROM parcel p JOIN village v ON v.id = p.village_id
 WHERE v.name = 'Ramasamypuram' AND p.block_id = 2 ORDER BY p.parcel_uid LIMIT 5)"""

FAKE_OWNERS_EN = ["Arumugam", "Selvaraj", "Muthulakshmi", "Pandiyan", "Karuppasamy", "Valliammal",
                  "Subbiah", "Ponnammal", "Ramasubramanian", "Chellathai", "Mariappan", "Santhanam",
                  "Irulappan", "Gomathi", "Veerabhadran", "Kaliammal", "Sudalaimani", "Esakkiammal",
                  "Balasundaram", "Petchiammal"]
FAKE_OWNERS_TA = ["ஆறுமுகம்", "செல்வராஜ்", "முத்துலட்சுமி", "பாண்டியன்", "கருப்பசாமி", "வள்ளியம்மாள்",
                  "சுப்பையா", "பொன்னம்மாள்", "இராமசுப்பிரமணியன்", "செல்லத்தாய்", "மாரியப்பன்", "சந்தானம்",
                  "இருளப்பன்", "கோமதி", "வீரபத்திரன்", "காளியம்மாள்", "சுடலைமணி", "இசக்கியம்மாள்",
                  "பாலசுந்தரம்", "பேச்சியம்மாள்"]
FATHERS_EN = ["Ganesan", "Murugan", "Sankaran", "Periyasamy", "Kandasamy"]
RATE_BY_BLOCK = {("Melathattaparai", 2): 500000, ("Ramasamypuram", 1): 900000, ("Ramasamypuram", 2): 700000}


_TYPES: dict[str, dict[str, str]] = {}


def col_types(conn, table: str) -> dict[str, str]:
    if table not in _TYPES:
        _TYPES[table] = {r[0]: r[1] for r in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name=%s", (table,))}
    return _TYPES[table]


def lit(conn, v, dtype: str = "") -> str:
    if dtype in ("jsonb", "json") and v is not None:
        return sql.Literal(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)).as_string(conn) + "::jsonb"
    return sql.Literal(v).as_string(conn)


def dump(conn, out: list[str], table: str, where: str, params=(), exclude=(), geom_cols=("geom",),
         order: str = "1", expr: dict | None = None, batch: int = 250) -> int:
    """Multi-row INSERTs of `table` rows matching `where`; `expr` overrides a column's SELECT expression."""
    expr = expr or {}
    cols = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
        "AND is_generated='NEVER' ORDER BY ordinal_position", (table,)) if r[0] not in exclude]
    sel = ", ".join(expr.get(c) or (f"ST_AsEWKT({c})" if c in geom_cols else c) for c in cols)
    rows = conn.execute(f"SELECT {sel} FROM {table} WHERE {where} ORDER BY {order}", params).fetchall()
    types = col_types(conn, table)
    tuples = []
    for r in rows:
        vals = []
        for c, v in zip(cols, r, strict=True):
            vals.append(f"ST_GeomFromEWKT({lit(conn, v)})" if c in geom_cols and v is not None
                        else lit(conn, v, types[c]))
        tuples.append(f"({', '.join(vals)})")
    for i in range(0, len(tuples), batch):
        out.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES\n" + ",\n".join(tuples[i:i + batch]) + ";")
    return len(rows)


def ins(conn, table: str, row: dict) -> str:
    types = col_types(conn, table)
    return (f"INSERT INTO {table} ({', '.join(row)}) VALUES "
            f"({', '.join(lit(conn, v, types[k]) for k, v in row.items())});")


def main() -> None:
    load_dotenv(ROOT / ".env")
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    rng = random.Random(42)
    mela = [r[0] for r in conn.execute(MELA_UIDS_SQL)]
    mela += [r[0] for r in conn.execute(
        "SELECT p.parcel_uid FROM parcel p JOIN village v ON v.id=p.village_id WHERE v.name='Melathattaparai' "
        "AND p.block_id=1 ORDER BY p.parcel_uid LIMIT %s", (20 - len(mela),))]
    rama = [r[0] for r in conn.execute(RAMA_UIDS_SQL)]
    uids = mela + rama
    assert len(uids) == 40, len(uids)
    out: list[str] = ["-- kg_small.sql: P4 fixture KG (generated by eval/agent/make_kg_small.py; do not edit).",
                      "-- Real public GIS/satellite rows for 40 parcels + SYNTHETIC documents/facts/owners.",
                      "SET client_min_messages = warning;", "BEGIN;"]
    n = {}
    n["village"] = dump(conn, out, "village", "true", order="id")
    n["parcel"] = dump(conn, out, "parcel", "parcel_uid = ANY(%s)", (uids,), exclude=("loaded_at",),
                       order="parcel_uid")
    n["survey"] = dump(conn, out, "survey", "(village_id, survey_no) IN (SELECT village_id, survey_no FROM parcel "
                       "WHERE parcel_uid = ANY(%s))", (uids,), exclude=("loaded_at",), order="1,2")
    env = "(SELECT ST_Buffer(ST_Collect(geom_utm), 6000) g FROM parcel WHERE parcel_uid = ANY(%s))"
    for t in ("substations", "waterbodies", "rail_stations", "park_boundary", "sipcot_parks", "airport",
              "seaport", "schools"):
        where = f"ST_DWithin(geom_utm, {env}, 15000)" if t in ("waterbodies", "schools") else "true"
        n[t] = dump(conn, out, f"ref_layer_{t}", where, (uids,) if "%s" in where else (), order="id")
    n["roads"] = dump(conn, out, "ref_layer_roads", f"(is_major OR fclass IN ('tertiary','unclassified')) AND "
                      f"ST_Intersects(geom_utm, {env})", (uids,), order="id")
    n["rail"] = dump(conn, out, "ref_layer_rail", f"ST_DWithin(geom_utm, {env}, 4000)", (uids,), order="id")
    # best scene per month (highest mean valid_frac over the chosen parcels)
    scenes = [r[0] for r in conn.execute("""
        SELECT DISTINCT ON (date_trunc('month', o.date)) o.scene_id
        FROM parcel_obs o WHERE o.parcel_uid = ANY(%s)
        GROUP BY o.scene_id, o.date HAVING avg(o.valid_frac) >= 0.5
        ORDER BY date_trunc('month', o.date), avg(o.valid_frac) DESC, o.scene_id""", (uids,))]
    n["s2_scene"] = dump(conn, out, "s2_scene", "id = ANY(%s)", (scenes,), order="id",
                         expr={"hrefs": "'{}'::jsonb"})
    n["parcel_obs"] = dump(conn, out, "parcel_obs", "parcel_uid = ANY(%s) AND scene_id = ANY(%s)", (uids, scenes),
                           exclude=("id",), order="parcel_uid, date")
    n["parcel_season"] = dump(conn, out, "parcel_season", "parcel_uid = ANY(%s)", (uids,), exclude=("id",),
                              order="parcel_uid, ag_year, season",
                              expr={"features": "(SELECT jsonb_object_agg(k, features->k) FROM unnest(ARRAY['n_obs', 'amplitude', 'integral', 'peak_doy', 'ndvi_max', 'ndvi_min', 'dry_mean', 'bsi_dry_mean', 'gap_flag', 'max_gap_days', 'data_sufficient', 'mixed_pixel', 'yoy_delta', 'peaks_count']) k "
                                                "WHERE features ? k)",
                                    "classify_meta": "jsonb_build_object('probs', classify_meta->'probs')"})
    n["parcel_event_window"] = dump(conn, out, "parcel_event_window", "parcel_uid = ANY(%s)", (uids,),
                                    order="parcel_uid, window_name")

    # ----------------------------------------------------------------- synthetic paper side
    info = {r[0]: {"village": r[1], "survey_no": r[2], "sub_div": r[3], "block": r[4], "unit": r[5],
                   "area": float(r[6])}
            for r in conn.execute("SELECT p.parcel_uid, v.name, p.survey_no, p.sub_div, p.block_id, p.unit_id, "
                                  "p.area_ha_gis FROM parcel p JOIN village v ON v.id=p.village_id "
                                  "WHERE parcel_uid = ANY(%s) ORDER BY 1", (uids,))}
    docs = []

    def add_doc(did, dtype, stage, village, block, unit, ddate, pages_text, folder):
        sha = hashlib.sha256(f"fixture-doc-{did}".encode()).hexdigest()
        docs.append(did)
        out.append(ins(conn, "document", {
            "id": did, "sha256": sha, "md5": sha[:32], "dup_group_id": sha, "bytes": 100000 + did,
            "pages": len(pages_text), "folder_label": folder, "path": f"fixture/{folder}/doc_{did}.pdf",
            "all_paths": [f"fixture/{folder}/doc_{did}.pdf"], "classified_type": dtype, "type_confidence": 0.95,
            "scheme_relevance": "allikulam", "unit_no": str(unit) if unit else None,
            "block_no": str(block) if block else None, "village": village, "doc_no": f"FX/{did}/2024",
            "doc_date": ddate, "date_precision": "day", "lang": "ta", "status": "classified", "stage": stage}))
        for i, txt in enumerate(pages_text, start=1):
            out.append(ins(conn, "page", {"id": did * 100 + i, "document_id": did, "page_no": i,
                                          "text_layer_ok": True, "text_quality": 0.9, "text": txt,
                                          "preview_path": f"fixture/previews/{did}_{i}.webp"}))

    add_doc(1, "GO", "GO_AS_LPS", None, None, None, date(2021, 2, 26),
            ["G.O.(Ms) No.100 Industries (SIPCOT-LA) Department dated 26.02.2021. Administrative sanction for "
             "acquisition of lands for Allikulam Oil Refinery Industry Formation. Village-wise extents: "
             "Melathattaparai 175.83.5 ha, Ramasamypuram 95.23.0 ha."], "GO")
    add_doc(2, "SEC_3_1_GAZETTE", "SEC_3_1", "Melathattaparai", 2, 4, date(2022, 11, 3),
            ["Tamil Nadu Government Gazette Extraordinary No.506 dated 03.11.2022. Notification under section "
             "3(1) of the Tamil Nadu Acquisition of Land for Industrial Purposes Act, 1997. Village "
             "Melathattaparai, Unit 4 Block 2, survey numbers 231/1, 231/2, 232/1, 232/2, 233, 234, 235."],
            "3(1) Published")
    add_doc(3, "SEC_3_1_GAZETTE", "SEC_3_1", "Ramasamypuram", 1, 2, date(2022, 11, 28),
            ["Gazette No.527 dated 28.11.2022. Section 3(1) notice. Ramasamypuram village, Unit 2 Block 1, "
             "10.2300 ha."], "3(1) Published")
    add_doc(4, "SEC_3_2_NOTICE", "SEC_3_2", "Melathattaparai", 2, 4, date(2021, 12, 18),
            ["பிரிவு 3(2) அறிவிப்பு. மேலத்தட்டப்பாறை கிராமம், தொகுதி 2. நில உரிமையாளர்: ஆறுமுகம் த/பெ கணேசன், "
             "புல எண் 233, விஸ்தீரணம் 2.03.00 ஹெக்டேர்."], "3(2) Notice")
    add_doc(5, "PRICE_NEGOTIATION", "PRICE_NEGOTIATION", "Melathattaparai", 2, 4, date(2023, 8, 24),
            ["DLPNC meeting dated 24.08.2023, Melathattaparai Block 2: land value fixed at Rs.5,00,000 per acre."],
            "DLPNC")
    add_doc(6, "PRICE_NEGOTIATION", "PRICE_NEGOTIATION", "Ramasamypuram", 1, 2, date(2023, 8, 9),
            ["SLPNC dated 09.08.2023, Ramasamypuram Block 1: Rs.9,00,000 per acre; Block 2: Rs.7,00,000 per acre."],
            "SLPNC")
    add_doc(7, "AWARD_7_2", "AWARD", "Melathattaparai", 2, 4, date(2024, 6, 14),
            ["பிரிவு 7(2) தீர்வு. Award No. A7/10/Unit-4-Block-2/2024. Schedule of lands and compensation "
             "(இழப்பீட்டுத் தொகை) at Rs.5,00,000 per acre.",
             "Schedule continued. Thiru. Selvaraj S/o Murugan, survey 234, 2.18.60 ha, amount Rs.27,00,000/-."],
            "7(2) Awarded")
    add_doc(8, "FORM_F", "AWARD", "Ramasamypuram", 1, 2, date(2024, 9, 2),
            ["படிவம் F. இராமசாமிபுரம் கிராமம், தொகுதி 1. Compensation apportionment per owner at Rs.9,00,000 per "
             "acre. Tmt. Muthulakshmi W/o Sankaran."], "Form F")
    add_doc(9, "LDR", "POSSESSION", "Melathattaparai", 2, 4, date(2025, 3, 21),
            ["Land Delivery Certificate. Melathattaparai Block 2, survey numbers 233 and 235 handed over to "
             "SIPCOT on 21.03.2025."], "LDR Issued")
    add_doc(10, "BANK_INSTRUMENT", "PAYMENT", "Melathattaparai", 2, 4, date(2024, 11, 5),
            ["Demand draft payable to the Principal District Judge, Thoothukudi, Rs.12,60,000/- (Rupees twelve "
             "lakh sixty thousand only)."], "Court Deposit")
    add_doc(11, "EXEMPTION_ORDER", "EXEMPTION", "Ramasamypuram", 2, 2, date(2023, 3, 10),
            ["Exemption G.O. approved: lands in Ramasamypuram Block 2 survey listed below exempted from "
             "acquisition (temple land)."], "Exemption GO Approved")
    add_doc(12, "CHITTA", "MUTATION", "Melathattaparai", 2, 4, date(2025, 6, 30),
            ["சிட்டா. பட்டா எண் 1234. உரிமையாளர்: SIPCOT. புல எண் 233 புன்செய் 2 - 03.00"], "Patta Transferred")

    # extraction + facts + owners + events
    ext_id, fact_id, ev_id, owner_id = 1000, 5000, 9000, 100
    owners_by_uid = {}
    for i, uid in enumerate(uids):
        p = info[uid]
        oi = i % len(FAKE_OWNERS_EN)
        owner_id += 1
        owners_by_uid[uid] = owner_id
        out.append(ins(conn, "owner", {
            "id": owner_id, "owner_key": f"fx-owner-{owner_id}", "canonical_name": FAKE_OWNERS_EN[oi],
            "variants": [FAKE_OWNERS_EN[oi], FAKE_OWNERS_TA[oi], FAKE_OWNERS_EN[oi].upper()],
            "relation": "father", "relation_name": FATHERS_EN[i % len(FATHERS_EN)], "legal_heirs": False}))
    for i, uid in enumerate(uids):
        p = info[uid]
        vil, blk = p["village"], p["block"]
        rate = RATE_BY_BLOCK[(vil, blk)] if (vil, blk) in RATE_BY_BLOCK else 500000
        doc = 7 if vil == "Melathattaparai" else 8
        dtype = "AWARD_7_2" if doc == 7 else "FORM_F"
        # the document extent: normally equal to GIS to 2 dp; seeded EXTENT_MISMATCH on 2 parcels
        extent = round(p["area"], 2)
        if i in (3, 27):
            extent = round(p["area"] * 1.12, 2)
        acres = round(extent * 2.47, 2)
        amount = int(round(acres * rate))
        if i == 5:
            amount += 96800          # seeded COMPENSATION_MISMATCH (printed != ac x rate)
        classification = "wet" if i in (8, 30) else ("poramboke" if i == 15 else "dry")
        ext_id += 1
        page_id = doc * 100 + (1 if i % 2 == 0 else (2 if doc == 7 else 1))
        y0 = round(0.2 + (i % 10) * 0.06, 3)
        row = {"survey_no": p["survey_no"], "sub_div": p["sub_div"], "village": vil, "block": blk,
               "extent_ha": extent, "extent_ac": acres, "classification": classification, "amount_rs": amount,
               "rate_per_acre": rate}
        out.append(ins(conn, "extraction", {
            "id": ext_id, "document_id": doc, "page_id": page_id, "page_no": page_id % 100,
            "page_ref": f"fixture/doc_{doc}.pdf#p{page_id % 100}", "record_no": i + 1, "schema_name": "parcel_row",
            "doc_type": dtype, "row_json": row, "bbox": [0.08, y0, 0.92, round(y0 + 0.05, 3)],
            "bbox_method": "vlm_row", "extractor": "vlm:primary", "model_id": "bedrock-ministral-8b",
            "privacy_tier": "PII", "confidence": 0.62 if i == 11 else 0.9,
            "self_consistency": "fail" if i == 11 else "pass",
            "owner_read_status": "vlm_single", "review_status": "queued" if i == 11 else "auto",
            "page_status": "needs_human" if i == 11 else "accepted",
            "content_hash": hashlib.sha256(f"fx-page-{page_id}".encode()).hexdigest(),
            "schema_version": "fixture-1"}))
        out.append(ins(conn, "extraction_owner", {"extraction_id": ext_id, "owner_id": owners_by_uid[uid],
                                                  "position": 1, "raw_text": FAKE_OWNERS_TA[i % 20]}))
        facts = [("extent_ha", extent, None, "ha"), ("extent_ac", acres, None, "ac"),
                 ("classification", None, classification, None), ("amount_rs", amount, None, "INR"),
                 ("rate_per_acre", rate, None, "INR/ac"),
                 ("owner", None, FAKE_OWNERS_EN[i % 20], None), ("patta_no", None, str(1200 + i), None)]
        for ftype, vnum, vtext, unit in facts:
            fact_id += 1
            out.append(ins(conn, "parcel_fact", {
                "id": fact_id, "parcel_uid": uid, "village": vil, "survey_no": p["survey_no"],
                "sub_div": p["sub_div"], "survey_ref": f"{vil}|{p['survey_no']}|{p['sub_div'] or ''}",
                "fact_type": ftype, "value_num": vnum, "value_text": vtext, "unit": unit,
                "extraction_id": ext_id if ftype in ("extent_ha", "extent_ac", "classification", "amount_rs",
                                                     "rate_per_acre", "owner", "patta_no") else None,
                "doc_type": dtype, "valid_from": "2024-06-14" if doc == 7 else "2024-09-02"}))
        # events: lifecycle with seeded defects
        stages = [("SEC_3_2", date(2021, 12, 18) + timedelta(days=i % 5), 4 if doc == 7 else None),
                  ("SEC_3_1", date(2022, 11, 3) if vil == "Melathattaparai" else date(2022, 11, 28), 2 if doc == 7 else 3),
                  ("PRICE_NEGOTIATION", date(2023, 8, 24) if doc == 7 else date(2023, 8, 9), 5 if doc == 7 else 6),
                  ("AWARD", date(2024, 6, 14) if doc == 7 else date(2024, 9, 2), doc)]
        if i == 20:
            stages = [s for s in stages if s[0] != "SEC_3_2"]            # seeded STAGE_ORDER_VIOLATION
        paid = i not in (9, 25, 26)                                       # seeded STALLED (awarded, not paid)
        if paid:
            stages.append(("PAYMENT", date(2024, 11, 5) + timedelta(days=i % 20), 10 if doc == 7 else None))
            if i % 3 != 0:
                stages.append(("POSSESSION", date(2025, 3, 21) if doc == 7 else date(2025, 2, 27), 9 if doc == 7 else None))
                if i % 4 == 0:
                    stages.append(("MUTATION", date(2025, 6, 30), 12 if doc == 7 else None))
        if uid.startswith("Ramasamypuram") and blk == 2 and i == 36:
            stages.insert(2, ("EXEMPTION", date(2023, 3, 10), 11))        # seeded EXEMPTION_CONFLICT
        for stg, d, src_doc in stages:
            ev_id += 1
            out.append(ins(conn, "acquisition_event", {
                "id": ev_id, "parcel_uid": uid, "village": vil, "survey_no": p["survey_no"], "sub_div": p["sub_div"],
                "survey_ref": f"{vil}|{p['survey_no']}|{p['sub_div'] or ''}", "unit_no": str(p["unit"]),
                "block_no": str(blk), "stage": stg,
                "sub_stage": {"AWARD": "award_7_2" if doc == 7 else "form_f", "PAYMENT": "deposit" if doc == 7 else "transfer",
                              "POSSESSION": "handover"}.get(stg),
                "event_date": d, "date_precision": "day", "source": "extraction", "document_id": src_doc,
                "extraction_id": ext_id if stg == "AWARD" else None,
                "amount": amount if stg in ("AWARD", "PAYMENT") else None,
                "event_key": f"fx|{uid}|{stg}|{d.isoformat()}"}))
    # synthetic parcel_did (PV3 input) — deterministic values, three parcels look farmland-like
    for i, uid in enumerate(uids):
        farm = i in (1, 4, 22)
        did = round(rng.uniform(-0.15, 0.1) + (0.02 if farm else -0.45), 3)
        out.append(ins(conn, "parcel_did", {
            "parcel_uid": uid, "event": "t_possession", "metric": "vigour_z", "season_scope": "rabi",
            "event_date": "2025-03-21" if uid.startswith("Mela") else "2025-02-27", "n_pre": 4, "n_post": 1,
            "pre_diff": round(rng.uniform(-0.2, 0.2), 3), "post_diff": round(rng.uniform(-0.6, 0.1), 3),
            "did": did, "ci_lo": round(did - 0.25, 3), "ci_hi": round(did + 0.25, 3),
            "farmland_like_p": round(0.82 if farm else rng.uniform(0.05, 0.35), 3),
            "meta": {"synthetic": True}, "did_version": "fixture-1"}))
    for rid, (ext, reason) in enumerate([(1012, "self_consistency_fail"), (1003, "owner_sample")], start=1):
        out.append(ins(conn, "review_queue", {
            "id": rid, "content_hash": hashlib.sha256(f"rq-{rid}".encode()).hexdigest(), "document_id": 7,
            "extraction_id": ext, "reason": reason, "detail": {"synthetic": True}, "status": "open"}))
    for t, col in (("document", "id"), ("page", "id"), ("extraction", "id"), ("owner", "id"),
                   ("parcel_fact", "id"), ("acquisition_event", "id"), ("review_queue", "id")):
        out.append(f"SELECT setval(pg_get_serial_sequence('{t}', '{col}'), (SELECT max({col}) FROM {t}));")
    out.append("COMMIT;")
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "bytes": OUT.stat().st_size, "counts": n, "docs": len(docs)}, indent=1))


if __name__ == "__main__":
    main()
