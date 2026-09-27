"""Mapped tables → typed records (schemas/extraction/*.json) with normalised values and evidence."""
from __future__ import annotations

import re

from pipeline.normalize.classification import normalise_classification
from pipeline.normalize.names import normalise_name, owner_display, split_owners
from pipeline.normalize.numbers import (
    parse_amount,
    parse_cents,
    parse_extent_ac,
    parse_extent_ha,
    parse_number_words,
)
from pipeline.normalize.survey import canon_subdiv, parse_survey_ref, split_merged_survey_extent
from pipeline.normalize.village import normalise_village

from .mapping import (
    TOTAL_RE,
    PreparedTable,
    cell_type,
    map_owner_amount_columns,
    map_parcel_columns,
    map_village_columns,
    norm,
    prepare_table,
    profile_for,
    table_signature,
)

# award schedules merge the survey cell over many rows (held-out: 13 of 17 rows had no own survey)
MERGED_SURVEY_TYPES = {"AWARD_7_2", "AWARD_7_3", "CHITTA", "SEC32_NOTICE", "SEC32_ERRATA", "FORM_E"}
_DESC_LABEL = re.compile(r"விவரம்|விவரங்கள்|இனம்|description|particulars|items?\b", re.IGNORECASE)
WELL_RE = re.compile(r"கிணறு|சதுரக்கிணறு|சதுரக்கணம்|\bwell\b", re.IGNORECASE)


def _evidence(ctx: dict, bbox, method, row_index, table_index, raw_cells, conf) -> dict:
    return {"doc_id": ctx.get("doc_id"), "page": ctx.get("page_no"), "bbox": bbox, "bbox_method": method,
            "row_index": row_index, "table_index": table_index, "source_engine": ctx["source_engine"],
            "model_id": ctx.get("model_id"), "privacy_tier": ctx["privacy_tier"],
            "confidence": round(conf, 3), "raw_cells": [None if c is None else str(c) for c in raw_cells]}


_OWNER_LABEL_RE = re.compile(r"^(?:நில\s*)?(?:உரிமையாளர்(?:கள்)?|உடமைதாரர்(?:கள்)?)(?:\s*விபரம்)?\s*[:：]?$|"
                             r"^(?:name|owner|land\s*owners?)\s*[:：]?$", re.IGNORECASE)


def clean_owner_cell(raw: str | None) -> str | None:
    """Drop column-label lines that the VLM copied into an owner cell ("நில உரிமையாளர்\n1. ...")."""
    if raw is None:
        return None
    lines = [ln for ln in norm(raw).split("\n") if not _OWNER_LABEL_RE.match(ln.strip())]
    return "\n".join(lines).strip() or None


def _owners(raw: str | None) -> list[dict]:
    out = []
    for part in split_owners(clean_owner_cell(raw)):
        o = normalise_name(part)
        if o:
            out.append({"raw": o.raw, "name": o.name, "relation": o.relation, "relation_name": o.relation_name,
                        "honorific": o.honorific, "legal_heirs": o.legal_heirs, "key": o.key})
    return out


_REL_MARK = re.compile(r"[தகTSWDC]\s*/\s*[பெoO]|மகன்|மனைவி|மகள்|வாரிசு|S/o|W/o|D/o|Ltd|லிமிடெட்", re.I)


def _is_prose_cell(raw) -> bool:
    s = norm(raw)
    return len(s.split()) >= 12 and not _REL_MARK.search(s) and "\n" not in s


def _row_conf(fields_ok: int, fields_expected: int, base: float = 0.85) -> float:
    if fields_expected == 0:
        return base
    return max(0.1, min(0.99, base * (0.5 + 0.5 * fields_ok / fields_expected)))


