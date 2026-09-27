"""Idempotent loader: per-page extraction JSON (pipeline.extract.page) → 0007 tables.

    uv run python -m pipeline.load.extractions --dir data/extract/pages [--replace] [--doc-events]

- idempotency key: content_hash = sha256(image_sha + ":" + schema_version). A page whose hash is
  already loaded is skipped (`--replace` deletes and reloads it).
- extraction: one row per record (+ record 0 = page header). `row_json` has every owner name
  stripped (names live only in owner / extraction_owner, which agent_ro cannot read).
- parcel_fact: survey-level claims with village|survey|sub_div refs normalised; parcel_uid NULL
  (the P5 matcher fills it). Owner facts store only the pseudonymous token (same formula as
  v_owner_pseudo). Errata "should read as" facts supersede the "published as" ones.
- acquisition_event: per parcel row from its page's doc type / dates (source=extraction), and —
  with `--doc-events` — one document-level event per classified document (source=classification).
- review_queue: one row per page queue reason (D-033).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from pipeline.classify.taxonomy import STAGE_OF
from pipeline.normalize.names import normalise_name
from pipeline.normalize.survey import canon_subdiv
from pipeline.normalize.village import normalise_village

PII_KEYS = {"owner_raw", "owners", "owner_other_read", "raw_cells", "owner_vlm_raw"}  # owner_vlm_raw: D-055 (phase-1-2 review)
PII_RAW_KEYS = {"owner"}
SUB_STAGE = {"AWARD_7_2": "award_7_2", "AWARD_7_3": "award_7_3", "FORM_F": "form_f", "LDR": "handover",
             "POSSESSION_CERT": "handover", "CHITTA": "mutation", "SEC32_NOTICE": "notice_3_2",
             "SEC32_ERRATA": "errata_3_2", "SEC31_GAZETTE": "gazette_3_1", "FORM_E": "possession_notice",
             "DISBURSEMENT": "disbursed", "COURT_DEPOSIT": "court_deposit", "BANK_INSTRUMENT": "instrument"}
REVIEW_STATUS = {"needs_human": "queued", "failed": "queued", "accepted_unverified": "auto_unverified"}
FACT_FIELDS = [("extent_ha", "extent_ha", "ha"), ("extent_ac", "extent_ac", "acre"), ("cents", "cents", "cent"),
               ("amount_rs", "amount_rs", "INR"), ("classification", "classification", None),
               ("patta_no", "patta_no", None)]


# ---------------------------------------------------------------------------------- pure helpers
def content_hash(page: dict) -> str:
    return hashlib.sha256(f"{page['image_sha']}:{page.get('schema_version')}".encode()).hexdigest()


def strip_pii(obj):
    """Deep copy without owner names / raw cells (for agent-readable row_json)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in PII_KEYS:
                continue
            if k == "raw" and isinstance(v, dict):
                out[k] = {rk: rv for rk, rv in v.items() if rk not in PII_RAW_KEYS}
                continue
            out[k] = strip_pii(v)
        return out
    if isinstance(obj, list):
        return [strip_pii(x) for x in obj]
    return obj


def header_value(page: dict, field: str):
    return ((page.get("header") or {}).get(field) or {}).get("value")


def page_date(page: dict, doc_date=None, doc_prec=None) -> tuple[str | None, str | None]:
    h = (page.get("header") or {}).get("doc_date") or {}
    if h.get("value"):
        return h["value"], h.get("precision") or "day"
    return (str(doc_date), doc_prec or "day") if doc_date else (None, None)


def survey_ref(village: str | None, survey_no: str | None, sub_div: str | None) -> str | None:
    if not survey_no:
        return None
    return f"{village or '?'}|{str(survey_no).strip().upper()}|{canon_subdiv(sub_div) or ''}"


def facts_from_record(rec: dict, village: str | None) -> list[dict]:
    """Parcel facts of one record (owner facts are added in SQL, token only)."""
    if rec.get("record_type") not in {"parcel_row", "form_f"}:
        return []
    sno = rec.get("survey_no")
    sub = canon_subdiv(rec.get("sub_div")) if rec.get("sub_div") else None
    base = {"village": village, "survey_no": sno, "sub_div": sub, "survey_ref": survey_ref(village, sno, sub)}
    out = []
    for fact_type, key, unit in FACT_FIELDS:
        v = rec.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({**base, "fact_type": fact_type, "value_num": v, "value_text": None, "unit": unit})
        else:
            out.append({**base, "fact_type": fact_type, "value_num": None, "value_text": str(v), "unit": unit})
    tot = (rec.get("raw") or {}).get("extent_ha_total_value")
    if tot not in (None, "None", ""):
        out.append({**base, "fact_type": "total_extent_ha", "value_num": float(tot), "value_text": None,
                    "unit": "ha"})
    if rec.get("boundaries"):
        out.append({**base, "fact_type": "boundary", "value_num": None,
                    "value_text": json.dumps(rec["boundaries"], ensure_ascii=False), "unit": None})
    return out


