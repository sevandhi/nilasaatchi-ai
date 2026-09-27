"""PII property tests: a PSEUDO payload never contains an owner name (plan: phase4 skill)."""
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from app.router.gateway import Pseudonymiser, residual_names

TAMIL = st.text(alphabet=[chr(c) for c in range(0x0B85, 0x0BB9) if chr(c).isalpha()], min_size=4, max_size=10)
LATIN = st.from_regex(r"[A-Z][a-z]{3,9}", fullmatch=True)
NAME = st.one_of(TAMIL, LATIN)
TEMPLATES = ["நில உரிமையாளர்: {n} த/பெ {f}, புல எண் 233", "Thiru. {n} S/o {f} received Rs.12,60,000/-",
             "Tmt. {n} W/o {f}", "owner {n} and heir {f} signed", "{n} மனைவி {f}"]


@settings(max_examples=150, deadline=None)
@given(n=NAME, f=NAME, tpl=st.sampled_from(TEMPLATES))
def test_pseudo_payload_has_no_owner_names(n, f, tpl):
    p = Pseudonymiser([n, f])
    payload = {"text": tpl.format(n=n, f=f), "rows": [{"owner": n, "patta_no": "1234", "extent_ha": 0.95},
                                                     {"fact_type": "owner", "value_text": f}],
               "nested": [{"relation_name": f, "note": f"see {n}"}]}
    out = p.pseudonymise(payload)
    assert residual_names(out, [n, f]) == []
    assert p.reinsert(out)["rows"][0]["owner"] == n


def test_marker_names_without_known_list():
    p = Pseudonymiser()
    out = p.text("Thiru. Karuppasamy S/o Veerabhadran; நில உரிமையாளர்: ஆறுமுகம் த/பெ கணேசன்")
    assert residual_names(out, ["Karuppasamy", "Veerabhadran", "ஆறுமுகம்", "கணேசன்"]) == []


def test_fixture_page_zero_residual_tamil_names(kg_dsn):
    from app.agent.runtime import known_names
    from app.tools.db import query
    names = known_names(kg_dsn)
    assert len(names) >= 40
    pages = query(kg_dsn, "SELECT text FROM page ORDER BY id", readonly_role=False)
    p = Pseudonymiser(names)
    out = p.pseudonymise({"pages": [r["text"] for r in pages]})
    assert residual_names(out, [n for n in names if len(n) > 3]) == []
    assert "⟨OWNER_" in json.dumps(out, ensure_ascii=False)


def test_ocr_noise_names_do_not_mask_request_words():
    from app.agent.runtime import clean_names
    from app.domains import get_pack
    from app.router.gateway import Pseudonymiser
    names = ["இழப்பீட்டுத் தொகை", "கள்", "100% ஆறுதல் தொகை", "முருகன்"]
    kept = clean_names(names, get_pack("land_acquisition").non_name_words)
    assert kept == ("முருகன்",)
    q = "மேலத்தட்டப்பாறை கிராமத்தில் இழப்பீட்டுத் தொகை முரண்பாடுகள் எத்தனை? முருகன்"
    out = Pseudonymiser(kept).text(q)
    assert "இழப்பீட்டுத் தொகை முரண்பாடுகள்" in out and "முருகன்" not in out


def test_agent_never_routes_to_bedrock_by_default(monkeypatch):
    from app.tools.context import agent_excluded_models
    monkeypatch.delenv("AGENT_EXCLUDE_MODELS", raising=False)
    assert {"bedrock-ministral-8b", "bedrock-ministral-3b"} <= set(agent_excluded_models())