# ---------------------------------------------------------------------------
# Parcel rows
# ---------------------------------------------------------------------------
def parcel_records(pt: PreparedTable, doc_type: str | None, ctx: dict, table_index: int,
                   bboxes: list, bbox_method: str, table_role: str | None = None) -> tuple[list[dict], dict]:
    cmap = map_parcel_columns(pt, doc_type)
    prof = profile_for(doc_type)
    records: list[dict] = []
    last: dict[str, str | None] = {}
    _move_unlabelled_totals(pt, cmap.fields)
    _merge_continuation_rows(pt, cmap.fields)
    _spread_merged_cells(pt, cmap.fields)
    si = cmap.fields.index("survey_no") if "survey_no" in cmap.fields else None
    survey_gaps = (sum(1 for r in pt.rows if si is None or si >= len(r) or not r[si]) / len(pt.rows)) if pt.rows else 0.0
    for k, row in enumerate(pt.rows):
        raw = {f: row[i] for i, f in enumerate(cmap.fields) if f and i < len(row)}
        # key-field repair: a value shifted by one cell (the VLM sometimes inserts a "--")
        for f, want in (("extent_ha", {"ha"}), ("survey_no", {"survey", "int"})):
            if f in cmap.fields:
                i = cmap.fields.index(f)
                if cell_type(row[i]) not in want:
                    for j in (i + 1, i - 1, i + 2):
                        if 0 <= j < len(row) and cell_type(row[j]) in want and (
                                j >= len(cmap.fields) or cmap.fields[j] in (None, "remarks", "other_extent")):
                            raw[f] = row[j]
                            break
        rec: dict = {"record_type": "parcel_row", "serial": None, "survey_no": None, "sub_div": None, "part": False,
                     "extent_ha": None, "extent_ac": None, "cents": None, "patta_no": None, "owner_raw": None,
                     "owners": [], "classification": None, "classification_raw": None, "amount_rs": None,
                     "table_role": table_role, "filled_down": [], "raw": {k2: v for k2, v in raw.items()}}
        if raw.get("serial"):
            rec["serial"] = norm(raw["serial"]).rstrip(".") or None
        surv = raw.get("survey_no")
        if surv:
            s = norm(surv)
            m = re.match(r"^(\d+)\s*/\s*(\d{1,2}\.\d{2}\.\d{2})$", s)
            if m:                                  # "173/20.86.00" run together (§11b)
                split = split_merged_survey_extent(s.split("/", 1)[1])
                if split:
                    rec["survey_no"], rec["sub_div"] = m.group(1), split[0]
                    rec["extent_ha"] = split[1]
            else:
                refs = parse_survey_ref(s)
                if refs:
                    rec["survey_no"], rec["sub_div"], rec["part"] = refs[0].survey_no, refs[0].sub_div, refs[0].part
                    if len(refs) > 1:
                        rec["raw"]["survey_list"] = s
        if raw.get("sub_div") and norm(raw["sub_div"]) not in {"-", "--"}:
            rec["sub_div"] = canon_subdiv(raw["sub_div"])
        ha_raw = raw.get("extent_ha")
        for f, cls in (("dry_extent", "DRY"), ("wet_extent", "WET"), ("other_extent", "OTHER")):
            if raw.get(f) and cell_type(raw[f]) == "ha":
                ha_raw = ha_raw or raw[f]
                rec["classification"] = rec["classification"] or cls
                rec["classification_raw"] = rec["classification_raw"] or f
        if ha_raw and rec["extent_ha"] is None:
            rec["extent_ha"] = parse_extent_ha(ha_raw)
        if raw.get("extent_ha_total"):
            rec["raw"]["extent_ha_total_value"] = str(parse_extent_ha(raw["extent_ha_total"]))
            if rec["extent_ha"] is None:
                rec["extent_ha"] = parse_extent_ha(raw["extent_ha_total"])
        if raw.get("extent_ac"):
            rec["extent_ac"] = parse_extent_ac(raw["extent_ac"])
        if raw.get("cents"):
            rec["cents"] = parse_cents(raw["cents"])
        if raw.get("patta_no"):
            p = re.sub(r"[^\dA-Za-z/]", "", norm(raw["patta_no"]))
            rec["patta_no"] = p or None
        if raw.get("owner") and "owner" not in prof.drop_fields:
            rec["owner_raw"] = owner_display(clean_owner_cell(raw["owner"]))
            rec["owners"] = _owners(raw["owner"])
        if raw.get("classification"):
            rec["classification_raw"] = norm(raw["classification"])
            rec["classification"] = normalise_classification(raw["classification"])
        if raw.get("amount_rs"):
            rec["amount_rs"] = parse_amount(raw["amount_rs"])
        for f in ("land_amount_rs", "tree_amount_rs"):
            if raw.get(f):
                rec[f] = parse_amount(raw[f])
        shares = [norm(row[i]) for i, f in enumerate(cmap.fields) if f == "share" and i < len(row) and row[i]]
        if shares:
            rec["share"] = " ".join(" ".join(shares).split())
        for f in ("plot_no", "trees", "buildings", "remarks"):
            if raw.get(f) and norm(raw[f]) not in {"-", "--"}:
                rec[f] = norm(raw[f])
        if raw.get("assessment_rs"):
            try:
                rec["assessment_rs"] = float(norm(raw["assessment_rs"]).replace(",", ""))
            except ValueError:
                pass
        if any(raw.get(f) for f in ("north", "east", "south", "west")):
            rec["boundaries"] = {f: norm(raw.get(f)) or None for f in ("north", "east", "south", "west")}
        if rec.get("remarks"):
            rec["well_marker"] = bool(WELL_RE.search(rec["remarks"]))
        # merged survey cell: a sub-division row without a survey continues the survey above
        has_extent = rec.get("extent_ha") is not None or bool(raw.get("extent_ac"))
        # a merged survey cell leaves MANY rows blank; one blank among rows that all print their own
        # survey is a misread (golden g07 row 6) - leave it empty rather than copy the wrong survey
        if rec["survey_no"] is None and last.get("survey_no") and (
                rec.get("sub_div") or (doc_type in MERGED_SURVEY_TYPES and has_extent and survey_gaps >= 0.3)):
            rec["survey_no"] = last["survey_no"]
            if not rec.get("sub_div") and last.get("sub_div"):
                rec["sub_div"] = last["sub_div"]        # the merged cell held "survey/sub" together
            rec["group_ref"] = last.get("group_ref")
            rec["filled_down"].append("survey_no")
        # compensation group: rows with owner + amount but no survey and no extent continue the group
        # headed by the last row that printed a survey (merged survey cell over the owners); survey,
        # sub-division and patta are inherited, the extent stays on the group head only
        if rec["survey_no"] is None and last.get("survey_no") and rec.get("extent_ha") is None \
                and not raw.get("extent_ac") and rec.get("owner_raw") and rec.get("amount_rs") is not None:
            rec["survey_no"], rec["sub_div"] = last["survey_no"], last.get("sub_div")
            if rec.get("patta_no") is None and last.get("patta_no"):
                rec["patta_no"] = last["patta_no"]
            rec["group_ref"] = last.get("group_ref")
            rec["filled_down"].append("survey_no:group")
        elif rec["survey_no"] and "survey_no" not in rec["filled_down"]:
            last["survey_no"], last["sub_div"] = rec["survey_no"], rec.get("sub_div")
            last["group_ref"] = rec["group_ref"] = f"t{table_index}r{k}"
            if rec.get("patta_no"):
                last["patta_no"] = rec["patta_no"]
        # merged cells: fill down owner / patta / classification for award tables
        for f in prof.fill_down:
            key = "owner_raw" if f == "owner" else f
            if rec.get(key) is None and last.get(key) and rec["survey_no"]:
                rec[key] = last[key]
                if f == "owner":
                    rec["owners"] = _owners(last[key].replace(" ; ", "\n"))
                rec["filled_down"].append(key)
            if rec.get(key):
                last[key] = rec[key]
        if not any(rec.get(f) for f in ("survey_no", "extent_ha", "extent_ac", "owner_raw", "amount_rs", "cents")):
            continue
        if _is_prose_cell(raw.get("owner")):
            # a sentence in the owner column: the VLM turned prose into a table (golden g06)
            ctx["prose_rows_dropped"] = ctx.get("prose_rows_dropped", 0) + 1
            continue
        ok = sum(1 for f in prof.expected if rec.get("owner_raw" if f == "owner" else f) is not None)
        conf = _row_conf(ok, len(prof.expected)) * (0.9 if rec["filled_down"] else 1.0)
        bi = pt.row_index[k]
        rec["evidence"] = _evidence(ctx, bboxes[bi] if bi < len(bboxes) else [0, 0, 1, 1], bbox_method, bi,
                                    table_index, row, conf)
        records.append(rec)
    if doc_type in MERGED_SURVEY_TYPES:
        _align_group_extents(records)
    totals = _parcel_totals(pt, cmap.fields)
    return records, totals


