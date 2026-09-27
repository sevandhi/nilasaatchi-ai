"""Plain-language explanation of one finding for non-experts ("In simple words" on the evidence pack).

Built only from the finding's own facts (category, severity, confidence, metrics, dates, caveats,
evidence level); owner names are never sent. Routed through app.router (task `present`, PSEUDO tier),
cached per finding + rule version + language under data/summaries/, and falls back to a plain template
when no model answers, so the box is never empty.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.api.config import REPO_ROOT

CACHE = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")) / "summaries"

WHAT = {
    "COMPENSATION_MISMATCH": "the money paid for this land does not match the land's size times the official rate on the same page",
    "EXTENT_MISMATCH": "the land size written in the documents is different from the size measured on the survey map",
    "DOC_VERSION_CONFLICT": "two official documents give different total areas for the same village",
    "FMB_QUALITY": "the survey map itself has a problem: two land parcels are drawn on top of each other",
    "PV1_CLASSIFICATION_CONFLICT": "the papers call this dry land, but the satellite saw it watered and cropped several times a year",
    "PV3_POST_POSSESSION_ACTIVITY": "after the government took possession, the satellite saw this land behave differently from nearby farms that were never acquired",
    "PV4_IDLE_LAND_BANK": "land taken over months ago shows no clearing or construction on the satellite: it looks unused",
    "EXTRACTION_ERROR": "a number read from the scanned document looks impossible, so it was probably misread",
}

PROMPT_VERSION = "v2"   # part of the cache key: bump when the prompt or schema changes

SCHEMA = {
    "type": "object",
    "required": ["headline", "what_was_found", "numbers_explained", "why_flagged", "other_explanations",
                 "how_sure", "what_next", "bottom_line"],
    "properties": {
        "headline": {"type": "string", "description": "one plain sentence (max 20 words): the main point"},
        "what_was_found": {"type": "string", "description": "3-4 short sentences: what the documents and/or satellite show for this land, with the key facts (place, dates, sizes, money) in everyday words"},
        "numbers_explained": {"type": "array", "maxItems": 5, "description": "the 2-5 most important numbers, each explained so a farmer understands it",
                              "items": {"type": "object", "required": ["number", "meaning"],
                                        "properties": {"number": {"type": "string", "description": "the number as shown, rounded, with its unit"},
                                                       "meaning": {"type": "string", "description": "1-2 sentences in plain words: what it measures and what this value tells us"}}}},
        "why_flagged": {"type": "string", "description": "2-3 sentences: which automatic check raised this and why the result looked unusual"},
        "other_explanations": {"type": "array", "maxItems": 4, "items": {"type": "string"},
                               "description": "2-4 innocent or technical reasons that could also explain this (e.g. rounding, a misread number, weeds after rain, map drawing errors)"},
        "how_sure": {"type": "string", "description": "2-3 sentences: how strong the evidence is (weak / medium / strong) and the main reasons for caution"},
        "what_next": {"type": "array", "maxItems": 4, "items": {"type": "string"},
                      "description": "2-4 concrete next steps a person can take to confirm or rule it out"},
        "bottom_line": {"type": "string", "description": "one or two sentences a busy officer can act on"},
    },
}

PROMPT = """You explain land-acquisition findings to ordinary people (farmers, clerks, officers) who know nothing
about satellites or statistics. Be informative and clear: explain every number you mention in simple words,
say where the information comes from (the scanned government document, the survey map, or the satellite),
and give concrete examples. Use short sentences and everyday words. Do NOT use jargon such as DiD,
difference-in-differences, confidence interval, NDVI, z-score, pixel, rabi, kharif or FMB (say "survey map").
If you must mention a statistical idea, explain it in words, e.g. "compared with nearby farms that were never
acquired". Round numbers (e.g. "about 2 hectares", "about ₹10 lakh"). Describe the size of any change carefully
("a little", "clearly") and never exaggerate. Say clearly that this is a signal to check, not a proof. Use only
facts present in the data; do not invent names, dates or amounts. Answer in {language}.

