from pipeline.classify.taxonomy import DOC_TYPES, STAGE_OF, STAGES, stage_of


def test_every_doc_type_has_a_stage_entry():
    for dt in DOC_TYPES:
        assert dt in STAGE_OF


def test_stage_values_are_from_the_canonical_10():
    for stage in STAGE_OF.values():
        assert stage is None or stage in STAGES


def test_form_and_function_are_split_for_bank_instrument():
    # D-025: a DD to the District Judge is BANK_INSTRUMENT (form), stage PAYMENT.
    assert stage_of("BANK_INSTRUMENT") == "PAYMENT"
    assert stage_of("COURT_DEPOSIT") == "PAYMENT"
    assert stage_of("AWARD_7_2") == "AWARD"  # not PAYMENT, even though it's often paid together


def test_new_d025_classes_have_no_forced_stage():
    for dt in ("CALCULATION_SHEET", "CLASSIFICATION_ORDER", "CORRESPONDENCE", "OTHER"):
        assert stage_of(dt) is None


def test_stage_of_unknown_or_none_type():
    assert stage_of(None) is None
    assert stage_of("NOT_A_REAL_TYPE") is None