def _align_group_extents(records: list[dict]) -> None:
    """Compensation groups (a merged survey cell over several owner rows): the group's single
    merged extent cell is vertically centred, so the VLM attaches it to a middle row. A group =
    a row with a PRINTED survey (the head) + the following rows without one. If the head has no
    extent and exactly one member has, move that extent (ha and, if present, acres) to the head
    and record `extent_aligned: group_head`. Missing acres are never derived."""
    groups: list[list[dict]] = []
    for r in records:
        if r.get("record_type") != "parcel_row" or r.get("table_role") in {"heirs", "published_as"}:
            continue
        printed = bool((r.get("raw") or {}).get("survey_no"))
        if printed or not groups:
            groups.append([r])
        else:
            groups[-1].append(r)
    for g in groups:
        head, members = g[0], g[1:]
        if not members or not (head.get("raw") or {}).get("survey_no"):
            continue
        for m in members:
            if m.get("survey_no") == head.get("survey_no"):
                m["group_ref"] = head.get("group_ref")
        with_ext = [m for m in members if m.get("extent_ha") is not None or m.get("extent_ac") is not None]
        if head.get("extent_ha") is None and head.get("extent_ac") is None and len(with_ext) == 1:
            m = with_ext[0]
            head["extent_ha"], head["extent_ac"] = m.get("extent_ha"), m.get("extent_ac")
            m["extent_ha"] = m["extent_ac"] = None
            head["extent_aligned"] = "group_head"
            head.setdefault("evidence", {})["extent_from_row"] = (m.get("evidence") or {}).get("row_index")
            m.setdefault("filled_down", []).append("extent:moved_to_group_head")


def _merge_continuation_rows(pt: PreparedTable, fields: list) -> None:
    """Wrapped apportionment rows: a row with no serial, owner, survey or extent is the second
    line of the row above (the share continues; the amount often sits on this line, half a row
    below the name - dev2 e07/e08). Merge it: append its share, take its amount if the row above
    has none, drop a duplicated amount."""
    if "share" not in fields:
        return
    key = [i for i, f in enumerate(fields) if f in {"serial", "owner", "survey_no", "extent_ha"}]
    rows, idx = [], []
    for r, ri in zip(pt.rows, pt.row_index):
        if rows and not any(i < len(r) and r[i] for i in key):
            prev = rows[-1]
            for i, f in enumerate(fields):
                if i >= len(r) or not r[i]:
                    continue
                if f == "share":
                    prev[i] = f"{prev[i]} {r[i]}" if prev[i] else r[i]
                elif f in {"amount_rs", "land_amount_rs", "tree_amount_rs"} and not prev[i]:
                    prev[i] = r[i]
            continue
        rows.append(list(r))
        idx.append(ri)
    pt.rows[:], pt.row_index[:] = rows, idx


def _spread_merged_cells(pt: PreparedTable, fields: list) -> None:
    """A cell merged over the whole table (company owner, patta) may be transcribed on any one row
    - or on the total row (dev2 e01). If the owner/patta column holds exactly one distinct value
    among data rows and the total row, fill every empty data row with it."""
    for f in ("owner", "patta_no"):
        if f not in fields:
            continue
        i = fields.index(f)
        vals = [r[i] for r in pt.rows if i < len(r) and r[i]]
        tot_vals = [r[i] for _, r in pt.totals if i < len(r) and r[i] and not TOTAL_RE.search(str(r[i]))
                    and cell_type(r[i]) == ("text" if f == "owner" else "int")]
        distinct = set(vals) | set(tot_vals)
        if len(distinct) == 1 and len(vals) < len(pt.rows) and len(pt.rows) >= 2:
            v = distinct.pop()
            # fill up to the first value (rows below are filled down, with evidence, in the row loop);
            # a value found only on the total row fills every row
            for r in pt.rows:
                if i < len(r) and r[i]:
                    break
                if i < len(r):
                    r[i] = v


def _move_unlabelled_totals(pt: PreparedTable, fields: list) -> None:
    """A row with extents but no serial, survey or owner, equal to the sum of the rows above it,
    is an unlabelled printed total (dev d01/d04: block total rows without "மொத்தம்")."""
    if "extent_ha" not in fields:
        return
    hi = fields.index("extent_ha")
    key_cols = [i for i, f in enumerate(fields) if f in {"serial", "survey_no", "owner", "sub_div"}]
    keep_rows, keep_idx, run, n_run = [], [], 0.0, 0
    last = len(pt.rows) - 1
    for k, (r, ri) in enumerate(zip(pt.rows, pt.row_index)):
        v = parse_extent_ha(r[hi]) if hi < len(r) else None
        # a blurred data row can lose its serial/survey too: a one-row "sum" only counts as a total
        # on the table's last row (equal plot extents are common on LDRs)
        if v is not None and keep_rows and not any(i < len(r) and r[i] for i in key_cols) \
                and abs(v - run) < 0.0006 and (n_run >= 2 or k == last):
            pt.totals.append((ri, r))
            run, n_run = 0.0, 0
            continue
        keep_rows.append(r)
        keep_idx.append(ri)
        run += v or 0.0
        n_run += 1
    pt.rows[:], pt.row_index[:] = keep_rows, keep_idx


