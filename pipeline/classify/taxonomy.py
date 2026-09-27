"""Doc-type / stage / scheme taxonomy (T1.6, updated per D-025 from the qa golden-set labelling
in eval/classify/README.md).

`doc_type` describes the document's *form*. `stage` is the acquisition lifecycle stage it
evidences (land-domain-knowledge §2) and is a separate field, because form and stage don't
line up 1:1 (a demand draft to the District Judge is BANK_INSTRUMENT / PAYMENT; an award
proceeding filed in the "Amount Disbursed" folder is AWARD_7_2 / AWARD, not PAYMENT).
"""
from __future__ import annotations

# -- stages (land-domain-knowledge §2) --------------------------------------------------------
STAGE_GO_AS_LPS = "GO_AS_LPS"
STAGE_SEC_3_1 = "SEC_3_1"
STAGE_SEC_3_2 = "SEC_3_2"
STAGE_EXEMPTION = "EXEMPTION"
STAGE_PRICE_NEGOTIATION = "PRICE_NEGOTIATION"
STAGE_POSSESSION_NOTICE = "POSSESSION_NOTICE"
STAGE_AWARD = "AWARD"
STAGE_PAYMENT = "PAYMENT"
STAGE_POSSESSION = "POSSESSION"
STAGE_MUTATION = "MUTATION"

STAGES = (
    STAGE_GO_AS_LPS, STAGE_SEC_3_1, STAGE_SEC_3_2, STAGE_EXEMPTION, STAGE_PRICE_NEGOTIATION,
    STAGE_POSSESSION_NOTICE, STAGE_AWARD, STAGE_PAYMENT, STAGE_POSSESSION, STAGE_MUTATION,
)

# -- doc_type -> stage (D-025). None = no clean single-stage mapping. ------------------------
STAGE_OF: dict[str, str | None] = {
    "GO": STAGE_GO_AS_LPS,
    "AS": STAGE_GO_AS_LPS,
    "AS_PROPOSAL": STAGE_GO_AS_LPS,
    "LPS": STAGE_GO_AS_LPS,
    "SEC31_GAZETTE": STAGE_SEC_3_1,
    "SEC32_NOTICE": STAGE_SEC_3_2,
    "SEC32_ERRATA": STAGE_SEC_3_2,
    "EXEMPTION_PROPOSAL": STAGE_EXEMPTION,
    "EXEMPTION_GO": STAGE_EXEMPTION,
    "DLPNC": STAGE_PRICE_NEGOTIATION,
    "SLPNC": STAGE_PRICE_NEGOTIATION,
    "LAND_VALUE": STAGE_PRICE_NEGOTIATION,
    "FUNDS": STAGE_PRICE_NEGOTIATION,
    "FORM_E": STAGE_POSSESSION_NOTICE,
    "AWARD_7_2": STAGE_AWARD,
    "AWARD_7_3": STAGE_AWARD,
    "FORM_F": STAGE_AWARD,
    "DISBURSEMENT": STAGE_PAYMENT,
    "COURT_DEPOSIT": STAGE_PAYMENT,
    "BANK_INSTRUMENT": STAGE_PAYMENT,
    "LDR": STAGE_POSSESSION,
    "CHITTA": STAGE_MUTATION,
    # new D-025 classes and the catch-all have no single clean lifecycle stage
    "CALCULATION_SHEET": None,
    "CLASSIFICATION_ORDER": None,
    "CORRESPONDENCE": None,
    "OTHER": None,
}

DOC_TYPES = tuple(STAGE_OF)

SCHEME_ALLIKULAM = "allikulam"
SCHEME_OTHER = "other_scheme"
SCHEME_UNKNOWN = "unknown"
SCHEME_VALUES = (SCHEME_ALLIKULAM, SCHEME_OTHER, SCHEME_UNKNOWN)


def stage_of(doc_type: str | None) -> str | None:
    if not doc_type:
        return None
    return STAGE_OF.get(doc_type)
