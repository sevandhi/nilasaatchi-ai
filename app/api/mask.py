"""Owner-safety redaction helpers (D-032/D-033): applied to every extraction row_json the API
serves, regardless of the `demo_mask` flag.

- `strip_bank_accounts` removes anything that looks like a bank account number unconditionally —
  bank account numbers are never stored for serving and never served (per the task brief), even
  when `demo_mask=false`.
- `mask_names` redacts owner/payee-shaped string values; only skipped when the caller explicitly
  asked for `demo_mask=false` *and* the server allows it (`ALLOW_DEMO_UNMASK=true`, local only).
"""
from __future__ import annotations

import re
from typing import Any

_ACCOUNT_KEY_RE = re.compile(r"(acc(ou)?nt).*no|bank_?account|ifsc|routing_?no", re.IGNORECASE)
_NAME_KEYS = {"payee", "owner_name", "relation_name", "name", "owner", "canonical_name"}


def strip_bank_accounts(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_bank_accounts(v) for k, v in obj.items() if not _ACCOUNT_KEY_RE.search(str(k))}
    if isinstance(obj, list):
        return [strip_bank_accounts(v) for v in obj]
    return obj


def mask_names(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _NAME_KEYS and isinstance(v, str) and v:
                out[k] = "[REDACTED]"
            else:
                out[k] = mask_names(v)
        return out
    if isinstance(obj, list):
        return [mask_names(v) for v in obj]
    return obj


def safe_row_json(row_json: dict[str, Any], *, masked: bool) -> dict[str, Any]:
    row_json = strip_bank_accounts(row_json)
    if masked:
        row_json = mask_names(row_json)
    return row_json
