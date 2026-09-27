"""JSON-schema validation against schemas/extraction/*.json (resolved through a local registry)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "extraction"
BASE = "https://nilasaatchi.local/schemas/extraction/"
RECORD_SCHEMA = {"parcel_row": "parcel_row.json", "form_f": "form_f.json", "village_totals": "village_totals.json",
                 "payment_instrument": "payment_instrument.json", "price_rate": "price_rate.json",
                 "errata_correction": "errata_correction.json", "summary_item": "summary_item.json"}
# doc-type specific row schemas (allOf parcel_row + required fields)
DOC_ROW_SCHEMA = {"AWARD_7_2": "award_row.json", "AWARD_7_3": "award_row.json", "SEC32_NOTICE": "notice_row.json",
                  "SEC32_ERRATA": "notice_row.json", "LDR": "land_delivery_row.json", "CHITTA": "chitta_row.json",
                  "FORM_E": "form_e_row.json"}


@lru_cache(maxsize=1)
def registry() -> Registry:
    resources = []
    for p in SCHEMA_DIR.glob("*.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        resources.append((doc.get("$id", BASE + p.name), Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    doc = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    return Draft202012Validator(doc, registry=registry())


def errors(obj: dict, name: str) -> list[str]:
    """Path + failed keyword + expected value only. Never `e.message`: for root-level errors it
    embeds a repr of the whole instance, owner names included (PII would leak into row_json)."""
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.validator}={str(e.validator_value)[:80]}"
            for e in validator(name).iter_errors(obj)]


def validate_record(rec: dict, doc_type: str | None) -> list[str]:
    rt = rec.get("record_type")
    name = DOC_ROW_SCHEMA.get(doc_type or "", RECORD_SCHEMA.get(rt)) if rt == "parcel_row" else RECORD_SCHEMA.get(rt)
    if not name:
        return [f"unknown record_type {rt!r}"]
    return errors(rec, name)