def _parcel_totals(pt: PreparedTable, fields: list) -> dict:
    """Printed totals, read by cell TYPE rather than position: the "மொத்தம்" label often sits in
    the serial or survey column and shifts the values left (or the VLM shifts them), so the first
    hectare triple is the hectare total and a decimal after it is the acre total."""
    tot: dict = {}
    two_ha = "extent_ha_total" in fields
    for _, row in pt.totals:
        cells = [c for c in row if c]
        ha = [c for c in cells if cell_type(c) == "ha"]
        if ha and "extent_ha" not in tot:
            tot["extent_ha"] = parse_extent_ha(ha[-1] if two_ha and len(ha) >= 2 else ha[0])
            if two_ha and len(ha) >= 2:
                tot["extent_ha_total"] = parse_extent_ha(ha[0])
        if ha and "extent_ac" in fields and "extent_ac" not in tot:
            after = row[row.index(ha[0]) + 1:] if ha[0] in row else []
            dec = [c for c in after if c and cell_type(c) in {"dec", "int"}]
            if dec:
                tot["extent_ac"] = parse_extent_ac(dec[0])
        for c in cells:
            if cell_type(c) in {"money", "bignum"} and "amount_rs" in fields and "amount_rs" not in tot:
                tot["amount_rs"] = parse_amount(c)
        for i, f in enumerate(fields):
            if f == "cents" and i < len(row) and row[i] and "cents" not in tot:
                tot["cents"] = parse_cents(row[i])
    return {k: v for k, v in tot.items() if v is not None}


def _is_summary_table(pt: PreparedTable, doc_type: str | None) -> bool:
    """Proceedings/summary "items" tables: serial + description + extent ha/ac + amount, no survey
    column and no owner column (held-out: schema_invalid on every row)."""
    if table_signature(pt) not in {"parcel", "village_or_parcel"}:
        return False                    # payout / heirs tables (a VLM-invented "Description" header)
    cm = map_parcel_columns(pt, doc_type)
    if "survey_no" in cm.fields or any(t in {"survey", "share"} for t in cm.types):
        return False
    desc = [i for i, c in enumerate(pt.columns) if _DESC_LABEL.search(c or "")]
    has_values = any(f in cm.fields for f in ("extent_ha", "extent_ac", "amount_rs"))
    owner_named = any(f == "owner" and h.startswith("keyword") for f, h in zip(cm.fields, cm.how))
    return bool(desc) and has_values and not owner_named


def summary_records(pt: PreparedTable, doc_type: str | None, ctx: dict, table_index: int, bboxes: list,
                    method: str) -> tuple[list[dict], dict]:
    cm = map_parcel_columns(pt, doc_type)
    desc_i = next(i for i, c in enumerate(pt.columns) if _DESC_LABEL.search(c or ""))
    out = []
    for k, row in enumerate(pt.rows):
        raw = {f: row[i] for i, f in enumerate(cm.fields) if f and i < len(row)}
        desc = norm(row[desc_i]) if desc_i < len(row) else ""
        rec = {"record_type": "summary_item", "table_role": "summary",
               "serial": norm(raw.get("serial")).rstrip(".") or None if raw.get("serial") else None,
               "description": desc or None,
               "extent_ha": parse_extent_ha(raw["extent_ha"]) if raw.get("extent_ha") else None,
               "extent_ac": parse_extent_ac(raw["extent_ac"]) if raw.get("extent_ac") else None,
               "amount_rs": parse_amount(raw["amount_rs"]) if raw.get("amount_rs") else None,
               "raw": {kk: (None if v is None else str(v)) for kk, v in raw.items()}}
        if not (rec["description"] or rec["extent_ha"] is not None or rec["amount_rs"]):
            continue
        bi = pt.row_index[k]
        rec["evidence"] = _evidence(ctx, bboxes[bi] if bi < len(bboxes) else [0, 0, 1, 1], method, bi, table_index,
                                    row, 0.75)
        out.append(rec)
    return out, _parcel_totals(pt, cm.fields)


def heir_records(pt: PreparedTable, ctx: dict, table_index: int, bboxes: list, method: str) -> list[dict]:
    """Heirs tables (வ.எண் / வாரிசுதாரர்களின் பெயர் / இறந்தவருக்கான உறவுமுறை): one record per heir,
    owner = heir, relation as printed; no extents or amounts."""
    name_i = next((i for i, c in enumerate(pt.columns) if re.search(r"வாரிசு|பெயர்|name|heir", c or "", re.I)), None)
    rel_i = next((i for i, c in enumerate(pt.columns) if re.search(r"உறவு|relation", c or "", re.I)), None)
    ser_i = 0 if pt.columns and name_i != 0 else None
    out = []
    if name_i is None:
        return out
    for k, row in enumerate(pt.rows):
        name = norm(row[name_i]) if name_i < len(row) else ""
        if not name:
            continue
        rec = {"record_type": "parcel_row", "table_role": "heirs",
               "serial": norm(row[ser_i]).rstrip(".") or None if ser_i is not None and ser_i < len(row) else None,
               "survey_no": None, "sub_div": None, "part": False, "extent_ha": None, "extent_ac": None,
               "cents": None, "patta_no": None, "owner_raw": owner_display(name), "owners": _owners(name),
               "classification": None, "classification_raw": None, "amount_rs": None, "filled_down": [],
               "relation": norm(row[rel_i]) if rel_i is not None and rel_i < len(row) and row[rel_i] else None,
               "raw": {"owner": name}}
        bi = pt.row_index[k]
        rec["evidence"] = _evidence(ctx, bboxes[bi] if bi < len(bboxes) else [0, 0, 1, 1], method, bi, table_index,
                                    row, 0.7)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Village totals (GO / AS / LPS)
