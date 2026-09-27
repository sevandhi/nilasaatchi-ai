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

SCHEMA = {
    "type": "object",
    "required": ["summary", "what_it_means", "how_sure", "what_next"],
    "properties": {
        "summary": {"type": "string", "description": "1-2 short sentences: what was found, in everyday words"},
        "what_it_means": {"type": "string", "description": "1-2 sentences: why it matters to a landowner or officer"},
        "how_sure": {"type": "string", "description": "1-2 sentences: how strong the evidence is and the main reason for caution"},
        "what_next": {"type": "string", "description": "1 sentence: the simple next check a person should do"},
    },
}

PROMPT = """Explain this land-acquisition finding to an ordinary person (a farmer or a clerk) who knows nothing
about satellites or statistics. Use short, simple sentences and everyday words. Do NOT use jargon such as
DiD, confidence interval, NDVI, z-score, pixel, rabi or kharif. Round numbers (e.g. "about 2 hectares",
"about ₹10 lakh"). Describe the size of any change carefully ("a little", "clearly") and never exaggerate.
Say clearly that this is a signal to check, not a proof. Do not invent facts that are not
in the data. Answer in {language}.

Finding type in plain words: {what}
Data (JSON): {facts}"""


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
        "summary": f"For {where}: {WHAT.get(cat, pack.get('title') or 'a possible problem was found')}.",
        "what_it_means": "If this is right, the records for this land may need to be corrected or looked into.",
        "how_sure": (f"This is a {sev}-priority signal" + (f" with about {round(conf * 100)}% strength" if isinstance(conf, (int, float)) else "")
                     + ". It comes from automatic checks, so it must be confirmed by a person."),
        "what_next": "Open the scanned document and, for satellite signals, visit the land to confirm.",
        "source": "template",
    }


def plain_summary(pack: dict, lang: str = "en") -> dict:
    fid, rv = pack.get("finding_id"), pack.get("rule_version") or "v"
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{fid}_{rv}_{lang}.json"
    if path.exists():
        return {**json.loads(path.read_text()), "cached": True}
    from app.router import call
    prompt = PROMPT.format(language="Tamil" if lang == "ta" else "simple English",
                           what=WHAT.get(pack.get("category"), pack.get("title") or ""),
                           facts=json.dumps(_facts(pack), ensure_ascii=False))
    try:
        res = call("present", {"prompt": prompt, "max_tokens": 400, "temperature": 0.2},
                   schema=SCHEMA, privacy_tier="PSEUDO")
        out = ({**{k: str(res.data[k]).strip() for k in SCHEMA["required"]}, "source": res.model_id}
               if res.ok and res.data else _template(pack))
    except Exception:  # noqa: BLE001 - the explanation is a convenience; never fail the evidence pack
        out = _template(pack)
    if out.get("source") != "template":
        path.write_text(json.dumps(out, ensure_ascii=False))
    return {**out, "cached": False}
