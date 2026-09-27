"""Owner-name safety (D-032): per-record read status and the deterministic owner sample.

Owner names are the field least protected by arithmetic checks (golden g08 was auto-accepted
with most names misread). So an owner value is `vlm_agreed` only when two independent reads
agree (rapidfuzz token_sort_ratio ≥ 90 on punctuation-stripped text with canonical relation
markers); otherwise `vlm_single` with confidence capped at 0.8. 10% of auto-accepted pages that
carry owners are sampled into the review queue (reason `owner_sample`), by hash of the page image,
so the sample is stable across re-runs.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

AGREE_THRESHOLD = 90
SINGLE_READ_CAP = 0.8
SAMPLE_MODULUS = 10          # 1 in 10 pages

_REL = [(r"த\s*[/.]\s*ப(?:ெ)?\.?", " தபெ "), (r"க\s*[/.]\s*ப(?:ெ)?\.?", " கபெ "), (r"S\s*/\s*o\.?", " so "),
        (r"W\s*/\s*o\.?", " wo "), (r"D\s*/\s*o\.?", " do ")]


def owner_key(s: str | None) -> str:
    s = re.sub(r"[​-‍﻿]", "", unicodedata.normalize("NFC", s or ""))
    for rx, rep in _REL:
        s = re.sub(rx, rep, s, flags=re.I)
    s = re.sub(r"(^|\s|;)\(?\d{1,2}[.)]\s*", " ", s)
    s = re.sub(r"[.,;:()\-/]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def owners_agree(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    ka, kb = owner_key(a), owner_key(b)
    return fuzz.token_sort_ratio(ka, kb) >= AGREE_THRESHOLD or \
        fuzz.ratio(ka.replace(" ", ""), kb.replace(" ", "")) >= AGREE_THRESHOLD


def _match_key(r: dict, i: int):
    if r.get("record_type") == "parcel_row" and r.get("survey_no"):
        return ("p", r.get("survey_no"), r.get("sub_div"), r.get("table_role"))
    return (r.get("record_type"), r.get("serial") or i)


def annotate_owner_reads(records: list[dict], other: list[dict] | None) -> dict:
    """Set `owner_read_status` / `owner_confidence` on every record with an owner. `other` =
    records of the other read (None when only one read exists). Returns counts."""
    index: dict = {}
    for i, r in enumerate(other or []):
        if r.get("owner_raw"):
            index.setdefault(_match_key(r, i), r)
    counts = {"vlm_agreed": 0, "vlm_single": 0}
    for i, r in enumerate(records):
        if not r.get("owner_raw"):
            continue
        conf = (r.get("evidence") or {}).get("confidence", 0.0)
        o = index.get(_match_key(r, i))
        if o is not None and owners_agree(r["owner_raw"], o.get("owner_raw")):
            r["owner_read_status"], r["owner_confidence"] = "vlm_agreed", round(conf, 3)
        else:
            r["owner_read_status"], r["owner_confidence"] = "vlm_single", round(min(conf, SINGLE_READ_CAP), 3)
            if o is not None and o.get("owner_raw"):
                r["owner_other_read"] = o["owner_raw"]
        counts[r["owner_read_status"]] += 1
    return counts


def in_owner_sample(image_sha: str) -> bool:
    return int(image_sha[:12], 16) % SAMPLE_MODULUS == 0