def event_for_record(page: dict, rec: dict, record_no: int, village: str | None, chash: str,
                     doc_date=None, doc_prec=None) -> dict | None:
    """Parcel-level lifecycle event evidenced by one extracted parcel row."""
    dt = page.get("doc_type")
    stage = STAGE_OF.get(dt or "")
    if not stage or rec.get("record_type") != "parcel_row" or not rec.get("survey_no"):
        return None
    if rec.get("table_role") in {"published_as", "already_paid_7_2"}:
        return None
    if dt in {"LDR", "POSSESSION_CERT"} and header_value(page, "handover_date"):
        h = page["header"]["handover_date"]
        date, prec = h["value"], h.get("precision") or "day"
    else:
        date, prec = page_date(page, doc_date, doc_prec)
    sub = canon_subdiv(rec.get("sub_div")) if rec.get("sub_div") else None
    return {"village": village, "survey_no": rec["survey_no"], "sub_div": sub,
            "survey_ref": survey_ref(village, rec["survey_no"], sub),
            "unit_no": header_value(page, "unit_no"), "block_no": header_value(page, "block_no"),
            "stage": stage, "sub_stage": SUB_STAGE.get(dt), "event_date": date, "date_precision": prec,
            "source": "extraction", "amount": rec.get("amount_rs"),
            "event_key": f"ext:{chash}:{record_no}:{stage}"}


# ---------------------------------------------------------------------------------- DB loading
def resolve_document(conn, page: dict) -> tuple[int | None, int | None, dict]:
    doc_id = page.get("doc_id")
    row = None
    if isinstance(doc_id, int) or (isinstance(doc_id, str) and doc_id.isdigit()):
        row = conn.execute("SELECT id, doc_date, date_precision, village FROM document WHERE id = %s",
                           (int(doc_id),)).fetchone()
    if row is None and page.get("page_ref"):
        path = page["page_ref"].rsplit("#p", 1)[0]
        row = conn.execute("SELECT id, doc_date, date_precision, village FROM document "
                           "WHERE path = %s OR %s = ANY(all_paths) LIMIT 1", (path, path)).fetchone()
    if row is None:
        return None, None, {}
    pid = conn.execute("SELECT id FROM page WHERE document_id = %s AND page_no = %s",
                       (row[0], page.get("page_no"))).fetchone()
    return row[0], (pid[0] if pid else None), {"doc_date": row[1], "date_precision": row[2], "village": row[3]}


def _upsert_owner(conn, o: dict) -> int | None:
    key = o.get("key") or ""
    if not key.strip():
        return None
    r = conn.execute(
        """INSERT INTO owner (owner_key, canonical_name, variants, relation, relation_name, legal_heirs)
           VALUES (%s, %s, ARRAY[%s], %s, %s, %s)
           ON CONFLICT (owner_key) DO UPDATE SET
             variants = ARRAY(SELECT DISTINCT unnest(owner.variants || EXCLUDED.variants)),
             legal_heirs = owner.legal_heirs OR EXCLUDED.legal_heirs
           RETURNING id""",
        (key, o.get("name") or o.get("raw"), o.get("raw") or o.get("name"), o.get("relation"),
         o.get("relation_name"), bool(o.get("legal_heirs")))).fetchone()
    return r[0]


def _insert_extraction(conn, page, chash, record_no, schema_name, row, doc_id, page_id) -> int:
    ev = row.get("evidence") or {}
    conf = ev.get("confidence", page.get("confidence"))
    if page.get("confidence_cap") and conf is not None:
        conf = min(conf, page.get("confidence"))
    r = conn.execute(
        """INSERT INTO extraction (document_id, page_id, page_no, page_ref, record_no, schema_name, doc_type, row_json,
             bbox, bbox_method, raw_cells, extractor, model_id, privacy_tier, confidence, self_consistency,
             owner_read_status, second_read_decision, review_status, page_status, content_hash, schema_version)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (content_hash, record_no) DO NOTHING RETURNING id""",
        (doc_id, page_id, page.get("page_no"), page.get("page_ref"), record_no, schema_name, page.get("doc_type"),
         json.dumps(strip_pii(row), ensure_ascii=False, default=str), ev.get("bbox"), ev.get("bbox_method"),
         json.dumps(ev.get("raw_cells"), ensure_ascii=False) if ev.get("raw_cells") is not None else None,
         ev.get("source_engine") or page.get("text_source") or "vlm:primary", ev.get("model_id"),
         page.get("privacy_tier") or "PII", conf, page.get("self_consistency"), row.get("owner_read_status"),
         page.get("second_read_decision"), REVIEW_STATUS.get(page.get("status"), "auto"), page.get("status"),
         chash, page.get("schema_version") or "p2.v1")).fetchone()
    return r[0] if r else None


