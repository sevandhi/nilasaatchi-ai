"""Self-consistency checks (phase2 skill): printed totals, ha↔ac (2.47 or 2.47105), amount = ac × rate,
village-total arithmetic, Form F amount words, date range. Produces `checks[]`,
`self_consistency: pass|fail|n/a` and a page confidence."""
from __future__ import annotations

from pipeline.normalize.numbers import compensation_matches, extents_agree

TOTAL_TOL_HA = 0.0005


SERIAL_RUN_MIN = 3  # a fresh read of b8award p3 put the S.No. column (1..7) into survey_no


def serial_run(rows: list[dict]) -> int | None:
    """Length of the leading run of rows whose survey_no reads 1, 2, 3, ... with no subdivision;
    None when there are too few rows to tell."""
    if len(rows) < SERIAL_RUN_MIN:
        return None
    run = 0
    for i, r in enumerate(rows):
        if str(r.get("survey_no") or "").strip() == str(i + 1) and not r.get("sub_div"):
            run += 1
        else:
            break
    return run


def _chk(name: str, ok: bool | None, detail: str | None = None) -> dict:
    return {"name": name, "result": "skip" if ok is None else ("pass" if ok else "fail"), "detail": detail}


def _table_of(r: dict):
    return (r.get("evidence") or {}).get("table_index")


def run_checks(records: list[dict], totals: dict, header: dict, family: str,
               table_totals: dict | None = None) -> list[dict]:
    checks: list[dict] = []
    rows = [r for r in records if r.get("record_type") == "parcel_row"
            and r.get("table_role") not in {"published_as", "compensation", "heirs"}]
    if family == "parcel" and rows:
        # printed totals are checked per table (multi-block award pages print one total per block);
        # without per-table totals, fall back to the page total
        groups = ([(ti, [r for r in rows if _table_of(r) == int(ti)], t) for ti, t in (table_totals or {}).items()]
                  if table_totals else [(None, rows, totals)])
        bad_ha, bad_ac, n_ha, n_ac = [], [], 0, 0
        for ti, grp, tot in groups:
            ha = [r["extent_ha"] for r in grp if r.get("extent_ha") is not None]
            if tot.get("extent_ha") is not None and ha:
                n_ha += 1
                s = round(sum(ha), 4)
                if abs(s - tot["extent_ha"]) > TOTAL_TOL_HA:
                    bad_ha.append(f"table {ti}: rows={s} printed={tot['extent_ha']}")
            ac = [r["extent_ac"] for r in grp if r.get("extent_ac") is not None]
            if tot.get("extent_ac") is not None and ac and len(ac) == len(grp):
                n_ac += 1
                s = round(sum(ac), 3)
                if abs(s - tot["extent_ac"]) > 0.011 * len(ac):
                    bad_ac.append(f"table {ti}: rows={s} printed={tot['extent_ac']}")
        if n_ha:
            checks.append(_chk("sum_extent_ha_eq_printed_total", not bad_ha, "; ".join(bad_ha[:4]) or None))
        if n_ac:
            checks.append(_chk("sum_extent_ac_eq_printed_total", not bad_ac, "; ".join(bad_ac[:4]) or None))
        # one land rate per page: amount / acres agrees across rows (no rate is assumed)
        rates = [r["amount_rs"] / r["extent_ac"] for r in records if r.get("record_type") == "parcel_row"
                 and r.get("amount_rs") and r.get("extent_ac")]
        if len(rates) >= 2:
            lo, hi = min(rates), max(rates)
            checks.append(_chk("amount_per_acre_consistent", (hi - lo) / hi <= 0.01,
                               f"rate range {lo:.0f}–{hi:.0f}" if (hi - lo) / hi > 0.01 else None))
        bad = [f"{r.get('survey_no')}/{r.get('sub_div')}: {r['extent_ha']} ha vs {r['extent_ac']} ac"
               for r in rows if extents_agree(r.get("extent_ha"), r.get("extent_ac")) is False]
        pairs = [r for r in rows if extents_agree(r.get("extent_ha"), r.get("extent_ac")) is not None]
        if pairs:
            checks.append(_chk("ha_ac_agree", not bad, "; ".join(bad[:5]) or None))
        rate = (header.get("rate_per_acre") or {}).get("value")
        amt_rows = [r for r in records if r.get("record_type") == "parcel_row" and r.get("amount_rs")
                    and r.get("extent_ac")]
        if rate and amt_rows:
            bad = [f"{r['extent_ac']} ac × {rate} ≠ {r['amount_rs']}" for r in amt_rows
                   if not compensation_matches(r["amount_rs"], r["extent_ac"], int(rate))]
            checks.append(_chk("amount_eq_ac_x_rate", not bad, "; ".join(bad[:5]) or None))
        missing = [i for i, r in enumerate(rows) if r.get("survey_no") is None]
        checks.append(_chk("rows_have_survey", not missing, f"{len(missing)} rows without survey" if missing else None))
        run = serial_run(rows)
        if run is not None:
            checks.append(_chk("survey_not_serial", run < SERIAL_RUN_MIN,
                               f"first {run} survey numbers read as 1..{run} (row serial column?)" if run >= SERIAL_RUN_MIN else None))
    if family == "village_totals":
        vt = [r for r in records if r.get("record_type") == "village_totals"]
        bad = []
        for r in vt:
            parts = [r.get("patta_ha"), r.get("poramboke_ha")]
            if r.get("total_ha") is not None and all(p is not None for p in parts):
                if abs(sum(parts) + (r.get("wet_ha") or 0) - r["total_ha"]) > TOTAL_TOL_HA:
                    bad.append(f"{r.get('village')}: {parts} ≠ {r['total_ha']}")
        if any(r.get("total_ha") is not None for r in vt):
            checks.append(_chk("village_parts_eq_total", not bad, "; ".join(bad[:5]) or None))
        for cat in ("patta", "poramboke", "total"):
            printed = totals.get(f"{cat}_ha")
            vals = [r.get(f"{cat}_ha") for r in vt if r.get(f"{cat}_ha") is not None]
            if printed is not None and vals:
                s = round(sum(vals), 4)
                checks.append(_chk(f"sum_{cat}_ha_eq_printed_total", abs(s - printed) <= 0.002,
                                   f"rows={s} printed={printed}"))
        bad = []
        n = 0
        for r in vt:
            for cat in ("patta", "poramboke", "total"):
                a = extents_agree(r.get(f"{cat}_ha"), r.get(f"{cat}_ac"))
                if a is not None:
                    n += 1
                    if not a:
                        bad.append(f"{r.get('village')} {cat}: {r.get(f'{cat}_ha')} ha vs {r.get(f'{cat}_ac')} ac")
        if n:
            checks.append(_chk("ha_ac_agree", not bad, "; ".join(bad[:5]) or None))
        unknown = [r.get("village_raw") for r in vt if r.get("village") is None]
        checks.append(_chk("villages_resolved", not unknown, ", ".join(str(u) for u in unknown[:5]) or None))
    if family == "owner_amount":
        ff = [r for r in records if r.get("record_type") == "form_f"]
        amts = [r["amount_rs"] for r in ff if r.get("amount_rs")]
        if totals.get("amount_rs") and amts:
            checks.append(_chk("sum_amount_eq_printed_total", sum(amts) == totals["amount_rs"],
                               f"rows={sum(amts)} printed={totals['amount_rs']}"))
        if totals.get("amount_words_value") and (totals.get("amount_rs") or amts):
            target = totals.get("amount_rs") or sum(amts)
            checks.append(_chk("amount_words_eq_figures", totals["amount_words_value"] == target,
                               f"words={totals['amount_words_value']} figures={target}"))
    if family == "instrument":
        inst = [r for r in records if r.get("record_type") == "payment_instrument"]
        w = inst[0]["raw"].get("words_value") if inst else None
        if inst and w and inst[0].get("amount_rs"):
            checks.append(_chk("amount_words_eq_figures", str(inst[0]["amount_rs"]) == w, None))
    if family == "rate":
        pr = [r for r in records if r.get("record_type") == "price_rate"]
        if pr and pr[0].get("extent_ha") is not None and pr[0].get("extent_ac") is not None:
            checks.append(_chk("ha_ac_agree", extents_agree(pr[0]["extent_ha"], pr[0]["extent_ac"]), None))
    return checks


