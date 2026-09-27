"""intake: modality detection, domain-pack selection, deterministic slot extraction, missing info / clarify."""
from __future__ import annotations

from app.agent.nodes import out
from app.agent.runtime import Emitter, pseudo_for, remember, save_pseudo
from app.agent.state import RunState
from app.domains import select_pack
from app.domains.common_slots import attachment_kind, detect_lang


async def intake(state: RunState) -> dict:
    st = state.model_copy(deep=True)
    st.status = "running"
    p = pseudo_for(st)
    em = Emitter(st, "intake", p)
    for a in st.attachments:
        a.kind = attachment_kind(a)
    req = st.request + (f"\nClarification: {st.clarify_answer}" if st.clarify_answer else "")
    pack, scores = select_pack(req, st.attachments, st.domain if st.domain_explicit else None)
    st.domain, st.domain_scores = pack.name, scores
    sr = pack.extract_slots(req, st.attachments)
    slots = sr.slots
    if pack.resolve_slots is not None:
        try:
            from app.tools.context import kg_dsn
            slots = pack.resolve_slots(slots, kg_dsn())
        except Exception as e:  # noqa: BLE001
            st.errors.append({"node": "intake", "message": f"slot resolution failed: {str(e)[:200]}"})
    st.slots, st.missing_info, st.assumptions = slots, sr.missing, sr.assumptions
    st.lang = detect_lang(st.request)
    em.emit("status", {"node": "intake", "message": f"domain={pack.name}",
                       "modalities": sorted({a.kind for a in st.attachments} | {"text"}),
                       "slots": slots.model_dump(exclude={"polygons_ref"}), "domain_scores": scores,
                       "assumptions": st.assumptions})
    blocking = [m for m in st.missing_info if m.blocking]
    if blocking:
        st.status, st.next_node = "clarify", "end"
        em.emit("clarify", {"question": " ".join(m.question for m in blocking), "missing": [m.slot for m in blocking]})
    else:
        st.next_node = "planner"
    st.ledger_head = em.close() or st.ledger_head
    save_pseudo(st, p)
    remember(st)
    return out(st)
