"""D-032/D-033: owner-safety redaction. Pure unit, no DB."""
from __future__ import annotations

from app.api.mask import mask_names, safe_row_json, strip_bank_accounts


def test_strip_bank_accounts_removes_account_like_keys_unconditionally():
    row = {"payee": "A. Kumar", "bank_account_no": "1234567890", "ifsc": "SBIN0001", "amount_rs": 500}
    out = strip_bank_accounts(row)
    assert "bank_account_no" not in out
    assert "ifsc" not in out
    assert out["amount_rs"] == 500
    assert out["payee"] == "A. Kumar"  # names are a separate concern (mask_names)


def test_strip_bank_accounts_recurses_into_nested_rows():
    row = {"raw": {"instrument": {"account_no": "999"}}}
    assert "account_no" not in strip_bank_accounts(row)["raw"]["instrument"]


def test_mask_names_redacts_owner_and_payee_shaped_keys():
    row = {"payee": "A. Kumar", "owner": "B. Devi", "amount_rs": 500,
          "nested": {"relation_name": "C. Raman"}}
    out = mask_names(row)
    assert out["payee"] == "[REDACTED]"
    assert out["owner"] == "[REDACTED]"
    assert out["nested"]["relation_name"] == "[REDACTED]"
    assert out["amount_rs"] == 500  # non-name fields untouched


def test_safe_row_json_masked_true_strips_both():
    row = {"payee": "A. Kumar", "bank_account_no": "123"}
    out = safe_row_json(row, masked=True)
    assert out == {"payee": "[REDACTED]"}


def test_safe_row_json_masked_false_still_strips_bank_accounts():
    row = {"payee": "A. Kumar", "bank_account_no": "123"}
    out = safe_row_json(row, masked=False)
    assert out == {"payee": "A. Kumar"}  # unmasked names allowed, but never bank accounts
