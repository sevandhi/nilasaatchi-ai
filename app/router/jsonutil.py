"""JSON extraction from model text and JSON-Schema validation."""
from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str | None) -> Any:
    """Best-effort: strip <think> blocks and code fences, then parse the first JSON object/array.
    Raw control characters inside strings (e.g. Ministral's multi-line SQL values) are accepted (strict=False)."""
    if text is None:
        raise ValueError("empty response")
    s = _THINK.sub("", text).strip()
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder(strict=False)
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(s[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object found in response")


def validate(obj: Any, schema: dict) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    v = jsonschema.Draft202012Validator(schema)
    errs = sorted(v.iter_errors(obj), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs][:10]


def parse_and_validate(text: str | None, schema: dict) -> tuple[dict | None, list[str]]:
    try:
        obj = extract_json(text)
    except ValueError as e:
        return None, [str(e)]
    if not isinstance(obj, dict):
        return None, ["top-level JSON must be an object"]
    errs = validate(obj, schema)
    return (obj if not errs else None), errs


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
