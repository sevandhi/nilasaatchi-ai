"""Build provider-neutral (OpenAI-style) chat messages from a router payload.

Payload control keys (everything else is passed to the model as JSON context):
  prompt: str            user instruction
  system: str            system prompt
  messages: list[dict]   full chat history (overrides prompt/system)
  max_tokens, temperature
  hard_replan: bool      enables `when: hard_replan` chain entries
  step_id, run_id        telemetry correlation
"""
from __future__ import annotations

import base64
import json

CONTROL_KEYS = {"prompt", "system", "messages", "max_tokens", "temperature", "hard_replan",
                "step_id", "run_id"}


def sniff_mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"%PDF":
        return "application/pdf"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return "application/octet-stream"


def input_modalities(images: list[bytes] | None) -> set[str]:
    mods = set()
    for b in images or []:
        mods.add("pdf" if sniff_mime(b) == "application/pdf" else "image")
    return mods


def data_url(b: bytes) -> str:
    return f"data:{sniff_mime(b)};base64,{base64.b64encode(b).decode()}"


def schema_instruction(schema: dict) -> str:
    return ("Respond with a single JSON object only (no prose, no code fences) that validates "
            "against this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False, sort_keys=True))


def build_messages(payload: dict, schema: dict | None, images: list[bytes] | None) -> list[dict]:
    if payload.get("messages"):
        msgs = [dict(m) for m in payload["messages"]]
    else:
        msgs = []
        if payload.get("system"):
            msgs.append({"role": "system", "content": str(payload["system"])})
        parts = []
        if payload.get("prompt"):
            parts.append(str(payload["prompt"]))
        ctx = {k: v for k, v in payload.items() if k not in CONTROL_KEYS}
        if ctx:
            parts.append("Input:\n" + json.dumps(ctx, ensure_ascii=False, sort_keys=True, default=str))
        msgs.append({"role": "user", "content": "\n\n".join(parts) or "(no input)"})
    if schema is not None:
        instr = schema_instruction(schema)
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = {**msgs[0], "content": f"{msgs[0]['content']}\n\n{instr}"}
        else:
            msgs.insert(0, {"role": "system", "content": instr})
    if images:
        msgs = attach_images(msgs, images)
    return msgs


def attach_images(msgs: list[dict], images: list[bytes]) -> list[dict]:
    """Vision format: the last user message becomes content parts (text + image_url / file)."""
    idx = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
    m = msgs[idx]
    content = m["content"]
    parts = list(content) if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    for b in images:
        url = data_url(b)
        if sniff_mime(b) == "application/pdf":
            parts.append({"type": "file", "file": {"file_data": url}})
        else:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    out = list(msgs)
    out[idx] = {**m, "content": parts}
    return out


def text_of(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.extend(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return "\n".join(out)


def estimate_tokens(msgs: list[dict], images: list[bytes] | None) -> int:
    return int(len(text_of(msgs)) / 3.5) + 1100 * len(images or []) + 8
