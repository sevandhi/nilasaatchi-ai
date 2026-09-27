"""Teacher labels for parcel-seasons (T3.4): Gemini (router task ``satellite_teacher``) + Cohere Vision
(router task ``satellite_second_opinion``) + the rule/WorldCover prior; final label = majority vote.

PUBLIC tier only. The teachers see one panel PNG (chips + NDVI plot, see ``panel.py``) and the prompt in
``prompts/satellite_teacher.md``. No parcel id, village, survey number, owner or document data is sent.

Pipeline (idempotent; every step skips work already stored):
1. ``sample``: ~300 stratified items (village x prior state x amplitude tercile, sqrt-proportional allocation,
   >= 1 per stratum) + the demo parcels; 50 non-demo items are the blind qa audit set (label_set = 'audit',
   never used to train or tune the student). Saved to ``data/s2/labels/items.csv``.
2. ``label``: panel per item, then one call per teacher; rows upserted into ``planet_teacher_label``.
   Free-tier pacing: >= 4.2 s between Gemini calls (15 RPM), >= 3.2 s between Cohere calls (20 RPM); a
   quota refusal stops that teacher (re-run later to resume). Bedrock is disabled for the batch
   (ROUTER_CHAOS=bedrock:down) so the capped AWS account is never touched.
3. ``consensus``: majority of the independent votes -> ``parcel_season.teacher_label`` with
   ``teacher_agreement`` (3of3 | 2of3 | 2of2 | split | none). A teacher slot answered by a fallback model
   of another slot's vendor is not an independent vote and is dropped.

``python -m planet.classify.teacher [sample|label|regrade|consensus|all] [--limit N] [--teachers gemini,cohere4] [--pilot N]``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import threading
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from planet.aoi import REPO
from planet.classify import rules
from planet.extract import parcel_stats as ps

log = logging.getLogger("planet.classify.teacher")

LABEL_DIR = ps.CACHE_DIR / "labels"
ITEMS_CSV = LABEL_DIR / "items.csv"
PROMPT_PATH = REPO / "prompts" / "satellite_teacher.md"
DEMO_ITEMS = [(f"Melathattaparai|{k}", y, s) for k in ("233", "235")
              for y, s in ((2024, "rabi"), (2024, "summer"), (2025, "kharif"), (2025, "rabi"), (2025, "summer"))]
N_ITEMS, N_AUDIT, SEED = 300, 50, 20260926
TEACHERS = {  # slot -> (router task, expected vendor, min seconds between calls, pinned router model id)
    "gemini": ("satellite_teacher", "google", 4.2, "gemini-flash-lite-31"),
    "cohere": ("satellite_second_opinion", "cohere", 3.2, "cohere-command-a-vision"),
    "cohere4": ("satellite_second_opinion", "cohere", 3.2, "cohere-command-a-vision"),
}
# slot -> (prompt file, panel style). ``cohere4`` = Cohere with prompt v4 + panel-v2 (clear season band, neutral
# A/B/C chip markers): Cohere v3 read the curve correctly for only 40 % of items (summer 5 %), mostly calling a
# falling curve "peak_inside". Old slots' rows stay in the table as the audit trail.
SLOT_CFG = {
    "gemini": (REPO / "prompts" / "satellite_teacher.md", "v1"),
    "cohere": (REPO / "prompts" / "satellite_teacher.md", "v1"),
    "cohere4": (REPO / "prompts" / "satellite_teacher_cohere.md", "v2"),
}
# slot -> teacher value stored in planet_teacher_label (its CHECK allows gemini/cohere/prior/...): ``cohere4`` rows
# are stored as teacher 'cohere' and told apart by prompt_version; the v3 rows they replace are archived first in
# data/s2/labels/cohere_v3_archive.parquet (see ``archive_slot``).
DB_TEACHER = {"gemini": "gemini", "cohere": "cohere", "cohere4": "cohere"}
ARCHIVE = {"cohere4": ("cohere", "satellite_teacher-v3", "cohere_v3_archive.parquet")}
# consensus vote -> slot that supplies it (switch the Cohere vote to ``cohere4`` once its labels are complete)
VOTE_SLOTS = {"gemini": "gemini", "cohere": "cohere"}
# Each slot is pinned with ``only_model`` (same router path: privacy tier, quota, breaker, telemetry) so the
# two teachers are always independent vendors. D-036: gemini-flash-lite-31 (3.1, 120 s image timeout).
USER_MSG = "Label the TARGET season shown in the panel. Return only the JSON object."
CURVES = ["peak_inside", "rising", "falling", "flat", "gappy"]
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "read_ndvi_max": {"type": "number"},
        "read_chip_dates": {"type": "array", "items": {"type": "string"}},
        "curve_in_band": {"type": "string", "enum": CURVES},
        "read_curve_start": {"type": "number"},  # v4c only (optional)
        "read_curve_end": {"type": "number"},
        "state": {"type": "string", "enum": [*rules.STATES, "unclear"]},
        "confidence": {"type": "number"},
        "visual_evidence": {"type": "string"},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["read_ndvi_max", "read_chip_dates", "curve_in_band", "state", "confidence", "visual_evidence",
                 "caveats"],
}
READ_TOL = 0.02  # header prints NDVI to 2 decimals
CURVE_FLAT = 0.1  # NDVI change below which a curve counts as flat (prompt: "less than about 0.1")


def slot_prompt(slot: str) -> Path:
    return SLOT_CFG.get(slot, (PROMPT_PATH, "v1"))[0]


def prompt_version(text: str | None = None, slot: str | None = None) -> str:
    text = text if text is not None else (slot_prompt(slot) if slot else PROMPT_PATH).read_text()
    first = text.splitlines()[0] if text else ""
    if "prompt_version:" in first:
        return first.split("prompt_version:")[1].split()[0]
    return "unversioned"


def prompt_text(text: str | None = None) -> str:
    """The system prompt as sent to the model: the file minus its leading HTML comment (version header)."""
    text = text if text is not None else PROMPT_PATH.read_text()
    return re.sub(r"\A\s*<!--.*?-->\s*", "", text, flags=re.DOTALL)


def item_id(uid: str, ag_year: int, season: str) -> str:
    return f"{uid}|{int(ag_year)}|{season}"


# ----------------------------------------------------------------------------------------------- sample
def candidates(feats: pd.DataFrame, wc: pd.DataFrame | None) -> pd.DataFrame:
    df = rules.apply(feats, wc)
    df = df[df.data_sufficient & ~df.partial_window & (df.ag_year >= 2019) & (df.prior_state != "insufficient_data")].copy()
    df["village"] = df.parcel_uid.str.split("|").str[0]
    df["amp_q"] = df.groupby("season").amplitude.transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    df["item_id"] = [item_id(u, y, s) for u, y, s in zip(df.parcel_uid, df.ag_year, df.season, strict=True)]
    return df


def stratified_sample(cand: pd.DataFrame, n: int = N_ITEMS, seed: int = SEED,
                      demo: Sequence[tuple[str, int, str]] = DEMO_ITEMS) -> pd.DataFrame:
    """sqrt-proportional allocation over (village, prior_state, amp_q) strata, >= 1 per stratum."""
    rng = np.random.default_rng(seed)
    demo_ids = {item_id(*d) for d in demo}
    picked = cand[cand.item_id.isin(demo_ids)]
    pool = cand[~cand.item_id.isin(demo_ids)]
    groups = pool.groupby(["village", "prior_state", "amp_q"])
    sizes = groups.size()
    budget = n - len(picked)
    w = np.sqrt(sizes.astype(float))
    alloc = np.maximum(1, np.floor(w / w.sum() * budget)).astype(int)
    alloc = np.minimum(alloc, sizes)
    # hand out the remainder to the largest strata by fractional weight, deterministically
    rest = budget - int(alloc.sum())
    order = (w / w.sum() * budget - np.floor(w / w.sum() * budget)).sort_values(ascending=False).index
    for k in order:
        if rest <= 0:
            break
        if alloc[k] < sizes[k]:
            alloc[k] += 1
            rest -= 1
    parts = [picked]
    for k, g in groups:
        m = int(alloc.get(k, 0))
        if m > 0:
            # at most one season per parcel from one stratum keeps the sample diverse
            g = g.sample(frac=1.0, random_state=int(rng.integers(1 << 31)))
            g = g.drop_duplicates("parcel_uid")
            parts.append(g.head(m))
    out = pd.concat(parts).drop_duplicates("item_id")
    out["is_demo"] = out.item_id.isin(demo_ids)
    # audit: stratified by prior state over non-demo items
    non_demo = out[~out.is_demo]
    frac = N_AUDIT / max(len(non_demo), 1)
    aud = (non_demo.groupby("prior_state", group_keys=False)
           .apply(lambda g: g.sample(n=max(1, round(len(g) * frac)), random_state=seed)))
    aud = aud.head(N_AUDIT) if len(aud) >= N_AUDIT else pd.concat(
        [aud, non_demo.drop(aud.index).sample(n=N_AUDIT - len(aud), random_state=seed)])
    out["label_set"] = np.where(out.item_id.isin(aud.item_id), "audit", "train")
    return out.sort_values(["label_set", "village", "item_id"]).reset_index(drop=True)


def pilot_items(items: pd.DataFrame, n: int, seed: int = SEED) -> pd.DataFrame:
    """N train items (the audit set is never used to tune a prompt), seeded, round-robin over seasons."""
    tr = items[items.label_set == "train"].sample(frac=1.0, random_state=seed)
    tr = tr.assign(_r=tr.groupby("season").cumcount()).sort_values(["_r", "season"])
    return tr.head(n).drop(columns="_r")


ITEM_COLS = ["item_id", "parcel_uid", "village", "ag_year", "season", "label_set", "is_demo", "prior_state",
             "prior_src", "prior_reason", "amp_q", "amplitude", "ndvi_max", "n_obs", "max_gap_days", "mixed_pixel"]


def load_items() -> pd.DataFrame:
    return pd.read_csv(ITEMS_CSV)


# ------------------------------------------------------------------------------------------------ DB io
def dsn() -> str:
    from planet.extract.run import dsn as _dsn

    return _dsn()


def done_items(conn: Any, teacher: str) -> set[str]:
    """Items with an ok, independent answer for this slot under the current prompt version (answers from a
    fallback vendor or an older prompt are redone; the upsert replaces them, the old text stays in git)."""
    pv = prompt_version(slot=teacher)
    return {r[0] for r in conn.execute("SELECT item_id FROM planet_teacher_label WHERE teacher = %s AND ok "
                                       "AND coalesce(provenance->>'independent', 'true') = 'true' "
                                       "AND prompt_version = %s", (DB_TEACHER.get(teacher, teacher), pv)).fetchall()}


def archive_slot(conn: Any, slot: str) -> int:
    """Before a slot overwrites another prompt version's rows (same DB teacher), copy them to a parquet once."""
    if slot not in ARCHIVE:
        return 0
    teacher, pv, fname = ARCHIVE[slot]
    out = LABEL_DIR / fname
    cur = conn.execute("SELECT * FROM planet_teacher_label WHERE teacher = %s AND prompt_version = %s", (teacher, pv))
    cols = [d.name for d in cur.description]
    new = pd.DataFrame(cur.fetchall(), columns=cols)
    if new.empty:
        return 0
    new["provenance"] = new.provenance.map(json.dumps)
    new["caveats"] = new.caveats.map(list)
    old = pd.read_parquet(out) if out.exists() else new.iloc[0:0]
    allr = pd.concat([old, new[~new.id.isin(old.id)]], ignore_index=True)
    allr.to_parquet(out, index=False)
    return len(allr)


