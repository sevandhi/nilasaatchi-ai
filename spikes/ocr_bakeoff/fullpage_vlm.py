"""Candidate (g): whole-page VLM transcription via the router (task table_read_pii → Bedrock Ministral 3 8B)."""
import csv
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image

from app.router import call

PUBLIC = {"GO", "AS", "LPS"}
SCHEMA = {
    "type": "object",
    "properties": {
        "header": {"type": "object", "properties": {
            "village": {"type": ["string", "null"]}, "unit_no": {"type": ["string", "null"]},
            "block_no": {"type": ["string", "null"]}, "doc_no": {"type": ["string", "null"]},
            "doc_date": {"type": ["string", "null"]}}},
        "tables": {"type": "array", "items": {"type": "object", "properties": {
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "null"]}}}}}},
    },
    "required": ["tables"],
}
PROMPT = (
    "This is a scanned Tamil Nadu land-acquisition document page (Tamil and/or English). "
    "Transcribe EVERY table on the page. For each table give the column headers exactly as printed "
    "and every row as a list of cell strings exactly as printed (keep Tamil script, keep numbers like "
    "0.95.50 or 1,35,496 exactly). Use null for empty or illegible cells; never guess. Also give the "
    "header fields (village, unit/அலகு, block/பிளாக், document number, date) if printed. Return JSON only."
)


def main(out_path: str, only: str = "") -> None:
    pages = list(csv.DictReader(open("eval/golden/mini_pages.csv")))
    if only:
        pages = [p for p in pages if p["page_id"] in only.split(",")]
    out = json.loads(Path(out_path).read_text()) if Path(out_path).exists() else {}
    for p in pages:
        pid = p["page_id"]
        im = Image.open(f"eval/golden/pages/{pid}.png").convert("RGB")
        im.thumbnail((1800, 1800))
        b = io.BytesIO(); im.save(b, "JPEG", quality=90)
        tier = "PUBLIC" if p["doc_type_hint"] in PUBLIC else "PII"
        t = time.time()
        r = call("table_read_pii", {"instruction": PROMPT, "max_tokens": 4096}, schema=SCHEMA, privacy_tier=tier, images=[b.getvalue()])
        out[pid] = {"ok": r.ok, "model": r.model_id, "secs": round(time.time() - t, 1), "tin": r.tokens_in,
                    "tout": r.tokens_out, "cost": r.shadow_cost_usd, "data": r.data, "text": None if r.data else r.text,
                    "error": r.error}
        print(pid, p["doc_type_hint"], r.ok, r.model_id, out[pid]["secs"], "s", r.tokens_in, r.tokens_out, (r.error or "")[:80], flush=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