# ---------------------------------------------------------------------------
def village_records(pt: PreparedTable, ctx: dict, table_index: int, bboxes: list, method: str
                    ) -> tuple[list[dict], dict]:
    cmap = map_village_columns(pt)
    out: list[dict] = []
    unlabelled_total: dict = {}
    for k, row in enumerate(pt.rows):
        rec: dict = {"record_type": "village_totals", "serial": None, "village": None, "village_raw": None, "raw": {}}
        for i, f in enumerate(cmap.fields):
            if not f or i >= len(row):
                continue
            v = row[i]
            rec["raw"][f] = v
            if f == "serial":
                rec["serial"] = norm(v).rstrip(".") or None
            elif f == "village":
                rec["village_raw"] = norm(v)
                rec["village"] = normalise_village(v)
            elif f.endswith("_ha"):
                s = norm(v)
                rec[f] = 0.0 if s in {"0", "-", "--"} else parse_extent_ha(v)
            elif f.endswith("_ac"):
                s = norm(v)
                rec[f] = 0.0 if s in {"0", "-", "--"} else parse_extent_ac(v)
        if not rec.get("village_raw") and not any(rec.get(f) for f in rec if f.endswith(("_ha", "_ac"))):
            continue
        if not rec.get("village_raw") and not rec.get("serial") and k == len(pt.rows) - 1:
            # unlabelled last row with extents = the printed total (golden g01)
            for f, v in rec.items():
                if f.endswith(("_ha", "_ac")) and v is not None:
                    unlabelled_total[f] = v
            continue
        ok = sum(1 for f in ("village", "patta_ha", "total_ha") if rec.get(f) is not None)
        bi = pt.row_index[k]
        rec["evidence"] = _evidence(ctx, bboxes[bi] if bi < len(bboxes) else [0, 0, 1, 1], method, bi, table_index,
                                    row, _row_conf(ok, 3))
        out.append(rec)
    totals: dict = {}
    for _, row in pt.totals:
        for i, f in enumerate(cmap.fields):
            if f and i < len(row) and row[i] and f not in {"serial", "village"}:
                totals.setdefault(f, parse_extent_ha(row[i]) if f.endswith("_ha") else parse_extent_ac(row[i]))
    for k, v in unlabelled_total.items():
        totals.setdefault(k, v)
    return out, {k: v for k, v in totals.items() if v is not None}


# ---------------------------------------------------------------------------
# Form F / disbursement (owner + amount)
# ---------------------------------------------------------------------------
def owner_amount_records(pt: PreparedTable, ctx: dict, table_index: int, bboxes: list, method: str,
                         page_text: str, header: dict) -> tuple[list[dict], dict]:
    cmap = map_owner_amount_columns(pt)
    if not ({"owner", "amount_rs"} <= set(cmap.fields)) or \
            cmap.how[cmap.fields.index("owner")] not in {"keyword_exact", "keyword_fuzzy"}:
        # label/value tables (treasury advice: "Treasury/PAO | ST TUTICORIN") and DEBIT/CREDIT
        # summaries are not owner lists: the owner column must be named (name / பெயர் / owner)
        return [], {}
    words = None
    m = re.search(r"Rupees\s+([A-Za-z\s\-]+?)(?:Only|only|/-|\.|$)", page_text or "")
    if m:
        words = m.group(0).strip()
    words_val = parse_number_words(words) if words else None
    out = []
    for k, row in enumerate(pt.rows):
        raw = {f: row[i] for i, f in enumerate(cmap.fields) if f and i < len(row)}
        if not raw.get("owner") and not raw.get("amount_rs"):
            continue
        rec = {"record_type": "form_f", "serial": norm(raw.get("serial")).rstrip(".") or None,
               "owner_raw": owner_display(raw.get("owner")), "owners": _owners(raw.get("owner")),
               "amount_rs": parse_amount(raw.get("amount_rs")),
               "land_amount_rs": parse_amount(raw.get("land_amount_rs")) if raw.get("land_amount_rs") else None,
               "tree_amount_rs": parse_amount(raw.get("tree_amount_rs")) if raw.get("tree_amount_rs") else None, "amount_words": words,
               "amount_words_value": words_val,
               "award_ref": (header.get("ref_doc_no") or {}).get("value"),
               "award_date": (header.get("ref_doc_date") or {}).get("value"),
               "sec31_gazette_no": (header.get("gazette_no") or {}).get("value"),
               "sec31_date": (header.get("gazette_date") or {}).get("value"), "raw": raw}
        ok = sum(1 for f in ("owner_raw", "amount_rs") if rec.get(f))
        bi = pt.row_index[k]
        rec["evidence"] = _evidence(ctx, bboxes[bi] if bi < len(bboxes) else [0, 0, 1, 1], method, bi, table_index,
                                    row, _row_conf(ok, 2))
        out.append(rec)
    totals = {}
    for _, row in pt.totals:
        amt_cells = [c for c in row if c and cell_type(c) in {"money", "bignum"}]
        if amt_cells:
            totals["amount_rs"] = parse_amount(amt_cells[-1])
    if words_val:
        totals["amount_words_value"] = words_val
    return out, totals


# ---------------------------------------------------------------------------
# Instruments and rate notes (page-level records)
# ---------------------------------------------------------------------------
_MONEY_ANY = re.compile(r"(?:₹|Rs\.?|\*{2,})?\s*\d{1,3}(?:[,.]\d{2})*[,.]\d{3}(?:\.\d{2})?")


def instrument_record(tables: list[dict], page_text: str, ctx: dict) -> dict | None:
    cells = [norm(c) for t in tables for r in (t.get("rows") or []) for c in r if c] + \
            [norm(c) for t in tables for c in (t.get("columns") or []) if c]
    blob = "\n".join(cells) + "\n" + (page_text or "")
    cands = [parse_amount(m.group(0)) for m in _MONEY_ANY.finditer(blob)]
    cands = [c for c in cands if c and c >= 100]
    words_val = None
    m = re.search(r"(?:Rupees|On\s*Demand)(.{10,200}?)(?:only|$)", blob, re.IGNORECASE | re.S)
    if m:
        words_val = parse_number_words(m.group(1))
    amount = None
    if words_val and words_val in cands:
        amount = words_val
    elif cands:
        # prefer an amount that the words' tail agrees with (partly overprinted words)
        if words_val:
            tail = [c for c in cands if str(c).endswith(str(words_val)[-3:])]
            if tail:
                amount = max(tail)
        amount = amount or max(cands)
    payee = None
    pm = re.search(r"(THE\s+PRINCIPAL\s+DISTRICT\s+JUDGE[A-Z\s]*|DISTRICT\s+JUDGE[A-Z\s]*)", blob, re.I)
    if pm:
        payee = re.sub(r"\s+", " ", pm.group(1)).strip()
    dd = re.search(r"\b(\d{6,9})\b", "\n".join(cells))
    rec = {"record_type": "payment_instrument", "instrument_no": dd.group(1) if dd else None, "amount_rs": amount,
           "payee": payee, "payee_kind": "court" if payee and "JUDGE" in payee.upper() else None,
           "bank": "Indian Overseas Bank" if re.search(r"indian\s*overseas", blob, re.I) else None,
           "instrument_date": None, "raw": {"amount_candidates": [str(c) for c in cands][:10],
                                            "words_value": str(words_val) if words_val else None}}
    rec["evidence"] = _evidence(ctx, [0, 0, 1, 1], "page", None, None, [], 0.6 if amount else 0.2)
    return rec


