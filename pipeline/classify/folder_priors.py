"""Folder label -> the doc_type(s) a naive "trust the folder" classifier would guess. Used only
for reporting folder-vs-content disagreement (land-domain-knowledge §3); never used to decide
`classified_type` itself."""
from __future__ import annotations

FOLDER_EXPECTED_TYPE: dict[str, set[str]] = {
    "Administrative Sanction (AS)": {"AS", "AS_PROPOSAL"},
    "Government Order (GO)": {"GO"},
    "Land Plan Schedule (LPS)": {"LPS"},
    "3(1) Publish": {"SEC31_GAZETTE"},
    "3(1) Published": {"SEC31_GAZETTE"},
    "3(2) Errata": {"SEC32_ERRATA"},
    "3(2) Notice": {"SEC32_NOTICE"},
    "3(2) Noticed": {"SEC32_NOTICE"},
    "Exemption Initiated": {"EXEMPTION_PROPOSAL"},
    "Exemption GO Approved": {"EXEMPTION_GO"},
    "DLPNC": {"DLPNC"},
    "SLPNC": {"SLPNC"},
    "Land Value Fixation": {"LAND_VALUE"},
    "Funds-Allocated": {"FUNDS"},
    "Form E": {"FORM_E"},
    "7(2) Award": {"AWARD_7_2"},
    "7(2) Awarded": {"AWARD_7_2"},
    "7(3) Awarded": {"AWARD_7_3"},
    "Form F": {"FORM_F"},
    "Amount Disbursed": {"DISBURSEMENT"},
    "Amount Disbursed 7(2) & 7(3)": {"DISBURSEMENT"},
    "Amount Disbursed Pattadars 7(2) & 7(3)": {"DISBURSEMENT"},
    "Court Deposit": {"COURT_DEPOSIT"},
    "LDR Issued": {"LDR"},
    "Possession Taken": {"LDR"},
    "Patta Transferred": {"CHITTA"},
}


def folder_disagrees(folder_label: str | None, classified_type: str | None) -> bool:
    if not folder_label or not classified_type:
        return False
    expected = FOLDER_EXPECTED_TYPE.get(folder_label)
    if not expected:
        return False
    return classified_type not in expected


# Folders where the qa golden-set sample (eval/classify/README.md) found 100% folder/content
# agreement. Used as a last-resort, medium-confidence prior *only* when the content rules are
# ambiguous (T1.6 method step 2) -- never to override a confident content match, and never for
# folders known to disagree often (e.g. "Amount Disbursed": 1/17; "DLPNC": 0/6).
FOLDER_RELIABLE = {
    "3(1) Published", "3(2) Errata", "3(2) Notice", "3(2) Noticed", "7(3) Awarded", "Form E",
    "Form F", "Funds-Allocated", "LDR Issued", "Possession Taken", "Patta Transferred", "SLPNC",
    "Government Order (GO)",
}


def folder_prior_type(folder_label: str | None) -> str | None:
    """A single unambiguous doc_type prior for a reliable folder, or None."""
    if folder_label not in FOLDER_RELIABLE:
        return None
    expected = FOLDER_EXPECTED_TYPE.get(folder_label)
    if expected and len(expected) == 1:
        return next(iter(expected))
    return None