def upsert_label(conn: Any, row: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    cols = ["item_id", "parcel_uid", "ag_year", "season", "teacher", "task", "model_id", "resolved_model",
            "prompt_version", "state", "confidence", "rationale", "caveats", "panel_sha256", "panel_path",
            "provenance", "ok", "error"]
    vals = [Jsonb(row.get(c) or {}) if c == "provenance" else row.get(c) for c in cols]
    vals[cols.index("caveats")] = list(row.get("caveats") or [])
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("item_id", "teacher"))
    conn.execute(f"INSERT INTO planet_teacher_label ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                 f"ON CONFLICT (item_id, teacher) DO UPDATE SET {upd}, created_at = now()", vals)
    conn.commit()


def write_priors(conn: Any, items: pd.DataFrame) -> int:
    for r in items.to_dict("records"):
        upsert_label(conn, {"item_id": r["item_id"], "parcel_uid": r["parcel_uid"], "ag_year": int(r["ag_year"]),
                            "season": r["season"], "teacher": "prior", "model_id": r["prior_src"],
                            "prompt_version": rules.RULES_VERSION, "state": r["prior_state"], "confidence": None,
                            "rationale": r["prior_reason"], "caveats": ["weak prior; crop vs weed flush not separable"],
                            "provenance": {"rules": rules.T, "worldcover_seasons": sorted(map(list, rules.WORLDCOVER_SEASONS))},
                            "ok": True})
    return len(items)


# ---------------------------------------------------------------------------------------------- labelling
def parse_answer(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise TypeError(f"not a JSON object: {data!r}")
    st = str(data.get("state", "")).strip().lower()
    if st not in (*rules.STATES, "unclear"):
        raise ValueError(f"unknown state {st!r}")
    conf = float(data.get("confidence", 0.0))
    conf = conf / 100.0 if conf > 1.0 else conf
    cav = data.get("caveats") or []
    cav = [str(c) for c in (cav if isinstance(cav, list) else [cav])][:8]
    reads = {"read_ndvi_max": data.get("read_ndvi_max"), "read_chip_dates": data.get("read_chip_dates"),
             "curve_in_band": data.get("curve_in_band")}
    for k in ("read_curve_start", "read_curve_end"):
        if k in data:
            reads[k] = data.get(k)
    return {"state": None if st == "unclear" else st, "unclear": st == "unclear",
            "confidence": float(np.clip(conf, 0.0, 1.0)),
            "rationale": str(data.get("visual_evidence", ""))[:2000], "caveats": cav, "reads": reads}


def measured_curve(sm: pd.DataFrame, a: pd.Timestamp, b: pd.Timestamp) -> str:
    """Shape of the smoothed NDVI inside the season band, from the data (same vocabulary as ``curve_in_band``)."""
    w = sm[(sm.date >= a) & (sm.date <= b)].sort_values("date")
    if w.empty or float(w.supported.mean()) < 0.5:
        return "gappy"
    v = w.ndvi_s.to_numpy(float)
    i = int(np.argmax(v))
    if float(v.max() - v.min()) < CURVE_FLAT:
        return "flat"
    rise, fall = float(v[i] - v[: i + 1].min()), float(v[i] - v[i:].min())
    if rise >= CURVE_FLAT and fall >= CURVE_FLAT:
        return "peak_inside"
    return "rising" if rise >= fall else "falling"


def consistent(state: str | None, reads: dict[str, Any]) -> bool:
    """The prompt's own rules: a crop state needs NDVI max >= 0.3 and a peak inside the band."""
    if state not in ("cropped", "irrigated_multi"):
        return True
    try:
        mx = float(reads.get("read_ndvi_max"))
    except (TypeError, ValueError):
        return False
    return mx >= 0.3 and reads.get("curve_in_band") == "peak_inside"


def grounding(reads: dict[str, Any], expected: dict[str, Any], state: str | None = None) -> dict[str, Any]:
    """Check the teacher's read-back against the panel (header NDVI max, chip dates) and the curve shape measured
    from the data; plus self-consistency of the state with its own reads. A vote counts only if all hold."""
    ok_max = None
    try:
        ok_max = abs(float(reads.get("read_ndvi_max")) - float(expected["ndvi_max"])) <= READ_TOL
    except (TypeError, ValueError, KeyError):
        ok_max = False
    got = [str(d)[:10] for d in (reads.get("read_chip_dates") or [])]
    ok_dates = got == list(expected.get("chip_dates", []))
    curve_ok = reads.get("curve_in_band") == expected["curve"] if expected.get("curve") else None
    return {"ndvi_max_ok": bool(ok_max), "dates_ok": bool(ok_dates), "grounded": bool(ok_max and ok_dates),
            "curve_ok": curve_ok, "consistent": consistent(state, reads),
            "vote_ok": bool(ok_max and ok_dates and curve_ok is not False and consistent(state, reads)),
            "expected": expected}


def label_item(slot: str, item: dict[str, Any], panel_meta: dict[str, Any], system: str, pv: str,
               expected: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.router import get_router, vendor_of

    task, vendor, _, model = TEACHERS[slot]
    img = Path(panel_meta["path"]).read_bytes()
    res = get_router().call(task, {"system": system, "prompt": USER_MSG, "max_tokens": 500, "temperature": 0,
                                   "step_id": f"teach-{slot}-{abs(hash(item['item_id'])) % 10**10}"},
                            schema=SCHEMA, privacy_tier="PUBLIC", images=[img], only_model=model)
    task = f"{task}[pinned:{model}]"
    base = {"item_id": item["item_id"], "parcel_uid": item["parcel_uid"], "ag_year": int(item["ag_year"]),
            "season": item["season"], "teacher": DB_TEACHER.get(slot, slot), "task": task, "model_id": res.model_id,
            "resolved_model": (res.route_log or {}).get("resolved_model"), "prompt_version": pv,
            "panel_sha256": panel_meta["sha256"], "panel_path": panel_meta["path"],
            "provenance": {"chips": panel_meta["chips"], "panel_version": panel_meta["panel_version"],
                           "system_sha256": hashlib.sha256(system.encode()).hexdigest()[:16],
                           "step_id": (res.route_log or {}).get("step_id"), "latency_ms": res.latency_ms,
                           "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
                           "shadow_cost_usd": res.shadow_cost_usd,
                           "vendor": vendor_of(res.model_id) if res.model_id else None,
                           "independent": bool(res.model_id) and vendor_of(res.model_id) == vendor}}
    if not res.ok:
        filt = (res.route_log or {}).get("filtered") or []
        return {**base, "ok": False, "error": f"{res.error}; filtered={filt}"[:1500], "quota": _quota_hit(filt)}
    try:
        ans = parse_answer(res.data)
    except (ValueError, TypeError) as exc:
        return {**base, "ok": False, "error": f"bad answer: {exc}; text={str(res.text)[:300]}"}
    reads = ans.pop("reads")
    unclear = ans.pop("unclear")
    g = grounding(reads, expected or {}, ans["state"])
    base["provenance"].update(reads=reads, grounding=g, unclear=unclear)
    return {**base, **ans, "ok": True}


def expected_from(ctx: Any, fidx: pd.DataFrame, it: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
    """What a faithful reader of the panel must report: header NDVI max, the 3 chip dates, measured curve shape."""
    from planet.features import seasons as se

    k = (it["parcel_uid"], int(it["ag_year"]), it["season"])
    mx = float(fidx.loc[k, "ndvi_max"]) if k in fidx.index else float("nan")
    a, b = se.window(int(it["ag_year"]), it["season"], ctx.cfg)
    sm = ctx.smoothed[ctx.smoothed.parcel_uid == it["parcel_uid"]]
    return {"ndvi_max": round(mx, 2), "chip_dates": [pm["chips"][c]["date"] for c in ("pre", "peak", "dry")],
            "curve": measured_curve(sm, a, b)}


def regrade(conn: Any) -> Counter:
    """Recompute ``provenance.grounding`` for every stored VLM label (no model calls): adds the measured-curve
    check and self-consistency to labels graded before they existed."""
    from psycopg.types.json import Jsonb

    from planet.classify import panel as pn

    ctx = pn.Context.load()
    fidx = ctx.feats.set_index(["parcel_uid", "ag_year", "season"])
    rows = conn.execute("SELECT id, parcel_uid, ag_year, season, state, provenance FROM planet_teacher_label "
                        "WHERE ok AND teacher <> 'prior' AND provenance ? 'reads'").fetchall()
    stats: Counter = Counter()
    for rid, uid, y, sn, st, prov in rows:
        pm = {"chips": prov["chips"]}
        g = grounding(prov["reads"], expected_from(ctx, fidx, {"parcel_uid": uid, "ag_year": y, "season": sn}, pm), st)
        prov["grounding"] = g
        conn.execute("UPDATE planet_teacher_label SET provenance = %s WHERE id = %s", (Jsonb(prov), rid))
        stats["vote_ok" if g["vote_ok"] else "vote_rejected"] += 1
    conn.commit()
    return stats


def _quota_hit(filtered: list[Any]) -> bool:
    """True only for daily/monthly quota refusals (terminal for this run); per-minute limits are transient."""
    s = json.dumps(filtered, default=str).lower()
    return any(k in s for k in ("quota:rpd", "quota:tpd", "quota:monthly", "quota:cap_usd"))


def run_labels(items: pd.DataFrame, slots: Sequence[str], limit: int | None = None) -> dict[str, Counter]:
    import psycopg

    from planet.classify import panel as pn

    prompts = {s: slot_prompt(s).read_text() for s in slots}
    ctx = pn.Context.load()
    panels: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()
    stats: dict[str, Counter] = {s: Counter() for s in slots}

    def panel_for(it: dict[str, Any], style: str = "v1") -> dict[str, Any]:
        with lock:
            key = f"{it['item_id']}#{style}"
            if key not in panels:
                sub = "items" if style == "v1" else f"items_{style}"
                p = pn.PANEL_DIR / sub / f"{pn.cr.safe_name(it['item_id'])}.png"
                panels[key] = pn.render_panel(ctx, it["parcel_uid"], int(it["ag_year"]), it["season"], p, style=style)
            return panels[key]

    fidx = ctx.feats.set_index(["parcel_uid", "ag_year", "season"])

    def expected_for(it: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
        return expected_from(ctx, fidx, it, pm)

    def worker(slot: str) -> None:
        raw = prompts[slot]
        pv, system, style = prompt_version(raw), prompt_text(raw), SLOT_CFG.get(slot, (None, "v1"))[1]
        with psycopg.connect(dsn()) as conn:
            archive_slot(conn, slot)
            done = done_items(conn, slot)
            todo = [it for it in items.to_dict("records") if it["item_id"] not in done]
            if limit is not None:
                todo = todo[:limit]
            gap = TEACHERS[slot][2]
            last, fails = 0.0, 0
            for i, it in enumerate(todo, 1):
                try:
                    pm = panel_for(it, style)
                except Exception as exc:  # noqa: BLE001
                    stats[slot]["panel_error"] += 1
                    log.warning("%s panel failed for %s: %s", slot, it["item_id"], exc)
                    continue
                wait = gap - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)
                last = time.monotonic()
                row = label_item(slot, it, pm, system, pv, expected_for(it, pm))
                upsert_label(conn, row)
                stats[slot]["ok" if row["ok"] else "failed"] += 1
                if not row["ok"]:
                    fails += 1
                    log.warning("%s failed on %s: %s", slot, it["item_id"], row.get("error", "")[:300])
                    if row.get("quota"):
                        log.warning("%s: daily/monthly quota refusal - stopping this teacher; re-run later", slot)
                        break
                    if fails >= 20:
                        log.warning("%s: 20 failures in a row - stopping", slot)
                        break
                    time.sleep(min(15 * fails, 120))  # transient (rpm limit, timeout, breaker): back off
                else:
                    fails = 0
                if i % 25 == 0:
                    log.info("%s %d/%d %s", slot, i, len(todo), dict(stats[slot]))

    threads = [threading.Thread(target=worker, args=(s,), name=s) for s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return stats


# ---------------------------------------------------------------------------------------------- consensus
SPLIT_VLM_MIN_CONF = 0.6


def vote(votes: dict[str, str | None], vlm_conf: dict[str, float | None] | None = None) -> tuple[str | None, str]:
    """Majority over the available independent votes -> (label, agreement).

    3 votes: 3of3 / 2of3 / none. 2 votes (a teacher slot missing): 2of2, or on a split the VLM's label if its
    confidence >= SPLIT_VLM_MIN_CONF (``split_vlm``: the imagery outranks the weak prior; down-weighted and
    flagged), else ``split`` (no label). 1 vote: ``single`` (no label)."""
    vals = {k: v for k, v in votes.items() if isinstance(v, str) and v}
    if not vals:
        return None, "none"
    c = Counter(vals.values()).most_common()
    top, k = c[0]
    if len(vals) == 3:
        return (top, "3of3") if k == 3 else ((top, "2of3") if k == 2 else (None, "none"))
    if len(vals) == 2:
        if k == 2:
            return top, "2of2"
        vlm = [(t, v) for t, v in vals.items() if t != "prior"]
        conf = vlm_conf or {}
        if len(vlm) == 1 and (conf.get(vlm[0][0]) or 0) >= SPLIT_VLM_MIN_CONF:
            return vlm[0][1], "split_vlm"
        return None, "split"
    return None, "single"


def consensus(conn: Any, items: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for vote_name, slot in (*VOTE_SLOTS.items(), ("prior", "prior")):
        q = ("SELECT item_id, %s, state, confidence, model_id, provenance->>'independent', "
             "coalesce(provenance->'grounding'->>'vote_ok', provenance->'grounding'->>'grounded') FROM planet_teacher_label "
             "WHERE ok AND teacher = %s")
        args: list[Any] = [vote_name, DB_TEACHER.get(slot, slot)]
        if slot != "prior":
            q += " AND prompt_version = %s"
            args.append(prompt_version(slot=slot))
        parts += conn.execute(q, args).fetchall()
    lab = pd.DataFrame(parts, columns=["item_id", "teacher", "state", "confidence", "model_id", "independent", "grounded"])
    # a VLM vote counts only if independent, grounded (read-back matches the panel, its curve reading matches the
    # measured series, its state is consistent with its own reads) and not "unclear"
    lab = lab[(lab.teacher == "prior") | ((lab.independent == "true") & (lab.grounded == "true") & lab.state.notna())]
    wide = lab.pivot(index="item_id", columns="teacher", values="state")
    conf = lab.pivot(index="item_id", columns="teacher", values="confidence")
    out = items.set_index("item_id").join(wide, how="left").join(conf.add_suffix("_conf"), how="left")
    for c in ("gemini", "cohere", "prior"):
        if c not in out:
            out[c] = None
    for c in ("gemini_conf", "cohere_conf"):
        if c not in out:
            out[c] = np.nan
    res = [vote({"gemini": r["gemini"], "cohere": r["cohere"], "prior": r["prior"]},
                {"gemini": None if pd.isna(r["gemini_conf"]) else float(r["gemini_conf"]),
                 "cohere": None if pd.isna(r["cohere_conf"]) else float(r["cohere_conf"])})
           for r in out.to_dict("records")]
    out["final"] = [r[0] for r in res]
    out["agreement"] = [r[1] for r in res]
    out = out.reset_index()
    for r in out.to_dict("records"):
        src = "consensus:" + "+".join(t for t in ("gemini", "cohere", "prior") if isinstance(r.get(t), str))
        conn.execute("UPDATE parcel_season SET teacher_label = %s, teacher_src = %s, teacher_agreement = %s, "
                     "label_set = %s WHERE parcel_uid = %s AND ag_year = %s AND season = %s",
                     (r["final"], src, r["agreement"], r["label_set"], r["parcel_uid"], int(r["ag_year"]), r["season"]))
    conn.commit()
    out.to_csv(LABEL_DIR / "consensus.csv", index=False)
    return out


def agreement_report(out: pd.DataFrame) -> list[str]:
    lines = [f"items {len(out)} (train {int((out.label_set == 'train').sum())}, audit {int((out.label_set == 'audit').sum())})"]
    for a, b in (("gemini", "cohere"), ("gemini", "prior"), ("cohere", "prior")):
        m = out[a].notna() & out[b].notna()
        if m.any():
            lines.append(f"agreement {a} vs {b}: {float((out.loc[m, a] == out.loc[m, b]).mean()):.3f} (n {int(m.sum())})")
    lines.append(f"agreement levels: {out.agreement.value_counts().to_dict()}")
    lines.append(f"final labels: {out.final.value_counts(dropna=False).to_dict()}")
    for t in ("gemini", "cohere", "prior"):
        lines.append(f"{t} states: {out[t].value_counts(dropna=False).to_dict()}")
    return lines


# --------------------------------------------------------------------------------------------------- CLI
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planet.classify.teacher", description=__doc__.split("\n")[0])
    ap.add_argument("step", nargs="?", default="all", choices=["sample", "label", "regrade", "consensus", "all"])
    ap.add_argument("--pilot", type=int, default=None,
                    help="label only N seeded TRAIN items, round-robin over seasons (never audit items)")
    ap.add_argument("--limit", type=int, default=None, help="max new calls per teacher this run")
    ap.add_argument("--teachers", default="gemini,cohere")
    ap.add_argument("--resample", action="store_true", help="redraw the item sample (default: reuse items.csv)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import psycopg

    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    if args.step in ("sample", "all") and (args.resample or not ITEMS_CSV.exists()):
        from planet.extract.worldcover import OUT_PATH as WC_PATH
        from planet.features.build import FEATURES_PARQUET

        cand = candidates(pd.read_parquet(FEATURES_PARQUET), pd.read_parquet(WC_PATH) if WC_PATH.exists() else None)
        items = stratified_sample(cand)
        items[ITEM_COLS].to_csv(ITEMS_CSV, index=False)
        print(f"sampled {len(items)} items from {len(cand)} candidates -> {ITEMS_CSV}")
        print(items.groupby(["season", "prior_state"]).size().unstack(fill_value=0).to_string())
        print(items.groupby("village").size().to_dict(), items.label_set.value_counts().to_dict())
    items = load_items()
    with psycopg.connect(dsn()) as conn:
        write_priors(conn, items)
    if args.step in ("label", "all"):
        slots = [s.strip() for s in args.teachers.split(",") if s.strip()]
        todo = pilot_items(items, args.pilot) if args.pilot else items
        stats = run_labels(todo, slots, args.limit)
        print("labelling:", {k: dict(v) for k, v in stats.items()})
    if args.step in ("regrade", "all"):
        with psycopg.connect(dsn()) as conn:
            print("regrade:", dict(regrade(conn)))
    if args.step in ("consensus", "all"):
        with psycopg.connect(dsn()) as conn:
            out = consensus(conn, items)
        print("\n".join(agreement_report(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
