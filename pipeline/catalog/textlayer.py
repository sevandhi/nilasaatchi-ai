"""Text-layer extraction and quality scoring (T1.1 / phase1-data-foundation skill).

`pdftotext` (poppler-utils) is tried first, per the land-domain-knowledge skill §4. Its
per-page split relies on the form-feed (\\f) poppler inserts between pages.

Quality score: the skill defines it as "(Tamil + Latin + digit characters) / all characters,
combined with a dictionary-hit ratio". We have no full dictionary resource bundled with the
repo, so the dictionary-hit ratio checks Latin tokens against a small common-word list (below)
and Tamil tokens against a length-2+ run heuristic.

**Why this matters (found during T1.6 evaluation):** some scanned documents (seen in "7(2)
Award") carry a legacy non-Unicode Tamil font (e.g. a TSCII/Bamini-style glyph encoding) whose
embedded text layer `pdftotext` happily extracts as syntactically fine-looking ASCII, but which
is actually meaningless Latin-lookalike garbage (example: "SIG`Gg LoITOLIL LLD, soflorroau=L").
A naive "looks word-shaped" check (any run of >=2 letters) scores this garbage almost as high as
real English prose, silently feeding nonsense into the classifier. Requiring a real common-word
match for Latin tokens catches it: text_ok correctly comes out False, and T1.2's OCR fallback
takes over instead.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

TAMIL_RE = re.compile(r"[஀-௿]")
ALNUM_RE = re.compile(r"[A-Za-z0-9]")
TAMIL_WORDISH_RE = re.compile(r"[஀-௿]{2,}")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")
WS_RE = re.compile(r"\s+")

TEXT_OK_MIN_CHARS = 50
TEXT_OK_MIN_SCORE = 0.8

# A small common-word list (English function words + the domain vocabulary these documents
# actually use), enough to separate real government-English prose from font-encoding garbage.
# Not a full dictionary -- see the module docstring.
_COMMON_WORDS = frozenset("""
the of to and a in is was he for it with as his on be at by i this had not are but from or
have an they which one you were her all she there would their we him been has when who will
more no if out so up its about into than them can only other new some could time these two may
then do at been said what into over also into being now under while such where these part
government order act section notice under village district taluk block unit survey extent
acre acres hectare hectares compensation deposit court judge collector officer land acquisition
industries department dated proceedings hereby shall shri smt no dated read read read whereas
therefore government of tamil nadu abstract sanction schedule form rule rules gazette published
authority chennai regd price paise part notifications interest general public heads departments
issued name owner amount rupees total block survey number date possession award notice notified
""".split())  # noqa: SIM905 - a multi-line word list reads far better than one long list literal


def _dict_ratio(text: str) -> float:
    tokens = [t for t in re.split(r"\s+", text) if t]
    if not tokens:
        return 0.0
    hits = 0
    for t in tokens:
        if TAMIL_WORDISH_RE.search(t):
            hits += 1
            continue
        m = LATIN_TOKEN_RE.findall(t)
        if any(w.lower() in _COMMON_WORDS for w in m):
            hits += 1
    return hits / len(tokens)


def extract_pages_text(pdf_path: str | Path, n_pages: int, timeout: int = 60) -> list[str]:
    """Return one text string per page (length == n_pages), using `pdftotext -layout`.
    Returns a list of empty strings (never raises) if pdftotext is missing, times out, or the
    PDF has no extractable text layer."""
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [""] * n_pages
    if r.returncode != 0:
        return [""] * n_pages
    raw = r.stdout.decode("utf-8", errors="replace")
    pages = raw.split("\f")
    # poppler appends a trailing form-feed, giving one extra (usually empty) element.
    if len(pages) > n_pages:
        pages = pages[:n_pages]
    while len(pages) < n_pages:
        pages.append("")
    return pages


def text_quality(text: str) -> tuple[int, float, bool]:
    """Return (non_space_chars, quality_score in [0,1], text_ok)."""
    stripped = WS_RE.sub("", text)
    n = len(stripped)
    if n == 0:
        return 0, 0.0, False
    good = len(TAMIL_RE.findall(text)) + len(ALNUM_RE.findall(text))
    good_ratio = min(1.0, good / n)
    dict_ratio = _dict_ratio(text)
    score = round(0.6 * good_ratio + 0.4 * dict_ratio, 4)
    ok = n >= TEXT_OK_MIN_CHARS and score >= TEXT_OK_MIN_SCORE
    return n, score, ok
