"""Deterministic, domain-neutral slot helpers (years, seasons, numbers, language, attachment kinds)."""
from __future__ import annotations

import re
from pathlib import Path

from app.agent.state import Attachment

TAMIL = re.compile(r"[஀-௿]")
SEASON_WORDS = {"kharif": "kharif", "rabi": "rabi", "summer": "summer", "ராபி": "rabi", "காரிஃப்": "kharif",
                "கோடை": "summer", "samba": "rabi", "kuruvai": "kharif"}


def detect_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    ta = sum(1 for c in letters if TAMIL.match(c))
    return "ta" if letters and ta / len(letters) > 0.3 else "en"


def years(text: str) -> list[int]:
    out = set()
    for m in re.finditer(r"\b(20[12]\d)\s*[-–/]\s*(\d{2,4})\b", text):
        a, b = int(m.group(1)), int(m.group(2))
        b = b + 2000 if b < 100 else b
        out.update(range(a, min(b, 2026) + 1))
    out.update(int(y) for y in re.findall(r"\b(20[12]\d)\b", text))
    return sorted(y for y in out if 2015 <= y <= 2026)


def seasons(text: str) -> list[str]:
    low = text.lower()
    return sorted({v for k, v in SEASON_WORDS.items() if k in low})


def thresholds(text: str) -> dict[str, float]:
    t: dict[str, float] = {}
    if m := re.search(r"(\d+(?:\.\d+)?)\s*km\b", text, re.IGNORECASE):
        t["distance_m"] = float(m.group(1)) * 1000
    elif m := re.search(r"(\d+(?:\.\d+)?)\s*(?:m|metres|meters)\b", text, re.IGNORECASE):
        t["distance_m"] = float(m.group(1))
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%", text):
        t["percent"] = float(m.group(1))
    if m := re.search(r"(\d+)\s*(?:days|நாட்கள்)", text, re.IGNORECASE):
        t["days"] = float(m.group(1))
    if m := re.search(r"(?:≥|>=|at least|minimum of)\s*(\d+)\s*seasons?", text, re.IGNORECASE):
        t["min_seasons"] = float(m.group(1))
    return t


def attachment_kind(a: Attachment) -> str:
    if a.kind:
        return a.kind
    mt = (a.media_type or "").lower()
    ext = Path(a.name or a.path or "").suffix.lower()
    if "geo+json" in mt or ext in (".geojson",):
        return "geojson"
    if ext == ".json" and a.path and Path(a.path).exists():
        head = Path(a.path).read_text(encoding="utf-8", errors="ignore")[:400]
        if '"FeatureCollection"' in head or '"Feature"' in head:
            return "geojson"
    if mt == "application/pdf" or ext == ".pdf":
        return "pdf"
    if mt.startswith("image/") or ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        return "image"
    if "csv" in mt or ext == ".csv":
        return "csv"
    return "text"


# Scope slots the planner may copy into OPTIONAL tool args the model left empty (some models emit args={}).
SCOPE_SLOTS = ("villages", "blocks", "parcel_uids")
# optional filter args filled from same-named slots when the planner leaves them empty (domain packs
# put these slots under Slots.extra); a value the planner wrote is never overridden
FILTER_SLOTS = ("categories", "severities", "signal", "metric_max")