def rate_record(tables: list[dict], page_text: str, header: dict, ctx: dict) -> dict:
    kv: dict[str, str] = {}
    for t in tables:
        for r in t.get("rows") or []:
            cells = [norm(c) for c in r if c]
            if len(cells) >= 2:
                kv[cells[0]] = " ".join(cells[1:])
    blob = "\n".join(f"{k}: {v}" for k, v in kv.items()) + "\n" + (page_text or "")
    blob1 = blob.replace("\n", " ")
    rec: dict = {"record_type": "price_rate", "committee_level": None, "rate_per_acre": None, "extent_ha": None,
                 "extent_ac": None, "villages": [], "survey_refs": None, "raw": {}}
    if re.search(r"STATE\s*LEVEL", blob1, re.I):
        rec["committee_level"] = "state"
    elif re.search(r"DISTRICT\s*LEVEL", blob1, re.I):
        rec["committee_level"] = "district"
    r = (header.get("rate_per_acre") or {}).get("value")
    rec["rate_per_acre"] = int(r) if r else None
    m = re.search(r"(\d{1,4}\s*\.\s*\d{2}\s*\.?\s*\d{0,2})\s*Hect[a-z]*\s*\(\s*([\d.]+)\s*Acres?", blob1, re.I)
    if m:
        rec["extent_ha"], rec["extent_ac"] = parse_extent_ha(m.group(1)), parse_extent_ac(m.group(2))
        rec["raw"]["extent"] = m.group(0)
    for key, fld in (("poramboke", "poramboke"), ("final", "final_patta")):
        mm = re.search(key + r"[^0-9]{0,60}?(\d{1,4}\s*\.\s*\d{2}\s*\.?\s*\d{0,2})\s*Hect[a-z]*\s*\(\s*([\d.]+)",
                       blob1, re.I)
        if mm:
            rec[f"{fld}_ha"], rec[f"{fld}_ac"] = parse_extent_ha(mm.group(1)), parse_extent_ac(mm.group(2))
    for k, v in kv.items():
        if re.search(r"village", k, re.I):
            rec["villages"] = [x for x in (normalise_village(p) for p in re.split(r"\band\b|,", v)) if x]
        if re.search(r"survey", k, re.I):
            rec["survey_refs"] = v
    rec["evidence"] = _evidence(ctx, [0, 0, 1, 1], "page", None, None, [], 0.6)
    return rec


_PAGE_CLASS_RE = re.compile(r"(?:வகைப்பாடு|வகைபாடு)\s*[:：]?\s*(புன்செய்|புஞ்சை|நன்செய்|நஞ்சை)|"
                            r"(புன்செய்|புஞ்சை|நன்செய்|நஞ்சை)\s*(?:நிலம்|நிலங்கள்|நிலங்களின்)")


_ROW_CLASS_RE = re.compile(r"\d{2,4}\s*/\s*\w{1,3}.{0,6}?(புன்செய்|புஞ்சை|நன்செய்|நஞ்சை)")
_PROSE_SURVEY_RE = re.compile(r"புல\s*(?:எண்|என்ன|எண|எளர்|என்|எனர்)\s*[:：.]?\s*(\d{1,4}\s*/\s*[0-9A-Za-z]{1,3}"
                              r"(?:\.\d{2}\.\d{2})?)")


_BLOCK_HEAD_RE = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[.)]\s*(?:புல\s*(?:எண்|என்|எளர்|எண)\s*[:：.]?\s*)?"
    r"(\d{1,4}\s*/\s*[0-9A-Za-z]{1,3}|\d{1,4})\b([^\n]*)")
_AMOUNT_RE = re.compile(r"ரூ\s*\.?\s*([\d]{1,3}(?:,\d{2})*,\d{3}|\d{5,9})(?:\.\d{2})?\s*/?-?")
_RATE_CONTEXT = re.compile(r"(ஏக்கருக்கு|ஏக்கர்\s*ஒன்றுக்கு|per\s*acre|ஹெக்டேருக்கு)\s*$", re.I)
_COMP_CONTEXT = re.compile(r"இழப்பீ|தொகை|வழங்க|compensation", re.I)


def apply_block_prose(records: list[dict], page_text: str) -> int:
    """Per-owner-block award pages (7(2)): "N. புல எண்: 224/1 - <owners>:" heading, a small table,
    then prose "... இழப்பீடு ரூ.8,09,000/-ஐ வழங்குவது ...". Each compensation amount belongs to the
    nearest preceding block heading; the heading's owner text fills rows whose table has no owner
    column. Amounts before the first heading (the previous page's block) and per-acre rates are
    ignored. Returns the number of values filled."""
    text = page_text or ""
    heads = []
    for m in _BLOCK_HEAD_RE.finditer(text):
        tail = m.group(3) or ""
        if not re.search(r"புல|[-–]|:\s*$", m.group(0)) and "/" not in m.group(2):
            continue
        refs = parse_survey_ref(m.group(2))
        if not refs:
            continue
        owner = None
        om = re.match(r"\s*[-–]\s*(.+?)\s*:?\s*$", tail)
        if om and len(om.group(1)) >= 4:
            owner = om.group(1)
            nxt = text[m.end():m.end() + 120].split("\n")
            if not tail.rstrip().endswith(":") and len(nxt) > 1 and nxt[1].rstrip().endswith(":") and len(nxt[1]) < 80:
                owner = f"{owner} {nxt[1].rstrip(': ')}"
        heads.append((m.start(), refs[0].survey_no, canon_subdiv(refs[0].sub_div), owner))
    if not heads:
        return 0
    filled = 0
    amounts: dict[tuple, int] = {}
    for m in _AMOUNT_RE.finditer(text):
        before = text[max(0, m.start() - 80):m.start()]
        if _RATE_CONTEXT.search(before) or not _COMP_CONTEXT.search(before + text[m.end():m.end() + 30]):
            continue
        prior = [h for h in heads if h[0] < m.start()]
        if not prior:
            continue
        key = (prior[-1][1], prior[-1][2])
        amounts.setdefault(key, parse_amount(m.group(1)))
    for r in records:
        if r.get("record_type") != "parcel_row" or not r.get("survey_no"):
            continue
        key = (r["survey_no"], canon_subdiv(r.get("sub_div")))
        if r.get("amount_rs") is None and key in amounts and r.get("table_role") != "published_as":
            r["amount_rs"] = amounts[key]
            r.setdefault("filled_down", []).append("amount:block_prose")
            filled += 1
        if not r.get("owner_raw"):
            h = next((h for h in heads if (h[1], h[2]) == key and h[3]), None)
            if h:
                r["owner_raw"] = owner_display(h[3])
                r["owners"] = _owners(h[3])
                r.setdefault("filled_down", []).append("owner:block_heading")
                filled += 1
    return filled