def load_page(conn, page: dict, replace: bool = False) -> dict:
    chash = content_hash(page)
    stats = {"skipped": 0, "extractions": 0, "facts": 0, "events": 0, "owners": 0, "queue": 0}
    exists = conn.execute("SELECT 1 FROM extraction WHERE content_hash = %s LIMIT 1", (chash,)).fetchone()
    if exists and not replace:
        stats["skipped"] = 1
        return stats
    if exists:
        conn.execute("DELETE FROM review_queue WHERE content_hash = %s", (chash,))
        conn.execute("DELETE FROM extraction WHERE content_hash = %s", (chash,))
    doc_id, page_id, doc = resolve_document(conn, page)
    village = header_value(page, "village") or normalise_village(doc.get("village"))
    header_row = {"record_type": "page_header",
                  "header": {k: {kk: vv for kk, vv in (v or {}).items() if kk != "evidence"}
                             for k, v in (page.get("header") or {}).items()},
                  "doc_type": page.get("doc_type"), "doc_type_source": page.get("doc_type_source"),
                  "status": page.get("status"), "review_reasons": page.get("review_reasons"),
                  "queue_reasons": page.get("queue_reasons"), "flags": page.get("flags"),
                  "checks": page.get("checks"), "printed_totals": page.get("printed_totals"),
                  "owner_reads": page.get("owner_reads"), "confidence": page.get("confidence"),
                  "reads": [{k: r.get(k) for k in ("route", "model_id", "ok", "tokens_in", "tokens_out", "cost_usd",
                                                   "crop_method")} for r in page.get("reads") or []],
                  "evidence": {"bbox": [0, 0, 1, 1], "bbox_method": "page", "confidence": page.get("confidence"),
                               "source_engine": page.get("text_source")}}
    _insert_extraction(conn, page, chash, 0, "page_header", header_row, doc_id, page_id)
    stats["extractions"] += 1
    published: dict[tuple, dict[str, int]] = {}
    for i, rec in enumerate(page.get("records") or [], start=1):
        eid = _insert_extraction(conn, page, chash, i, rec.get("record_type") or "unknown", rec, doc_id, page_id)
        if eid is None:
            continue
        stats["extractions"] += 1
        # owners
        tokens_keys = []
        for pos, o in enumerate(rec.get("owners") or []):
            oid = _upsert_owner(conn, o)
            if oid is None:
                continue
            conn.execute("INSERT INTO extraction_owner (extraction_id, owner_id, position, raw_text) "
                         "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (eid, oid, pos, o.get("raw")))
            tokens_keys.append(o["key"])
            stats["owners"] += 1
        # facts
        role = rec.get("table_role")
        facts = facts_from_record(rec, village)
        vf, _ = page_date(page, doc.get("doc_date"), doc.get("date_precision"))
        key = (rec.get("serial"), rec.get("survey_no"))
        for f in facts:
            sup = published.get(key, {}).get(f["fact_type"]) if role == "should_read_as" else None
            r = conn.execute(
                """INSERT INTO parcel_fact (village, survey_no, sub_div, survey_ref, fact_type, value_num, value_text,
                     unit, extraction_id, doc_type, valid_from, supersedes_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (f["village"], f["survey_no"], f["sub_div"], f["survey_ref"], f["fact_type"], f["value_num"],
                 f["value_text"], f["unit"], eid, page.get("doc_type"), vf, sup)).fetchone()
            if r:
                stats["facts"] += 1
                if role == "published_as":
                    published.setdefault(key, {})[f["fact_type"]] = r[0]
        if tokens_keys and rec.get("record_type") in {"parcel_row", "form_f"}:
            base = facts[0] if facts else {"village": village, "survey_no": rec.get("survey_no"),
                                            "sub_div": rec.get("sub_div"),
                                            "survey_ref": survey_ref(village, rec.get("survey_no"), rec.get("sub_div"))}
            conn.execute(
                """INSERT INTO parcel_fact (village, survey_no, sub_div, survey_ref, fact_type, value_text,
                     extraction_id, doc_type, valid_from)
                   SELECT %s,%s,%s,%s,'owner',
                          (SELECT string_agg('OWNER-' || left(md5(s.salt || ':' || k), 10), '; ' ORDER BY ord)
                             FROM unnest(%s::text[]) WITH ORDINALITY AS t(k, ord)),
                          %s,%s,%s
                   FROM pseudo_salt s ON CONFLICT DO NOTHING""",
                (base["village"], base["survey_no"], base.get("sub_div"), base["survey_ref"], tokens_keys, eid,
                 page.get("doc_type"), vf))
            stats["facts"] += 1
        ev = event_for_record(page, rec, i, village, chash, doc.get("doc_date"), doc.get("date_precision"))
        if ev:
            conn.execute(
                """INSERT INTO acquisition_event (village, survey_no, sub_div, survey_ref, unit_no, block_no, stage,
                     sub_stage, event_date, date_precision, source, document_id, extraction_id, amount, event_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_key) DO NOTHING""",
                (ev["village"], ev["survey_no"], ev["sub_div"], ev["survey_ref"], ev["unit_no"], ev["block_no"],
                 ev["stage"], ev["sub_stage"], ev["event_date"], ev["date_precision"], ev["source"], doc_id, eid,
                 ev["amount"], ev["event_key"]))
            stats["events"] += 1
    for reason in page.get("queue_reasons") or []:
        r = conn.execute(
            """INSERT INTO review_queue (content_hash, document_id, page_id, page_ref, reason, detail)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (content_hash, reason) DO NOTHING RETURNING id""",
            (chash, doc_id, page_id, page.get("page_ref"), reason.split(":")[0],
             json.dumps({"raw_reason": reason, "status": page.get("status"), "confidence": page.get("confidence"),
                         "review_reasons": page.get("review_reasons")}))).fetchone()
        stats["queue"] += int(bool(r))
    return stats


def load_document_events(conn) -> int:
    """One document-level event per classified, scheme-relevant document with a lifecycle stage."""
    rows = conn.execute(
        """SELECT id, classified_type, stage, doc_date, date_precision, village, unit_no, block_no
           FROM document WHERE stage IS NOT NULL AND coalesce(scheme_relevance, 'unknown') <> 'other_scheme'"""
    ).fetchall()
    n = 0
    for did, dt, stage, ddate, prec, vil, unit, block in rows:
        r = conn.execute(
            """INSERT INTO acquisition_event (village, unit_no, block_no, stage, sub_stage, event_date, date_precision,
                 source, document_id, event_key)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'classification',%s,%s)
               ON CONFLICT (event_key) DO UPDATE SET stage = EXCLUDED.stage, sub_stage = EXCLUDED.sub_stage,
                 event_date = EXCLUDED.event_date, date_precision = EXCLUDED.date_precision,
                 village = EXCLUDED.village, unit_no = EXCLUDED.unit_no, block_no = EXCLUDED.block_no
               RETURNING (xmax = 0)""",
            (normalise_village(vil) or vil, unit, block, stage, SUB_STAGE.get(dt or ""), ddate,
             prec if ddate else None, did, f"doc:{did}")).fetchone()
        n += int(bool(r and r[0]))
    return n


def iter_pages(paths: list[Path]):
    for p in paths:
        files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
        for f in files:
            if f.name == "metrics.json" or f.name.startswith("_"):
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("image_sha") and "records" in d:
                yield f, d


def main() -> None:
    import psycopg
    from dotenv import load_dotenv

    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=[], help="extraction JSON dir/file (repeatable)")
    ap.add_argument("--replace", action="store_true", help="reload pages whose content_hash is already loaded")
    ap.add_argument("--doc-events", action="store_true", help="also upsert document-level classification events")
    a = ap.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dirs = [Path(d) for d in (a.dir or ["data/extract/pages"])]
    tot = {"pages": 0, "skipped": 0, "extractions": 0, "facts": 0, "events": 0, "owners": 0, "queue": 0}
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for f, page in iter_pages(dirs):
            with conn.transaction():
                s = load_page(conn, page, a.replace)
            tot["pages"] += 1
            for k, v in s.items():
                tot[k] += v
        if a.doc_events:
            with conn.transaction():
                tot["doc_events_new"] = load_document_events(conn)
    print(json.dumps(tot))


if __name__ == "__main__":
    main()
