"""present: deterministic WorkspaceSpec (compose_workspace) + bilingual narrative from the presenter model on a
PSEUDO payload; post-check forbids numbers that were not in the model's input and requires a claim id next to
every number (else a deterministic template narrative). Names are re-inserted locally only."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.agent.nodes import out
from app.agent.nodes.execute import load_outputs
from app.agent.runtime import Emitter, account, llm_json, pseudo_for, remember, save_pseudo, sha
from app.agent.state import Narrative, PresentOutput, RunState
from app.agent.store import runs_dir
from app.tools.context import kg_dsn
from app.tools.workspace import compose

PROMPT = Path(__file__).resolve().parents[3] / "prompts" / "present.md"
NUM = re.compile(r"(?<![\w⟨])[-+]?\d[\d,]*(?:\.\d+)?")
REFTAG = re.compile(r"\[(c_[A-Za-z0-9_]+)\]")
TOKEN = re.compile(r"⟨[A-Z]+_\d+⟩")


def _norm(n: str) -> str:
    s = n.replace(",", "").lstrip("+")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def narrative_check(text: str, payload_blob: str, claim_ids: set[str]) -> list[str]:
    problems = []
    allowed = {_norm(x) for x in NUM.findall(payload_blob)}
    for sent in re.split(r"(?<=[.!?।])\s+|\n", text):
        clean = TOKEN.sub("", REFTAG.sub("", sent))
        nums = [_norm(x) for x in NUM.findall(clean)]
        if not nums:
            continue
        bad = [n for n in nums if n not in allowed]
        if bad:
            problems.append(f"new numbers {bad[:3]}")
        refs = REFTAG.findall(sent)
        if not refs:
            problems.append("number without claim id")
        elif any(r not in claim_ids for r in refs):
            problems.append("unknown claim id")
    return problems


META = re.compile(r"\b(wait|let's|json|schema|claim_refs|prompt)\b", re.IGNORECASE)


def sane(text: str, ref_len: int) -> bool:
    """Reject rambling / meta-talk (seen live: a Tamil field that kept 'correcting' itself)."""
    return bool(text) and len(text) <= max(1500, 3 * ref_len) and not META.search(text)


def template(st: RunState, items: list[dict]) -> Narrative:
    acc = [i for i in items if i["verdict"] == "ACCEPT"][:6]
    other = [i for i in items if i["verdict"] != "ACCEPT"][:3]
    en = [f"Answer to: {st.request[:160]}"]
    ta = [f"கேள்விக்கான பதில்: {st.request[:160]}"]
    for i in acc:
        en.append(f"Verified: {i['field'].replace('_', ' ')} for {i['subject']} = {i['value']} [{i['id']}].")
        ta.append(f"சரிபார்க்கப்பட்டது: {i['field'].replace('_', ' ')} ({i['subject']}) = {i['value']} [{i['id']}].")
    for i in other:
        en.append(f"Not fully verified ({i['verdict']}): {i['field'].replace('_', ' ')} for {i['subject']} = "
                  f"{i['value']} [{i['id']}].")
        ta.append(f"முழுமையாக சரிபார்க்கப்படவில்லை ({i['verdict']}): {i['field']} ({i['subject']}) = {i['value']} [{i['id']}].")
    if not items:
        en.append("No verifiable figures were produced; see the tables and caveats.")
        ta.append("சரிபார்க்கக்கூடிய எண்கள் இல்லை; அட்டவணைகளைப் பார்க்கவும்.")
    return Narrative(en=" ".join(en), ta=" ".join(ta), claim_refs=[i["id"] for i in acc + other])


async def present(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    p = pseudo_for(st)
    em = Emitter(st, "present", p)
    outputs = load_outputs(st)
    order = {"kpi": 0, "finding": 1, "discrepancy": 1, "entity": 2, "table_cell": 3}
    chosen = sorted([c for c in st.claims if c.verdict in ("ACCEPT", "DOWNGRADE", "REVIEW")],
                    key=lambda c: (c.verdict != "ACCEPT", order.get(c.kind, 4), c.id))[:30]
    items = p.pseudonymise([{"id": c.id, "kind": c.kind, "subject": c.subject, "field": c.field, "value": c.value,
                             "unit": c.unit, "verdict": c.verdict, "confidence": round(c.confidence, 2)} for c in chosen])
    caveats = list(dict.fromkeys(c for o in outputs.values() for c in o.get("caveats", [])))[:5]
    payload = {"system": PROMPT.read_text(encoding="utf-8"), "prompt": f"User request: {p.text(st.request)}",
               "claims": items, "caveats": caveats, "assumptions": st.assumptions, "max_tokens": 3000}  # Tamil is token-heavy; 1200 truncated JSON
    blob = json.dumps({k: payload[k] for k in ("prompt", "claims", "caveats", "assumptions")}, ensure_ascii=False)
    ids = {c.id for c in chosen}
    narrative, check = None, {"passed": False, "problems": []}
    if items:
        obj, res, logs = await llm_json(em, "present", payload, PresentOutput, privacy_tier="PSEUDO",
                                        run_id=st.run_id, step_id="present")
        account(st, logs, "present")
        if obj is not None:
            probs = narrative_check(obj.en, blob, ids)
            if not sane(obj.en, 400):
                probs.append("en narrative failed the sanity check")
            if obj.ta and (narrative_check(obj.ta, blob, ids) or not sane(obj.ta, len(obj.en))):
                obj.ta = template(st, items).ta          # keep the checked English, template Tamil
                em.emit("fallback", {"step_id": "present", "task": "present", "from": res.model_id,
                                     "to": "template Tamil narrative", "reason": "Tamil text failed the post-check"})
            check = {"passed": not probs, "problems": probs[:6], "model_id": res.model_id}
            if not probs:
                refs = sorted(set(REFTAG.findall(obj.en + " " + (obj.ta or ""))) & ids)
                narrative = Narrative(en=obj.en, ta=obj.ta, claim_refs=refs)
            else:
                em.emit("fallback", {"step_id": "present", "task": "present", "from": res.model_id,
                                     "to": "template narrative", "reason": "; ".join(probs[:3])})
    if narrative is None:
        narrative = template(st, items)
    st.plan_meta = {**st.plan_meta, "narrative_check": check}
    st.narrative = Narrative(**p.reinsert(narrative.model_dump()))          # local re-insertion only
    st.status = "done"
    spec = compose(st, outputs, kg_dsn(), p)
    spec.ledger_head = em.head[1] if em.head else st.ledger_head
    st.workspace = spec
    st.telemetry.latency_ms = sum(r.latency_ms for r in st.steps.values())
    try:   # end-to-end wall clock (all LLM + tool calls), not only tool steps
        from datetime import datetime
        t0 = datetime.fromisoformat(st.created_at)
        st.telemetry.latency_ms = max(st.telemetry.latency_ms, int((datetime.now(t0.tzinfo) - t0).total_seconds() * 1000))
    except (TypeError, ValueError):
        pass
    pub = st.public_dict()
    ws = spec.model_dump(mode="json")
    em.emit("result", {"state": pub, "workspace": ws, "degraded": st.degraded},
            ledger_payload={"type": "result", "workspace_sha256": sha(ws), "state_sha256": sha(pub),
                            "n_claims": len(st.claims), "verdicts": spec.verification.summary.model_dump(),
                            "degraded": st.degraded})
    st.ledger_head = em.close() or st.ledger_head
    st.workspace.ledger_head = st.ledger_head
    d = runs_dir() / st.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(st.public_dict(), ensure_ascii=False, default=str), encoding="utf-8")
    st.next_node = "end"
    save_pseudo(st, p)
    remember(st)
    return out(st)