_TA_WORD = re.compile(r"[\u0B80-\u0BFF]{3,}")
_INVALID_TA = re.compile(r"[\u0B85-\u0B94][\u0BBE-\u0BCD]|[\u0BBE-\u0BCD]{2}")


def apply_tesseract_owner_votes(records: list[dict], page_text: str, min_ratio: int = 75) -> int:
    """Two-engine vote on owner words (plan R2): Tesseract reads printed Tamil more faithfully
    than the VLM, which paraphrases names ("ஏற் சென்ஸ்" for "எர்த் சென்ஸ்"). Each Tamil word of a
    VLM owner string is replaced by the closest word of the page's Tesseract text when that word
    is close (ratio ≥ min_ratio) but not identical. Relation markers and numbers are untouched.
    The VLM text is kept in `owner_vlm_raw`. Returns the number of owners changed."""
    from rapidfuzz import fuzz, process

    from collections import Counter

    words = [w for w in _TA_WORD.findall(re.sub(r"[\u200b-\u200d]", "", page_text or ""))
             if not _INVALID_TA.search(w)]          # OCR debris such as "இி…"
    freq = Counter(words)
    vocab = sorted(freq)
    if len(vocab) < 20:
        return 0
    cache: dict[str, str] = {}

    def fix(word: str) -> str:
        if word in cache:
            return cache[word]
        # never swap a word for an inflected form of itself ("பெயர்" → "பெயரில்" in prose)
        stem = word.rstrip("\u0BCD")
        cands = [c for c in process.extract(word, vocab, scorer=fuzz.ratio, score_cutoff=min_ratio, limit=5)
                 if abs(len(c[0]) - len(word)) <= 3 and not c[0].startswith(stem) and not word.startswith(c[0].rstrip("\u0BCD"))]
        out = word
        if cands and word not in freq:
            # the closest word; ties broken by how often Tesseract read it on this page
            out = max(cands, key=lambda c: (round(c[1]), freq[c[0]]))[0]
        cache[word] = out
        return out

    changed = 0
    for r in records:
        own = r.get("owner_raw")
        if not own or r.get("record_type") not in {"parcel_row", "form_f"}:
            continue
        new = _TA_WORD.sub(lambda m: fix(m.group(0)), own)
        if new != own:
            r["owner_vlm_raw"] = own
            r["owner_raw"] = new
            r["owners"] = _owners(new.replace(" ; ", "\n"))
            r.setdefault("filled_down", []).append("owner:tesseract_vote")
            changed += 1
    return changed


def apply_prose_survey(records: list[dict], page_text: str) -> None:
    """A compensation table without a survey column inherits the survey named in the prose above
    it ("மேற்படி புல எண்: 173/1 விஸ்தீரணம் ..."), only when the page names exactly one."""
    found: set[tuple[str, str | None]] = set()
    for m in _PROSE_SURVEY_RE.findall(page_text or ""):
        m = re.sub(r"\s+", "", m)
        if re.search(r"\.\d{2}\.\d{2}$", m):           # "173/20.86.00" run together (§11b)
            sp = split_merged_survey_extent(m.split("/", 1)[1])
            if not sp:
                continue
            found.add((m.split("/", 1)[0], canon_subdiv(sp[0])))
            continue
        refs = parse_survey_ref(m)
        if refs:
            found.add((refs[0].survey_no, canon_subdiv(refs[0].sub_div)))
    present = {(r.get("survey_no"), canon_subdiv(r.get("sub_div"))) for r in records if r.get("survey_no")}
    cand = found - present
    rows = [r for r in records if r.get("record_type") == "parcel_row" and r.get("survey_no") is None]
    if len(cand) != 1 or not rows:
        return
    sno, sub = cand.pop()
    for r in rows:
        r["survey_no"], r["sub_div"] = sno, sub
        r.setdefault("filled_down", []).append("survey:page_text")
    return
    refs = parse_survey_ref(found.pop())
    if not refs:
        return
    for r in rows:
        r["survey_no"], r["sub_div"] = refs[0].survey_no, refs[0].sub_div
        r.setdefault("filled_down", []).append("survey:page_text")


def apply_page_classification(records: list[dict], page_text: str) -> None:
    """Rows without a classification cell inherit the page's single stated classification
    ("வகைபாடு: புன்செய்", "புஞ்சை நிலம்"). Skipped when the page names more than one class."""
    found = {normalise_classification(a or b) for a, b in _PAGE_CLASS_RE.findall(page_text or "")}
    found.discard(None)
    if not found:
        # OCR table lines "172/2 புன்செய் 0.19.50 ...": ≥3 survey lines all naming one class
        row_cls = [normalise_classification(m.group(1)) for ln in (page_text or "").split("\n")
                   for m in [_ROW_CLASS_RE.search(ln)] if m]
        row_cls = [c for c in row_cls if c]
        if len(row_cls) >= 3 and len(set(row_cls)) == 1:
            found = {row_cls[0]}
    if len(found) != 1:
        return
    cls = found.pop()
    for r in records:
        if r.get("record_type") == "parcel_row" and r.get("classification") is None and r.get("survey_no"):
            r["classification"] = cls
            r.setdefault("filled_down", []).append("classification:page_text")