# advisory checks: they trigger a second read and count when comparing two reads, but never make a
# page "fail" (so they cannot send it to review under D-033)
ADVISORY_CHECKS = {"villages_resolved", "acre_column_empty"}


def acre_column_check(records: list[dict], column_labels: list[str]) -> dict | None:
    """A header promising acres (ஏக்கர் / acre) with no acre value on any parcel row: the VLM merged
    or dropped the acre column (golden g08: "விஸ்தீரணம் / ஹெக்டேர் & ஏக்கர்" as one column)."""
    import re
    if not any(re.search(r"ஏக்|acre", c or "", re.I) for c in column_labels):
        return None
    rows = [r for r in records if r.get("record_type") == "parcel_row" and r.get("extent_ha") is not None
            and r.get("table_role") not in {"compensation", "heirs"}]
    if not rows:
        return None
    ok = any(r.get("extent_ac") is not None for r in rows)
    return _chk("acre_column_empty", ok, None if ok else f"{len(rows)} rows with ha, none with acres")


def summarise(checks: list[dict]) -> str:
    graded = [c for c in checks if c["result"] != "skip" and c["name"] not in ADVISORY_CHECKS]
    if not graded:
        return "n/a"
    return "fail" if any(c["result"] == "fail" for c in graded) else "pass"


def page_confidence(records: list[dict], checks: list[dict], header: dict, family: str,
                    vlm_meta: dict) -> float:
    """Blend of row confidences, check outcomes, header conflicts and the VLM's own legibility
    report. Calibrated only on the 20 golden pages — treat as an ordering, not a probability."""
    rc = [r["evidence"]["confidence"] for r in records if r.get("evidence")]
    base = sum(rc) / len(rc) if rc else (0.5 if family in {"rate", "prose"} else 0.2)
    graded = [c for c in checks if c["result"] != "skip"]
    if graded:
        passed = sum(1 for c in graded if c["result"] == "pass") / len(graded)
        base *= 0.7 + 0.3 * passed
        if any(c["result"] == "fail" and c["name"].startswith("sum_") for c in graded):
            base *= 0.8
    if any((header.get(k) or {}).get("conflict") for k in ("village", "unit_no", "block_no")):
        base *= 0.85
    leg = (vlm_meta or {}).get("legibility")
    if leg == "blurred":
        base *= 0.6
    elif leg == "partly_blurred":
        base *= 0.85
    if (vlm_meta or {}).get("handwritten"):
        base *= 0.9                      # weak signal: set on printed pages with signatures too
    return round(max(0.0, min(1.0, base)), 3)
