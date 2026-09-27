"""The 7 Allikulam villages and their aliases (land-domain-knowledge §7), used by the header
pre-parser (T1.2) and reused by pipeline.raster for parcel_uid canonicalisation.

Not written to config/aliases.yaml: that file is gis-engineer's (T1.4, phase1-data-foundation).
"""
from __future__ import annotations

import re

# canonical -> variants seen or expected (English and Tamil)
VILLAGE_ALIASES: dict[str, list[str]] = {
    "Allikulam": ["Allikulam", "அல்லிகுளம்", "அல்லிக்குளம்"],
    "Keelathattaparai": ["Keelathattaparai", "Keel Thattaparai", "கீழத்தட்டப்பாறை", "கீழதட்டப்பாறை"],
    "Melathattaparai": ["Melathattaparai", "Mela Thattaparai", "Melathataparai", "மேலத்தட்டப்பாறை"],
    "Umarikottai": ["Umarikottai", "உமரிக்கோட்டை"],
    "Peroorani": ["Peroorani", "Perurani", "பேரூரணி"],
    "Ramasamypuram": ["Ramasamypuram", "இராமசாமிபுரம்"],
    "South Silukanpatti": ["South Silukanpatti", "தெற்கு சிலுக்கன்பட்டி"],
}

# longest-alias-first so "Keel Thattaparai" matches before a shorter partial alias would
_ALL_ALIASES = sorted(
    ((alias, canon) for canon, aliases in VILLAGE_ALIASES.items() for alias in aliases),
    key=lambda t: -len(t[0]),
)
VILLAGE_RE = re.compile("|".join(re.escape(a) for a, _ in _ALL_ALIASES))
_ALIAS_TO_CANON = {a: c for a, c in _ALL_ALIASES}


def find_village(text: str) -> str | None:
    """Return the canonical village name if any alias appears in `text`, else None."""
    m = VILLAGE_RE.search(text)
    if not m:
        return None
    return _ALIAS_TO_CANON[m.group(0)]
