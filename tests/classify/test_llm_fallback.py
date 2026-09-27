from pipeline.classify.llm_fallback import redact


def test_redact_masks_owner_name_patta_and_amount():
    text = "த/பெ Muthu Kumar, பட்டா எண் : 1663, compensation ₹12,60,000/- paid"
    out = redact(text)
    assert "Muthu Kumar" not in out
    assert "1663" not in out
    assert "12,60,000" not in out
    assert "[REDACTED_NAME]" in out
    assert "[REDACTED_PATTA]" in out
    assert "[REDACTED_AMOUNT]" in out


def test_redact_leaves_ordinary_text_untouched():
    text = "SIPCOT Allikulam Scheme Unit-7 Block-9 notice"
    assert redact(text) == text
