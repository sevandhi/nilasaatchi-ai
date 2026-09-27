"""Provider model-list endpoints (used by `make doctor` to suggest replacements for 404 IDs)."""
from __future__ import annotations

import difflib
import os

import httpx

from ..secrets import redact

TIMEOUT = 15.0


def list_models(provider: str) -> tuple[list[str], str | None]:
    """Return (model ids, error). Keys travel only in headers; errors are redacted."""
    try:
        if provider == "gemini":
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                return [], "missing key"
            ids, token = [], None
            for _ in range(10):
                params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
                r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                              headers={"x-goog-api-key": key}, params=params, timeout=TIMEOUT)
                r.raise_for_status()
                j = r.json()
                for m in j.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        ids.append(m["name"].removeprefix("models/"))
                token = j.get("nextPageToken")
                if not token:
                    break
            return sorted(set(ids)), None
        if provider in ("groq", "mistral", "mistral_ocr"):
            env = "GROQ_API_KEY" if provider == "groq" else "MISTRAL_API_KEY"
            url = ("https://api.groq.com/openai/v1/models" if provider == "groq"
                   else "https://api.mistral.ai/v1/models")
            key = os.environ.get(env)
            if not key:
                return [], "missing key"
            r = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
            r.raise_for_status()
            return sorted({m["id"] for m in r.json().get("data", [])}), None
        if provider == "cohere":
            key = os.environ.get("COHERE_API_KEY")
            if not key:
                return [], "missing key"
            r = httpx.get("https://api.cohere.com/v1/models", params={"page_size": 1000, "endpoint": "chat"},
                          headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
            r.raise_for_status()
            return sorted({m["name"] for m in r.json().get("models", [])}), None
        return [], f"listing not supported for {provider}"
    except Exception as e:  # noqa: BLE001
        return [], redact(f"{type(e).__name__}: {e}")[:300]


def closest(wanted: str, available: list[str], n: int = 5) -> list[str]:
    """Closest IDs by family token overlap first, then string similarity."""
    w = wanted.lower()
    fam_tokens = [t for t in w.replace("/", "-").replace(":", "-").split("-") if t and not t[0].isdigit()]
    scored = []
    for a in available:
        al = a.lower()
        overlap = sum(1 for t in fam_tokens if t in al)
        ratio = difflib.SequenceMatcher(None, w, al).ratio()
        scored.append((overlap, ratio, a))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [a for o, r, a in scored[:n] if o > 0 or r > 0.5]