What this type of finding means: {what}
How the check works: {how}
Data (JSON): {facts}"""

HOW = {
    "COMPENSATION_MISMATCH": "the land size in acres, the rate per acre and the amount paid are all read from the same scanned award page (not from the survey map); acres x rate is compared with the amount paid, and a difference beyond rounding is flagged",
    "EXTENT_MISMATCH": "the land size written in the documents is compared with the area of the parcel drawn on the survey map; a difference of more than 5% is flagged",
    "DOC_VERSION_CONFLICT": "the total area for a village in the proposal is compared with the total in the government sanction and other documents",
    "FMB_QUALITY": "the parcels on the survey map are checked for overlapping or misplaced shapes",
    "PV1_CLASSIFICATION_CONFLICT": "the land class in the papers (dry / wet) is compared with how often the satellite saw the land green and watered in the years before acquisition",
    "PV3_POST_POSSESSION_ACTIVITY": "the greenness of this land before and after possession is compared with nearby farms (2-6 km away) that were never acquired, so that rain and season effects cancel out",
    "PV4_IDLE_LAND_BANK": "land possessed at least 6 months ago is checked on the satellite for signs of clearing or construction",
    "EXTRACTION_ERROR": "numbers read from the scanned page are checked for impossible values, such as an amount 10 times larger than size x rate",
}

def _facts(pack: dict) -> dict:
    keep = ("category", "severity", "confidence", "title", "parcel_uid", "village", "evidence_level", "metrics", "caveats")
    f = {k: pack.get(k) for k in keep if pack.get(k) not in (None, "", [], {})}
    planet = pack.get("planet") or {}
    for k in ("did_vigour_z", "did_plough_z"):
        if planet.get(k):
            f[k] = {x: planet[k].get(x) for x in ("did", "ci_lo", "ci_hi", "event_date", "n_post")}
    return json.loads(json.dumps(f, default=str))


def _template(pack: dict) -> dict:
    cat = pack.get("category")
    sev = pack.get("severity") or "low"
    conf = pack.get("confidence")
    where = pack.get("parcel_uid") or pack.get("village") or "this area"
    return {
        "headline": f"{where}: {WHAT.get(cat, pack.get('title') or 'a possible problem was found')}.",
        "what_was_found": pack.get("title") or "",
        "numbers_explained": [],
        "why_flagged": HOW.get(cat, "An automatic check found a value outside the expected range.").capitalize() + ".",
        "other_explanations": ["A number may have been misread from the scanned page.", "The survey map drawing may be inaccurate."],
        "how_sure": (f"This is a {sev}-priority signal" + (f" with about {round(conf * 100)}% strength" if isinstance(conf, (int, float)) else "")
                     + ". It comes from automatic checks, so a person must confirm it."),
        "what_next": ["Open the scanned document and compare the numbers.", "For satellite signals, visit the land."],
        "bottom_line": "Treat this as a lead to check, not a conclusion.",
        "source": "template",
    }


def plain_summary(pack: dict, lang: str = "en") -> dict:
    fid, rv = pack.get("finding_id"), pack.get("rule_version") or "v"
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{fid}_{rv}_{PROMPT_VERSION}_{lang}.json"
    if path.exists():
        return {**json.loads(path.read_text()), "cached": True}
    from app.router import call
    prompt = PROMPT.format(language="Tamil" if lang == "ta" else "simple English",
                           what=WHAT.get(pack.get("category"), pack.get("title") or ""),
                           how=HOW.get(pack.get("category"), ""),
                           facts=json.dumps(_facts(pack), ensure_ascii=False))
    try:
        res = call("present", {"prompt": prompt, "max_tokens": 1400, "temperature": 0.2},
                   schema=SCHEMA, privacy_tier="PSEUDO")
        out = ({**{k: res.data.get(k) for k in SCHEMA["required"]}, "source": res.model_id}
               if res.ok and res.data else _template(pack))
    except Exception:  # noqa: BLE001 - the explanation is a convenience; never fail the evidence pack
        out = _template(pack)
    if out.get("source") != "template":
        path.write_text(json.dumps(out, ensure_ascii=False))
    return {**out, "cached": False}
