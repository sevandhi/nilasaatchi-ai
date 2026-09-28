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
from app.common.explain import PROMPT_VERSION, SCHEMA, build_prompt, template

CACHE = Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")) / "summaries"

def plain_summary(pack: dict, lang: str = "en") -> dict:
    fid, rv = pack.get("finding_id"), pack.get("rule_version") or "v"
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{fid}_{rv}_{PROMPT_VERSION}_{lang}.json"
    if path.exists():
        return {**json.loads(path.read_text()), "cached": True}
    from app.router import call
    prompt = build_prompt(pack, lang)
    try:
        res = call("present", {"prompt": prompt, "max_tokens": 1400, "temperature": 0.2},
                   schema=SCHEMA, privacy_tier="PSEUDO")
        out = ({**{k: res.data.get(k) for k in SCHEMA["required"]}, "source": res.model_id}
               if res.ok and res.data else template(pack))
    except Exception:  # noqa: BLE001 - the explanation is a convenience; never fail the evidence pack
        out = template(pack)
    if out.get("source") != "template":
        path.write_text(json.dumps(out, ensure_ascii=False))
    return {**out, "cached": False}
