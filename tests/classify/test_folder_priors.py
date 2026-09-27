from pipeline.classify.folder_priors import folder_disagrees, folder_prior_type


def test_disagreement_flagged_for_known_folder():
    assert folder_disagrees("Amount Disbursed", "AWARD_7_2") is True


def test_no_disagreement_when_type_matches_folder():
    assert folder_disagrees("Form E", "FORM_E") is False


def test_ldr_folder_alias_possession_taken():
    assert folder_disagrees("Possession Taken", "LDR") is False
    assert folder_disagrees("LDR Issued", "LDR") is False


def test_unknown_folder_never_flags():
    assert folder_disagrees("Some New Folder", "OTHER") is False


def test_none_inputs_never_flag():
    assert folder_disagrees(None, "OTHER") is False
    assert folder_disagrees("Form E", None) is False


def test_reliable_folder_gives_single_prior():
    assert folder_prior_type("Government Order (GO)") == "GO"
    assert folder_prior_type("Form E") == "FORM_E"


def test_unreliable_folder_gives_no_prior():
    # "Amount Disbursed" agreed 1/17 in the golden set: never trusted as a prior.
    assert folder_prior_type("Amount Disbursed") is None
    assert folder_prior_type("DLPNC") is None


def test_ambiguous_as_folder_gives_no_single_prior():
    # AS folder maps to {AS, AS_PROPOSAL} -- not a singleton, so no fallback is offered.
    assert folder_prior_type("Administrative Sanction (AS)") is None
