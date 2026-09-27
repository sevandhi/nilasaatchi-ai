"""Per-page extraction pipeline (D-010) with escalation.

text layer | Tesseract → doc-type (page cues > classifier > folder) → whole-page VLM raw cells →
per-doc-type mapping → normalisers → schema validation → self-consistency → confidence →
escalation: fail / low confidence / header conflict → second read (router task
`table_second_read_pii`: zoomed table crop + prompt variant), kept only if it improves
self-consistency (D-032) → review queue. Owner values carry `owner_read_status`.
Handwritten Form E and blurred newsprint go to review after one VLM attempt.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import cv2

from . import vlm as vlm_mod
from .consistency import acre_column_check, page_confidence, run_checks, summarise
from .doctype import family_of, privacy_tier_of, resolve_doc_type
from pipeline.normalize.dates import parse_date

from .grid import assign_row_bboxes, detect_table_grid
from .headers import Lines, carry_headers, extract_headers
from .owner_reads import annotate_owner_reads, in_owner_sample
from .mapping import prepare_table, table_signature
from .records import (apply_block_prose, apply_page_classification, apply_prose_survey,
                      apply_tesseract_owner_votes, build_records)
from .validate import validate_record

LOW_CONFIDENCE = 0.6
NEWSPRINT_TYPES = {"SEC32_NOTICE", "SEC32_ERRATA"}
HANDWRITING_TYPES = {"FORM_E"}
SCHEMA_VERSION = "p2.v1"
ACCEPTED_STATUSES = {"accepted", "second_read_accepted", "accepted_unverified"}


ROTATED_DIR = Path(__file__).resolve().parents[2] / "data" / "extract" / "rotated"
MIN_OSD_CONFIDENCE = 2.0      # sideways (90/270) on a landscape image
STRONG_OSD_CONFIDENCE = 8.0   # anything else (180, or 90/270 on a portrait image)


def upright(image_path: Path, ocr, sha: str) -> tuple[Path, int]:
    """Rotate a sideways scan upright (Tesseract OSD). Returns (path to use, degrees rotated
    clockwise). Bboxes of a rotated page refer to the upright image.

    OSD false positives on ruled tables are common, especially 180° (an upright LDR page came
    back "Rotate: 180, confidence 1.99"). Flipping an upright page destroys the read, so:
    90/270 is applied on a landscape image (a portrait page scanned sideways) at confidence ≥ 2,
    and 180 — or 90/270 on a portrait image — only at confidence ≥ 8."""
    from PIL import Image
    rot, conf = ocr.detect_orientation(image_path)
    rot %= 360
    if rot == 0:
        return image_path, 0
    w, h = Image.open(image_path).size
    need = MIN_OSD_CONFIDENCE if rot in (90, 270) and w > h else STRONG_OSD_CONFIDENCE
    if conf < need:
        return image_path, 0
    out = ROTATED_DIR / f"{sha}_{rot}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.open(image_path).rotate(-rot, expand=True).save(out)
    return out, rot


def _table_bboxes(image_path: Path, tables: list[dict]) -> list[tuple[list, str]]:
    img = cv2.imread(str(image_path))
    if img is None or not tables:
        return [([], "page") for _ in tables]
    h, w = img.shape[:2]
    grid = detect_table_grid(img)
    out = []
    # the grid detector finds the largest ruled table: give it to the VLM table with most rows
    biggest = max(range(len(tables)), key=lambda i: len(tables[i].get("rows") or []))
    for i, t in enumerate(tables):
        n = len(t.get("rows") or [])
        if i == biggest:
            out.append(assign_row_bboxes(grid, n, w, h))
        else:
            out.append(([[0.0, 0.0, 1.0, 1.0]] * n, "page"))
    return out


def _interpret(image_path: Path, read: dict, lines: Lines, doc_type0: str | None, classified_type: str | None,
               folder_prior: str | None, ctx: dict, text_source: str, carry: dict | None = None) -> dict:
    data = read.get("data") or {}
    cols = [c for t in data.get("tables") or [] for c in (t.get("columns") or []) if c]
    doc_type, dt_src = resolve_doc_type(data.get("title"), lines.text, cols, classified_type, folder_prior)
    if dt_src in {"classifier", "folder", "none"} and doc_type0 and doc_type0 != doc_type:
        doc_type, dt_src = doc_type0, "text_pre"
    # a "disbursement"/instrument prior whose page holds a parcel table with owners is an award
    # order body (golden g15: folder "Amount Disbursed", content 7(2) award)
    if dt_src in {"classifier", "folder", "text_pre"} and family_of(doc_type) in {"owner_amount", "instrument"}:
        for t in data.get("tables") or []:
            pt = prepare_table(t)
            if table_signature(pt) == "parcel" and len(pt.rows) >= 1:
                doc_type, dt_src = "AWARD_7_2", "table_signature"
                break
    family = family_of(doc_type)
    header = carry_headers(extract_headers(lines, data.get("header"), doc_type, text_source), carry)
    rctx = {**ctx, "source_engine": f"vlm:{read.get('route', 'primary')}", "model_id": read.get("model_id")}
    bbs = _table_bboxes(image_path, data.get("tables") or [])
    records, totals, flags = build_records(data, doc_type, family, rctx, lines.text, header, bbs)
    table_totals = totals.pop("_per_table", {})
    # the VLM's own `handwritten` flag fires on printed pages (D-033); only FORM_E counts as handwriting
    if text_source == "tesseract" and doc_type not in HANDWRITING_TYPES:
        n_voted = apply_tesseract_owner_votes(records, lines.text)
        if n_voted:
            flags.append(f"owner_tesseract_vote:{n_voted}")
    if family == "parcel":
        apply_block_prose(records, lines.text)
        apply_prose_survey(records, lines.text)
        apply_page_classification(records, lines.text)
        # a parcel row with neither survey nor extent is a VLM table invented from prose (golden
        # g06: narrative award page read as label/value "tables"); drop it, keep the count
        keep = [r for r in records if r.get("record_type") != "parcel_row" or r.get("survey_no") or
                r.get("extent_ha") is not None or
                (r.get("table_role") == "compensation" and (r.get("amount_rs") or r.get("owner_raw"))) or
                (r.get("table_role") == "heirs" and r.get("owner_raw"))]
        if len(keep) < len(records):
            flags.append(f"dropped_rows_no_key:{len(records) - len(keep)}")
        records = keep
    if not header["doc_no"].get("value") and family == "owner_amount":
        # treasury advices repeat the reference number / date as table columns
        for t in data.get("tables") or []:
            cols = [str(c or "") for c in t.get("columns") or []]
            for ci, c in enumerate(cols):
                vals = [r[ci] for r in t.get("rows") or [] if ci < len(r) and r[ci]]
                if not vals:
                    continue
                if re.search(r"Treasury\s*Reference\s*(Number|No)", c, re.I) and re.fullmatch(r"\d{10,20}", str(vals[0])):
                    header["doc_no"] = {**header["doc_no"], "value": str(vals[0]), "source": "vlm", "raw": str(vals[0])}
                if re.search(r"Treasury\s*Reference\s*Date", c, re.I) and not header["doc_date"].get("value"):
                    dv, dp = parse_date(str(vals[0]))
                    if dv:
                        header["doc_date"] = {**header["doc_date"], "value": dv, "precision": dp, "source": "vlm",
                                              "raw": str(vals[0])}
    if family == "instrument" and records and records[0].get("instrument_no") and not header["doc_no"].get("value"):
        header["doc_no"] = {**header["doc_no"], "value": records[0]["instrument_no"], "source": "vlm",
                            "raw": records[0]["instrument_no"]}
    invalid = 0
    for r in records:
        errs = validate_record(r, doc_type)
        if errs:
            invalid += 1
            r.setdefault("flags", []).append("schema_invalid")
            r["schema_errors"] = errs[:3]
    if invalid:
        flags.append(f"schema_invalid:{invalid}")
    checks = run_checks(records, totals, header, family, table_totals)
    ac = acre_column_check(records, cols) if family == "parcel" else None
    if ac:
        checks.append(ac)
    sc = summarise(checks)
    meta = {"legibility": data.get("legibility"), "handwritten": data.get("handwritten")}
    conf = page_confidence(records, checks, header, family, meta)
    return {"doc_type": doc_type, "doc_type_source": dt_src, "family": family, "header": header,
            "records": records, "printed_totals": totals, "table_totals": table_totals, "checks": checks, "self_consistency": sc,
            "confidence": conf, "flags": flags, "vlm_meta": meta}


def _key_signature(res: dict) -> set:
    return {(r.get("survey_no"), r.get("sub_div"), r.get("extent_ha")) for r in res["records"]
            if r.get("record_type") == "parcel_row"} | \
           {(r.get("village"), r.get("total_ha"), r.get("patta_ha")) for r in res["records"]
            if r.get("record_type") == "village_totals"} | \
           {("amt", r.get("amount_rs")) for r in res["records"] if r.get("record_type") in {"form_f", "payment_instrument"}}


def escalation_reasons(res: dict) -> tuple[list[str], bool]:
    """(reasons, direct_to_review). Direct = handwriting / blurred newsprint: no second read."""
    reasons: list[str] = []
    direct = False
    if res["self_consistency"] == "fail":
        reasons.append("self_consistency_fail")
    if res["confidence"] < LOW_CONFIDENCE:
        reasons.append(f"low_confidence:{res['confidence']}")
    if any((res["header"].get(k) or {}).get("conflict") for k in ("village", "unit_no", "block_no")):
        reasons.append("header_vote_conflict")
    if any(c["name"] == "acre_column_empty" and c["result"] == "fail" for c in res["checks"]):
        reasons.append("acre_column_empty")
    if not res["records"] and res["family"] in {"parcel", "village_totals", "owner_amount"} and \
            not any(f.startswith("prose_as_table") for f in res["flags"]):
        reasons.append("no_records")
    meta = res.get("vlm_meta") or {}
    # the VLM's own `handwritten` flag fires on printed pages with a signature or pen mark
    # (live golden run: LDR g17/g18, notice g13) — only handwriting doc types skip the second read
    if res["doc_type"] in HANDWRITING_TYPES:
        reasons.append("handwriting")
        direct = True
    if res["doc_type"] in NEWSPRINT_TYPES and meta.get("legibility") in {"blurred", "partly_blurred"}:
        reasons.append("blurred_newsprint")
        direct = True
    return reasons, direct


def extract_page(image_path: Path, *, doc_id=None, page_no: int | None = None, page_ref: str | None = None,
                 text_layer: str | None = None, ocr=None, classified_type: str | None = None,
                 folder_prior: str | None = None, allow_calls: bool = True, allow_second_read: bool = True,
                 carry: dict | None = None) -> dict:
    """`carry` = header dict of the previous page of the same document (continuation pages)."""
    image_path = Path(image_path)
    sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
    rotation = 0
    if ocr is not None and hasattr(ocr, "detect_orientation"):
        image_path, rotation = upright(image_path, ocr, sha)
        if rotation:
            text_layer = None          # a text layer of a rotated scan is not trusted
    if text_layer:
        lines, text_source = Lines(None, text_layer), "text_layer"
    else:
        page = ocr.ocr_page(image_path) if ocr is not None else None
        lines = Lines([ln.__dict__ for ln in page.lines] if page else None, page.text if page else "")
        text_source = "tesseract"
    doc_type0, src0 = resolve_doc_type(None, lines.text, None, classified_type, folder_prior)
    tier = privacy_tier_of(doc_type0)
    ctx = {"doc_id": doc_id, "page_no": page_no, "privacy_tier": tier}
    reads: list[dict] = []
    primary = vlm_mod.transcribe(image_path, doc_type0, tier, "primary", allow_calls=allow_calls)
    reads.append({k: primary.get(k) for k in ("route", "model_id", "ok", "tokens_in", "tokens_out", "cost_usd",
                                              "from_cache", "error", "cache_key")} | {"route": "primary"})
    base = {"schema_version": SCHEMA_VERSION, "doc_id": doc_id, "page_no": page_no, "page_ref": page_ref,
            "image_sha": sha, "privacy_tier": tier, "text_source": text_source, "rotation": rotation}
    if not primary.get("ok"):
        res = _interpret(image_path, {"data": {"tables": []}}, lines, doc_type0, classified_type, folder_prior,
                         ctx, text_source, carry)
        return {**base, **res, "reads": reads, "status": "needs_human" if allow_calls else "failed",
                "review_reasons": ["vlm_failed"], "second_read_decision": None, "owner_reads": {},
                "review_queue": bool(allow_calls), "queue_reasons": ["vlm_failed"] if allow_calls else []}
    res = _interpret(image_path, primary, lines, doc_type0, classified_type, folder_prior, ctx, text_source, carry)
    reasons, direct = escalation_reasons(res)
    status = "accepted"
    other_records = None
    second_decision = None
    if reasons and direct:
        status = "needs_human"
    elif reasons and allow_second_read:
        n_tables = len((primary.get("data") or {}).get("tables") or [])
        # a crop covers only the largest ruled table: multi-table pages are re-read whole (zoomed)
        second = vlm_mod.transcribe(image_path, res["doc_type"], tier, "second", allow_calls=allow_calls,
                                    crop=n_tables < 2)
        reads.append({k: second.get(k) for k in ("model_id", "ok", "tokens_in", "tokens_out", "cost_usd",
                                                 "from_cache", "error", "cache_key", "crop_method")}
                     | {"route": "second"})
        if second.get("ok"):
            # the crop has no page heading: title/header votes come from the primary read
            sdata = dict(second["data"] or {})
            pdata = primary.get("data") or {}
            sdata["title"] = sdata.get("title") or pdata.get("title")
            sdata["header"] = pdata.get("header")
            res2 = _interpret(image_path, {**second, "data": sdata, "route": "second"}, lines, doc_type0,
                              classified_type, folder_prior, ctx, text_source, carry)
            if improves_consistency(res, res2):
                other_records = res["records"]
                res = res2
                r2, _ = escalation_reasons(res2)
                second_decision = "second_improved"
                status, reasons = ("second_read_accepted", []) if not r2 else \
                    ("needs_human", r2 + ["second_read_improved_but_flagged"])
            elif _key_signature(res2) and _key_signature(res2) == _key_signature(res) and \
                    res["self_consistency"] != "fail":
                other_records = res2["records"]
                second_decision = "reads_agree"
                status = "accepted"         # two independent reads agree on every key field
                reasons = reasons + ["reads_agree"]
            else:
                other_records = res2["records"]
                second_decision = "kept_first"
                status = "needs_human"      # D-032: keep the first read, route to review
                reasons.append("second_read_no_improvement")
        else:
            status = "needs_human"
            reasons.append("second_read_unavailable")
    elif reasons:
        status = "needs_human"
    status, reasons, res = apply_review_policy(status, reasons, res, second_decision)
    owner_counts = annotate_owner_reads(res["records"], other_records)
    # D-033: the queue gets only the hard reasons (soft ones stay in review_reasons / flags)
    queue_reasons = [r for r in reasons if r.split(":")[0] in HARD_REVIEW_REASONS] if status == "needs_human" else []
    if status == "needs_human" and not queue_reasons:
        queue_reasons = ["self_consistency_fail"] if res["self_consistency"] == "fail" else ["vlm_failed"]
    if status in ACCEPTED_STATUSES and any(r.get("owner_raw") for r in res["records"]) \
            and in_owner_sample(sha):
        queue_reasons.append("owner_sample")
    return {**base, **res, "reads": reads, "status": status, "review_reasons": reasons,
            "second_read_decision": second_decision, "owner_reads": owner_counts,
            "review_queue": bool(queue_reasons), "queue_reasons": queue_reasons}


# D-033: only these send a page to the review queue (plus the 10% owner_sample)
HARD_REVIEW_REASONS = {"self_consistency_fail", "handwriting", "blurred_newsprint", "prose_as_table",
                       "vlm_failed"}
UNVERIFIED_CAP = 0.7


def apply_review_policy(status: str, reasons: list[str], res: dict, second_decision: str | None
                        ) -> tuple[str, list[str], dict]:
    """D-033: a page whose self-consistency does not fail is accepted even if escalated and the
    second read did not improve it; its soft reasons stay as flags and confidence is capped at 0.7.
    Review only gets consistency failures, FORM_E handwriting, blurred newsprint and prose-as-table."""
    reasons = list(reasons)
    if any(f.startswith("prose_as_table") for f in res.get("flags") or []) and "prose_as_table" not in reasons:
        reasons.append("prose_as_table")
    if status != "needs_human":
        return status, reasons, res
    hard = [r for r in reasons if r in HARD_REVIEW_REASONS]
    if res["self_consistency"] == "fail" and "self_consistency_fail" not in hard:
        hard.append("self_consistency_fail")
    if hard:
        return "needs_human", reasons, res
    res = {**res, "confidence": min(res["confidence"], UNVERIFIED_CAP), "confidence_cap": "D-033",
           "flags": list(res.get("flags") or []) + [r.split(":")[0] for r in reasons]}
    return ("second_read_accepted" if second_decision == "second_improved" else "accepted_unverified"), reasons, res


_SC_RANK = {"fail": 0, "n/a": 1, "pass": 2}


def _coverage(res: dict) -> set:
    return {(r.get("survey_no"), r.get("sub_div")) for r in res["records"]
            if r.get("record_type") == "parcel_row" and r.get("survey_no")} | \
           {("amt", r.get("amount_rs")) for r in res["records"] if r.get("amount_rs")}


def improves_consistency(first: dict, second: dict) -> bool:
    """Strictly better self-consistency AND no loss of coverage (dev d01: a crop of one table
    "improved" consistency by dropping the other three tables)."""
    if "records" in first and "records" in second and not _coverage(first) <= _coverage(second) | set():
        lost = _coverage(first) - _coverage(second)
        if len(lost) > max(0, len(_coverage(first)) // 10):
            return False
    return _improves_checks(first, second)


def _improves_checks(first: dict, second: dict) -> bool:
    """D-032: the second read replaces the first only if self-consistency gets strictly better —
    a better overall grade, or the same grade with fewer failed checks and at least as many passes."""
    r1, r2 = _SC_RANK.get(first["self_consistency"], 1), _SC_RANK.get(second["self_consistency"], 1)
    if r2 != r1:
        # fewer checks is not better consistency: only a second read that passes may beat a
        # worse grade (golden g16: a failing primary vs a check-less second read with a wrong amount)
        return r2 > r1 and second["self_consistency"] == "pass"
    f1 = sum(c["result"] == "fail" for c in first["checks"])
    f2 = sum(c["result"] == "fail" for c in second["checks"])
    p1 = sum(c["result"] == "pass" for c in first["checks"])
    p2 = sum(c["result"] == "pass" for c in second["checks"])
    return f2 < f1 and p2 >= p1