def build_records(vlm: dict, doc_type: str | None, family: str, ctx: dict, page_text: str, header: dict,
                  table_bboxes: list[tuple[list, str]]) -> tuple[list[dict], dict, list[str]]:
    """Dispatch every VLM table to the mapper for its content signature. Returns
    (records, printed_totals, flags)."""
    tables = vlm.get("tables") or []
    records: list[dict] = []
    totals: dict = {}
    flags: list[str] = []
    if family == "instrument":
        rec = instrument_record(tables, page_text, ctx)
        return ([rec] if rec else []), ({"amount_rs": rec["amount_rs"]} if rec and rec["amount_rs"] else {}), flags
    if family == "rate":
        rec = rate_record(tables, page_text, header, ctx)
        tot = {k: rec.get(k) for k in ("extent_ha", "extent_ac", "poramboke_ha", "poramboke_ac", "final_patta_ha",
                                       "final_patta_ac") if rec.get(k) is not None}
        return [rec], tot, flags
    prepared = [prepare_table(t) for t in tables]
    errata = doc_type == "SEC32_ERRATA"
    if errata:
        prepared, roles = _split_errata(prepared)
    else:
        roles = [None] * len(prepared)
    prev_survey = None
    per_table: dict[int, dict] = {}
    for ti, pt in enumerate(prepared):
        if not pt.rows and not pt.totals:
            continue
        bbs, method = table_bboxes[ti] if ti < len(table_bboxes) else ([], "page")
        sig = table_signature(pt)
        if sig == "heirs" and family == "parcel":
            records.extend(heir_records(pt, ctx, ti, bbs, method))
            continue
        if family == "village_totals" and sig in {"village_or_parcel", "parcel", "other"}:
            recs, tot = village_records(pt, ctx, ti, bbs, method)
        elif family == "owner_amount" and sig not in {"parcel"}:
            recs, tot = owner_amount_records(pt, ctx, ti, bbs, method, page_text, header)
        elif family == "parcel" and _is_summary_table(pt, doc_type):
            recs, tot = summary_records(pt, doc_type, ctx, ti, bbs, method)
        elif sig in {"parcel", "village_or_parcel"} or family == "parcel":
            role = roles[ti]
            if doc_type == "AWARD_7_3" and _already_paid(page_text):
                role = "already_paid_7_2"
            elif role is None and sig == "owner_amount":
                role = "compensation"
            recs, tot = parcel_records(pt, doc_type, ctx, ti, bbs, method, table_role=role)
            surveys = {(r.get("survey_no"), r.get("sub_div")) for r in recs if r.get("survey_no")}
            if role == "compensation" and prev_survey and not surveys:
                # a per-owner compensation table follows its block's parcel table (no survey column)
                for r in recs:
                    r["survey_no"], r["sub_div"] = prev_survey
                    r.setdefault("filled_down", []).append("survey:previous_table")
            elif role != "compensation":
                prev_survey = next(iter(surveys)) if len(surveys) == 1 else None
        elif sig == "owner_amount":
            recs, tot = owner_amount_records(pt, ctx, ti, bbs, method, page_text, header)
        else:
            continue
        records.extend(recs)
        if tot:
            per_table[ti] = tot
        # printed totals of a compensation / "already published" table do not total the page's
        # main parcel table (golden g05: compensation total 2.35 ac vs parcel row 2.12 ac)
        if not (recs and recs[0].get("table_role") in {"compensation", "published_as"}):
            for k, v in tot.items():
                totals.setdefault(k, v)
    if errata:
        records, eflags = _pair_errata(records, ctx)
        flags += eflags
    if ctx.get("prose_rows_dropped"):
        flags.append(f"prose_as_table:{ctx['prose_rows_dropped']}")
    if per_table:
        totals["_per_table"] = per_table
    return records, totals, flags


def _already_paid(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"7\s*\(\s*2\s*\)", t) and re.search(r"(ஏற்கனவே|பெற்றுக்கொண்ட|பெற்றுள்ள|வழங்கப்பட்டுள்ள)", t))


def _split_errata(prepared: list[PreparedTable]) -> tuple[list[PreparedTable], list[str | None]]:
    """Errata pages print 'already published' (left) and 'to be read as' (right). Two tables →
    roles by order; one table whose labels repeat → split into halves."""
    if len(prepared) >= 2:
        return prepared, ["published_as", "should_read_as"] + [None] * (len(prepared) - 2)
    if len(prepared) == 1:
        pt = prepared[0]
        n = len(pt.columns)
        h = n // 2
        if n >= 6 and h >= 3:
            left = PreparedTable(pt.columns[:h], [r[:h] for r in pt.rows], pt.row_index, pt.totals, pt.n_vlm_rows)
            right = PreparedTable(pt.columns[h:], [r[h:] for r in pt.rows], pt.row_index, pt.totals, pt.n_vlm_rows)
            return [left, right], ["published_as", "should_read_as"]
    return prepared, [None] * len(prepared)


def _pair_errata(records: list[dict], ctx: dict) -> tuple[list[dict], list[str]]:
    pub = [r for r in records if r.get("table_role") == "published_as"]
    new = [r for r in records if r.get("table_role") == "should_read_as"]
    if not new:
        return records, ["errata_unpaired"]
    out = list(records)
    for i, r in enumerate(new):
        p = next((x for x in pub if x.get("serial") and x.get("serial") == r.get("serial")), None)
        if p is None and i < len(pub):
            p = pub[i]
        # the right half often lacks survey/extent (same as published): inherit them
        if p is not None:
            for f in ("serial", "survey_no", "sub_div", "extent_ha", "classification", "classification_raw"):
                if r.get(f) is None and p.get(f) is not None:
                    r[f] = p[f]
            if not r.get("owner_raw") and p.get("owner_raw"):
                r["owner_raw"], r["owners"] = p["owner_raw"], p["owners"]
        changed = [f for f in ("survey_no", "sub_div", "extent_ha", "owner_raw", "trees", "buildings",
                               "classification") if p is not None and r.get(f) != p.get(f)]
        out.append({"record_type": "errata_correction", "published_as": p, "should_read_as": r,
                    "changed_fields": changed, "gazette_ref": None, "evidence": r["evidence"]})
    return out, []
