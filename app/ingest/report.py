"""Document verification report for one upload job: what the document is, what was read from it,
which parcels it was linked to, where those parcels stand, and what the checks flagged.

`build(conn, job)` collects the data (owner names are never included; bank-like fields stripped),
`to_html(report)` renders a self-contained page (PDF via app.export.pdf.html_to_pdf), and
`to_csv(report)` gives one row per extracted record for spreadsheets.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import UTC, datetime

from psycopg.rows import dict_row

from app.api.mask import strip_bank_accounts

STEP_LABELS = {"store": "File saved", "catalog": "Catalogued (pages, fingerprint)", "classify": "Document type",
               "extract": "Tables read", "load": "Loaded into the knowledge graph", "match": "Linked to parcels",
               "findings": "Checks run (findings)"}
CATEGORY_HELP = {
    "EXTENT_MISMATCH": "the extent written in the documents differs from the parcel's area on the survey map",
    "COMPENSATION_MISMATCH": "the awarded amount does not equal extent × the notified rate",
    "DOC_VERSION_CONFLICT": "two official documents give different figures for the same thing",
    "FMB_QUALITY": "the survey-map polygon itself has a problem (overlap, sliver)",
    "PV1_CLASSIFICATION_CONFLICT": "the land class in the papers conflicts with what the satellite sees",
    "PV3_POST_POSSESSION_ACTIVITY": "after possession, the land behaves differently from never-acquired farmland",
    "PV4_IDLE_LAND_BANK": "possessed land shows no clearing or construction (idle)",
    "EXTRACTION_ERROR": "an extracted value looks implausible and needs a human check",
}
RECORD_FIELDS = ["page", "survey_no", "sub_div", "extent_ha", "extent_ac", "amount_rs", "patta_no",
                 "classification", "parcel_uid", "confidence", "check_status", "review_status"]


def _q(conn, sql: str, params=()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def build(conn, job: dict) -> dict:
    doc_id = job.get("document_id")
    doc = (_q(conn, """SELECT id, classified_type, type_evidence, scheme_relevance, village, pages, stage
                       FROM document WHERE id = %s""", (doc_id,)) or [None])[0] if doc_id else None
    records: list[dict] = []
    parcels: list[dict] = []
    findings: list[dict] = []
    if doc:
        rows = _q(conn, """
            SELECT e.id, e.page_no, e.row_json, e.confidence, e.self_consistency, e.review_status,
                   (SELECT f.parcel_uid FROM parcel_fact f WHERE f.extraction_id = e.id AND f.parcel_uid IS NOT NULL
                    LIMIT 1) AS parcel_uid
            FROM extraction e WHERE e.document_id = %s AND e.schema_name IN ('parcel_row', 'form_f')
            ORDER BY e.page_no, e.record_no""", (doc_id,))
        for r in rows:
            j = strip_bank_accounts(r["row_json"] or {})
            records.append({"page": r["page_no"], "survey_no": j.get("survey_no"), "sub_div": j.get("sub_div"),
                            "extent_ha": j.get("extent_ha"), "extent_ac": j.get("extent_ac"),
                            "amount_rs": j.get("amount_rs"), "patta_no": j.get("patta_no"),
                            "classification": j.get("classification"), "parcel_uid": r["parcel_uid"],
                            "confidence": round(r["confidence"], 2) if r["confidence"] is not None else None,
                            "check_status": r["self_consistency"], "review_status": r["review_status"]})
        uids = sorted({u for u in (job.get("result") or {}).get("linked_parcels") or []} |
                      {r["parcel_uid"] for r in records if r["parcel_uid"]})
        if uids:
            parcels = _q(conn, """
                SELECT l.parcel_uid, l.village, l.area_ha_gis, l.current_stage, l.stage_entered, l.days_in_stage,
                       l.next_expected_stage, l.evidence_level,
                       (SELECT round(sum(f.value_num), 4) FROM parcel_fact f JOIN extraction x ON x.id = f.extraction_id
                         WHERE f.parcel_uid = l.parcel_uid AND f.fact_type = 'extent_ha'
                           AND x.document_id = %s) AS doc_extent_ha
                FROM v_parcel_lifecycle l WHERE l.parcel_uid = ANY(%s) ORDER BY l.parcel_uid""", (doc_id, uids))
            findings = _q(conn, """
                SELECT id, category, severity, confidence, parcel_uid, title, caveats
                FROM finding WHERE parcel_uid = ANY(%s)
                ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id""", (uids,))
    ev = (doc or {}).get("type_evidence") or {}
    return {"job": job, "doc": doc, "records": records, "parcels": parcels, "findings": findings,
            "declared": ev.get("source") == "declared_by_uploader", "ai_type": ev.get("ai_type"),
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}


def _summary_lines(rep: dict) -> list[str]:
    job, doc, res = rep["job"], rep["doc"], rep["job"].get("result") or {}
    if job.get("status") == "duplicate":
        return [f"This file is identical to document {job.get('document_id')} already in the system; it was not processed again."]
    if job.get("status") == "failed":
        return [f"Processing stopped: {job.get('error') or 'a step failed'}."]
    if not doc:
        return ["No document was created."]
    t = doc["classified_type"] or "unclassified"
    how = (f"as declared by the uploader (the AI suggested {rep['ai_type'] or 'nothing'})" if rep["declared"]
           else "as classified by the AI")
    out = [f"Document type: {t}, {how}. {doc['pages']} page(s)."]
    if doc.get("scheme_relevance") == "other_scheme":
        out.append("It belongs to another land scheme, so no tables were read.")
    n_rec, n_linked = len(rep["records"]), len(rep["parcels"])
    out.append(f"{n_rec} table row(s) were read; {res.get('facts', 0)} facts and {res.get('events', 0)} "
               f"acquisition event(s) were recorded.")
    out.append(f"{n_linked} parcel(s) were linked on the survey map." if n_linked else
               "No parcel could be linked (no readable survey numbers, block-level document, or a village outside the map).")
    hi = sum(f["severity"] == "high" for f in rep["findings"])
    if rep["findings"]:
        out.append(f"The linked parcels have {len(rep['findings'])} open finding(s) ({hi} high).")
    if res.get("review_items"):
        out.append(f"{res['review_items']} page(s) were sent to the Review queue for a human check.")
    return out


def to_html(rep: dict) -> str:
    e = lambda x: html.escape("" if x is None else str(x))
    job = rep["job"]
    steps = "".join(f"<tr><td>{e(STEP_LABELS.get(s['name'], s['name']))}</td><td class='st {e(s['status'])}'>"
                    f"{e(s['status'])}</td><td>{e(s.get('detail'))}</td></tr>" for s in job.get("stages") or [])
    parcels = "".join(
        f"<tr><td>{e(p['parcel_uid'])}</td><td>{e(p['area_ha_gis'])}</td><td>{e(p['doc_extent_ha'])}</td>"
        f"<td>{e(p['current_stage'])}</td><td>{e(p['stage_entered'])}</td><td>{e(p['next_expected_stage'])}</td></tr>"
        for p in rep["parcels"])
    finds = "".join(
        f"<tr><td class='sev {e(f['severity'])}'>{e(f['severity'])}</td><td>{e(f['parcel_uid'])}</td>"
        f"<td><b>{e(f['category'])}</b><br><span class='muted'>{e(CATEGORY_HELP.get(f['category'], ''))}</span></td>"
        f"<td>{e(f['title'])}</td><td>{e(round(f['confidence'], 2) if f['confidence'] is not None else '')}</td></tr>"
        for f in rep["findings"])
    recs = "".join("<tr>" + "".join(f"<td>{e(r[k])}</td>" for k in RECORD_FIELDS) + "</tr>" for r in rep["records"])
    summary = "".join(f"<li>{e(s)}</li>" for s in _summary_lines(rep))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Document verification report</title>
<style>
body{{font-family:system-ui,sans-serif;color:#1f2937;font-size:11px;margin:0}}
h1{{font-size:18px;margin:0}} h2{{font-size:13px;margin:18px 0 6px;border-bottom:1px solid #d1d5db;padding-bottom:3px}}
.head{{background:#065f46;color:#fff;padding:12px 14px;border-radius:6px}} .head p{{margin:3px 0 0;opacity:.9}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #e5e7eb;padding:3px 5px;text-align:left;vertical-align:top}}
th{{background:#f3f4f6}} .muted{{color:#6b7280}} .st.done{{color:#047857}} .st.failed{{color:#b91c1c}} .st.skipped{{color:#6b7280}}
.sev.high{{color:#b91c1c;font-weight:600}} .sev.medium{{color:#b45309}} .sev.low{{color:#4b5563}}
.box{{background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:8px 12px}}
</style></head><body>
<div class="head"><h1>NilaSaatchi AI · Document verification report</h1>
<p>File: {e(job.get('filename'))} · upload job {e(job.get('id'))} · document {e(job.get('document_id'))} · generated {e(rep['generated_at'])}</p></div>

<h2>1. Summary</h2><ul class="box">{summary}</ul>

<h2>2. Processing steps</h2>
<table><tr><th>Step</th><th>Status</th><th>Detail</th></tr>{steps}</table>

<h2>3. Linked parcels and where they stand</h2>
<p class="muted">Parcel = Village|survey/subdivision on the FMB survey map. Map area is measured from the map polygon; document extent is the
extent this document gives for the parcel (blank if the document states none). Stage = the latest legal step with evidence.</p>
<table><tr><th>Parcel</th><th>Map area (ha)</th><th>Extent in this document (ha)</th><th>Current stage</th><th>Since</th><th>Next expected</th></tr>
{parcels or '<tr><td colspan="6" class="muted">No parcels linked.</td></tr>'}</table>

<h2>4. Findings on these parcels</h2>
<p class="muted">Findings are signals for verification, not legal conclusions. They cover all documents and satellite evidence for these parcels, not just this file.</p>
<table><tr><th>Severity</th><th>Parcel</th><th>Category (what it means)</th><th>Detail</th><th>Confidence</th></tr>
{finds or '<tr><td colspan="5" class="muted">No findings.</td></tr>'}</table>

<h2>5. Rows read from the document</h2>
<p class="muted">Read by AI from the scanned tables and checked automatically (sums, hectare ↔ acre, survey-number sanity).
Owner names are not included in this report. check_status: pass / fail / n/a for the page's arithmetic checks.
Rows on pages that failed a check are in the Review queue.</p>
<table><tr>{''.join(f'<th>{e(k)}</th>' for k in RECORD_FIELDS)}</tr>
{recs or f'<tr><td colspan="{len(RECORD_FIELDS)}" class="muted">No table rows were read.</td></tr>'}</table>

<h2>6. How to use this report</h2>
<div class="box">Check section 1 for the outcome. Open a parcel in the app (Parcel page) to see the scanned page with the value boxed,
the satellite timeline and the evidence behind each finding. Values with low confidence or failed checks should be confirmed
against the original document before they are relied on.</div>
</body></html>"""


def to_csv(rep: dict) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=RECORD_FIELDS)
    w.writeheader()
    for r in rep["records"]:
        w.writerow(r)
    return buf.getvalue()
