"""Redaction helper (defense in depth on top of the router's own PII-tier routing,
land-domain-knowledge §13): owner names, patta numbers and rupee amounts are masked before a
document's text leaves the process, even though Bedrock (D-027) does not train on customer
data. Used by `.llm_classifier` for the D-030 LLM classify calls; superseded that module's own
older `llm_classify()` decision step (kept only as this shared redaction utility now)."""
from __future__ import annotations

import re

_OWNER_NAME_RE = re.compile(r"(?:த/பெ|க/பெ|மகன்|மனைவி)[^\n,;]{0,40}")
_PATTA_RE = re.compile(r"பட்டா\s*எண்\s*[:\-]?\s*\d+")
_RUPEE_RE = re.compile(r"(?:₹|Rs\.?)\s*[\d,]+(?:/-)?")


def redact(text: str) -> str:
    text = _OWNER_NAME_RE.sub("[REDACTED_NAME]", text)
    text = _PATTA_RE.sub("[REDACTED_PATTA]", text)
    text = _RUPEE_RE.sub("[REDACTED_AMOUNT]", text)
    return text
