""""In simple words" for the cloud demo: the same prompt as the local app (app/common/explain.py), written by
AWS Bedrock (Ministral 3 8B) from the Lambda role. Order: explanation cached in the snapshot (made locally) →
/tmp cache → Bedrock → the standard template. Only the finding's facts are sent (never owner rows)."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from app.cloud.config import get_cloud_settings
from app.common.explain import PROMPT_VERSION, SCHEMA, build_prompt, template

TMP = Path(tempfile.gettempdir()) / "nilasaatchi-explain"
MODEL_ID = "mistral.ministral-3-8b-instruct"


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if all(k in data for k in SCHEMA["required"]) else None


SKELETON = {
    "headline": "one plain sentence",
    "what_was_found": "3-4 short sentences",
    "numbers_explained": [{"number": "the number with its unit", "meaning": "what it means, in plain words"}],
    "why_flagged": "2-3 sentences",
    "other_explanations": ["an innocent or technical reason", "another reason"],
    "how_sure": "2-3 sentences",
    "what_next": ["a concrete step", "another step"],
    "bottom_line": "one or two sentences",
}


def _bedrock(prompt: str) -> dict | None:
    import boto3

    instruction = ("\n\nReply with ONLY a JSON object that has EXACTLY these keys and this shape (fill in the values, "
                   "no other keys, no nesting beyond this, no markdown):\n" + json.dumps(SKELETON, ensure_ascii=False, indent=1))
    client = boto3.client("bedrock-runtime", region_name=get_cloud_settings().aws_region)
    resp = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt + instruction}]}],
        inferenceConfig={"maxTokens": 1400, "temperature": 0.2},
    )
    text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
    return _parse(text)


def explain(store, finding_id: int, lang: str = "en") -> dict | None:
    pack_doc = store.read_json(f"findings/{finding_id}__evidence-pack.json")
    if not pack_doc:
        return None
    pack = pack_doc.get("pack") or pack_doc
    name = f"{finding_id}_{pack.get('rule_version') or 'v'}_{PROMPT_VERSION}_{lang}.json"
    cached = store.read_json(f"summaries/{name}")
    if cached:
        return {**cached, "cached": True}
    TMP.mkdir(parents=True, exist_ok=True)
    local = TMP / name
    if local.exists():
        return {**json.loads(local.read_text()), "cached": True}
    try:
        data = _bedrock(build_prompt(pack, lang))
    except Exception:  # noqa: BLE001 - the explanation is a convenience; never fail the page
        data = None
    if not data:
        return {**template(pack), "cached": False}
    out = {**data, "source": "bedrock-ministral-3-8b"}
    local.write_text(json.dumps(out, ensure_ascii=False))
    return {**out, "cached": False}
