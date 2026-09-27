from pipeline.classify.rules import best_rule_match


def _top(text: str) -> str | None:
    hits = best_rule_match(text, len(text.replace(" ", "")))
    return hits[0].doc_type if hits else None


def test_go_english_letterhead():
    text = "GOVERNMENT OF TAMIL NADU\nABSTRACT\nIndustries (SIPCOT-LA) Department -- ORDER\nG.O.(Ms) No.100 dated 26.02.2021"
    assert _top(text) == "GO"


def test_as_letter():
    text = "Administrative Sanction\nLr.No.LA/TUT/Allikulam/2020 dated 31.03.2021"
    assert _top(text) == "AS"


def test_sec31_gazette():
    text = "TAMIL NADU\nGOVERNMENT GAZETTE\nEXTRAORDINARY\nPUBLISHED BY AUTHORITY\nNo. 527] CHENNAI"
    assert _top(text) == "SEC31_GAZETTE"


def test_sec32_errata_beats_notice():
    text = "Form A notice under section 3(2). This is an Errata to the earlier publication."
    assert _top(text) == "SEC32_ERRATA"


def test_sec32_notice():
    text = "படிவம் அ\nபிரிவு 3 ( 2 ) அறிவிப்பு"
    assert _top(text) == "SEC32_NOTICE"


def test_exemption_go_beats_generic_go_letterhead():
    # An exemption G.O. shares the same ABSTRACT/ORDER letterhead as a scheme-sanction GO; the
    # more specific EXEMPTION_GO must win, not the generic GO rule.
    text = (
        "GOVERNMENT OF TAMIL NADU\nABSTRACT\nIndustries (SIPCOT-LA) Department -- ORDER\n"
        "Exemption from acquisition granted. G.O.(Ms) No.45 dated 01.01.2022."
    )
    assert _top(text) == "EXEMPTION_GO"


def test_exemption_go_vs_proposal():
    go_text = "Exemption from acquisition. G.O.(Ms) No.45 dated 01.01.2022 grants exemption."
    proposal_text = "Exemption Initiated: SIPCOT recommends the following survey numbers for exemption, remarks enclosed."
    assert _top(go_text) == "EXEMPTION_GO"
    assert _top(proposal_text) == "EXEMPTION_PROPOSAL"


def test_slpnc_wins_over_dlpnc_mention():
    text = "Notes for approval of land value by the State Level Price Negotiation Committee (SLPNC) as determined by DLPNC on 12.11.2024."
    assert _top(text) == "SLPNC"


def test_dlpnc_alone():
    text = "Minutes of the District Level Price Negotiation Committee (DLPNC) meeting held on 24.08.2023."
    assert _top(text) == "DLPNC"


def test_form_e():
    text = "FORM E\n(See Rule 8)\nNotice under section 4(2) to surrender possession within 30 days."
    assert _top(text) == "FORM_E"


def test_form_f_beats_award_mentions():
    text = "FORM F\n(See Rule 10)\nAgreement regarding compensation under section 7(2) of the Act."
    assert _top(text) == "FORM_F"


def test_award_7_2_vs_7_3():
    text_72 = "AWARD passed under section 7(2) of the TN Act 10 of 1999, consent of the owner recorded."
    text_73 = "AWARD passed under section 7(3) as the owners did not consent to the negotiated value."
    assert _top(text_72) == "AWARD_7_2"
    assert _top(text_73) == "AWARD_7_3"


def test_bank_instrument_short_dd_scan():
    text = "DEMAND DRAFT No.123456 dated 01.02.2024 IOB Thoothukudi Branch PAY: Principal District Judge"
    assert _top(text) == "BANK_INSTRUMENT"


def test_court_deposit_letter_prose():
    text = (
        "To,\nThe Principal District Judge, Thoothukudi.\nSir,\nSub: Court deposit of compensation "
        "amount for land acquired at Allikulam village under section 7(3) of the Act. "
        "We enclose herewith the demand draft for depositing the amount in your court. " * 3
    )
    assert _top(text) == "COURT_DEPOSIT"


def test_disbursement_treasury_payment_advice():
    text = "Treasury Payment Advice: bank credit via NEFT to the following pattadars, list enclosed."
    assert _top(text) == "DISBURSEMENT"


def test_ldr_possession_certificate():
    text = "Land Delivery Receipt. Possession of the land has been handed over to SIPCOT on 21.03.2025."
    assert _top(text) == "LDR"


def test_chitta_eservices():
    text = "வட்டாட்சியர் அலுவலக இணைய சேவை - நில உரிமை விபரங்கள்\nபட்டா எண் : 1663"
    assert _top(text) == "CHITTA"


def test_calculation_sheet():
    text = "Calculation of Compensation Amount, Block-5 SF 31/2 (Unit-8), total worked out below."
    assert _top(text) == "CALCULATION_SHEET"


def test_award_recital_of_earlier_notice_is_not_misread_as_the_notice():
    # Regression (T1.6 evaluation): an AWARD document recites the earlier s.3(2) notice history
    # deep in its preamble; that recital must not itself be classified SEC32_NOTICE.
    text = (
        "AWARD PROCEEDINGS Block 5\n" + ("filler " * 80) +
        "Whereas notice under section 3 (2) of the Act was published on 03.08.2023 and "
        "the award is now passed under section 7(2) of the Act determining compensation."
    )
    assert _top(text) == "AWARD_7_2"


def test_exemption_and_unrelated_go_reference_do_not_combine():
    # Regression (T1.6 evaluation): "Exemption" and a G.O. number appearing far apart (an SLPNC
    # proposal letter's reference list citing an unrelated exemption letter) must not combine
    # into a false EXEMPTION_GO match.
    text = (
        "Sub: Land Value to be fixed by SLPNC as determined by the DLPNC.\n"
        "Ref 1) GOMs.No.100, Industries (SIPCOT-LA) Department, dated 26.02.2021\n"
        "Ref 3) Letter No LA083/Allikulam/exemption/2021 dated 12.09.2023 of the Managing Director"
    )
    assert _top(text) == "SLPNC"


def test_exemption_go_still_matches_when_close_together():
    text = "Exemption from acquisition. G.O.(Ms) No.45 dated 01.01.2022 grants the exemption."
    assert _top(text) == "EXEMPTION_GO"


def test_no_match_returns_empty():
    assert best_rule_match("completely unrelated text with no keywords at all", 40) == []
