"""Secret hygiene: redact key material from any string before it is logged or returned."""
from __future__ import annotations

import os
import re

_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_URL_PASSWORD = re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_KEYLIKE = re.compile(r"(?i)((?:api[_-]?key|x-goog-api-key|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}")


def _secret_values() -> list[str]:
    vals = []
    for k, v in os.environ.items():
        if v and len(v) >= 6 and any(h in k.upper() for h in _SECRET_ENV_HINTS):
            vals.append(v)
    return sorted(vals, key=len, reverse=True)


def redact(text: object) -> str:
    """Return `text` as str with every known secret value and key-like pattern masked."""
    s = str(text)
    for v in _secret_values():
        if v in s:
            s = s.replace(v, "***")
    s = _URL_PASSWORD.sub(r"\1***\3", s)
    s = _BEARER.sub(r"\1***", s)
    s = _KEYLIKE.sub(r"\1***", s)
    return s


def redact_url(url: str) -> str:
    return _URL_PASSWORD.sub(r"\1***\3", url)
