"""Table-driven tests for spikes/ocr_bakeoff/normalize.py.

Every case is drawn from a real value seen on a golden page, or from a worked
example in `land-domain-knowledge` §5/§6/§9/§11b.
"""
import pytest

from spikes.ocr_bakeoff.normalize import (
    SurveyRef,
    cents_to_ac,
    cents_to_ha,
    cer,
    compensation_matches,
    extents_agree,
    ha_to_ac,
    parse_date_iso,
    parse_extent_ac,
    parse_extent_ha,
    parse_money_inr,
    parse_survey_ref,
    split_merged_survey_extent,
    tamil_numerals_to_ascii,
)

EXTENT_HA_CASES = [
    ("0.95.50", 0.9550),
    ("10.30.50", 10.3050),
    ("904.40.0", 904.40),
    ("0 - 53.50", 0.5350),
    ("0-53.50", 0.5350),
    ("3.96.32", 3.9632),
    ("0.30.60", 0.3060),
    ("1.50.20", 1.5020),
    ("185.96.5", 185.9650),   # trailing zero dropped, §11b
    ("0.49.0", 0.4900),
    ("104.31.00", 104.3100),
    (None, None),
    ("", None),
    ("garbage", None),
]


@pytest.mark.parametrize("raw,expected", EXTENT_HA_CASES)
def test_parse_extent_ha(raw, expected):
    got = parse_extent_ha(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=1e-4)


EXTENT_AC_CASES = [
    ("2.35", 2.35),
    ("8.82", 8.82),
    ("27.947", 27.947),
    ("0.79", 0.79),
    (None, None),
    ("nope", None),
]


@pytest.mark.parametrize("raw,expected", EXTENT_AC_CASES)
def test_parse_extent_ac(raw, expected):
    got = parse_extent_ac(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=1e-4)


def test_ha_to_ac_exact_factor():
    # helpers round to 4dp, per land-domain-knowledge §9 ("report hectares to 4dp")
    assert ha_to_ac(1.0) == pytest.approx(2.47105, abs=1e-4)


def test_cents_conversions():
    assert cents_to_ac(4) == pytest.approx(0.04)
    assert cents_to_ha(4) == pytest.approx(4 * 40.4686 / 10_000, abs=1e-4)


EXTENTS_AGREE_CASES = [
    # (ha, ac, expected) -- both the exact 2.47105 factor and the common
    # rounded-2.47 factor used by the AS letter (land-domain-knowledge §5) must pass.
    (0.9550, 2.36, True),
    (186.455, 460.54, True),   # AS letter uses the 2.47 factor for this one
    (1.0, 2.47105, True),
    (1.0, 2.60, False),
    (None, 2.0, None),
    (1.0, None, None),
]


@pytest.mark.parametrize("ha,ac,expected", EXTENTS_AGREE_CASES)
def test_extents_agree(ha, ac, expected):
    assert extents_agree(ha, ac) is expected


MONEY_CASES = [
    ("₹12,60,000/-", 1260000),
    ("1,35,496/-", 135496),
    ("Rs. 11,75,000/-", 1175000),
    ("2,37,118/-", 237118),
    (None, None),
    ("", None),
]


@pytest.mark.parametrize("raw,expected", MONEY_CASES)
def test_parse_money_inr(raw, expected):
    assert parse_money_inr(raw) == expected


def test_compensation_matches():
    assert compensation_matches(1260000, 2.52, 500000)
    assert not compensation_matches(1260000, 2.52, 900000)


SURVEY_CASES = [
    ("177/4A", [SurveyRef("177", "4A")]),
    ("173/1", [SurveyRef("173", "1")]),
    ("18/3G", [SurveyRef("18", "3G")]),
    (
        "172/1 மற்றும் 172/2",
        [SurveyRef("172", "1"), SurveyRef("172", "2")],
    ),
    ("6,7,8,10A", [SurveyRef("6", None), SurveyRef("7", None), SurveyRef("8", None), SurveyRef("10A", None)]),
    ("1 / 6,7,8,10A", [SurveyRef("1", "6,7,8,10A")]),
    ("", []),
    (None, []),
]


@pytest.mark.parametrize("raw,expected", SURVEY_CASES)
def test_parse_survey_ref(raw, expected):
    assert parse_survey_ref(raw) == expected


def test_split_merged_survey_extent():
    # "173/20.86.00" -> survey_no 173 handled by caller; this parses the tail
    # "20.86.00" into sub_div "2" and extent_ha 0.86 (land-domain-knowledge §11b).
    assert split_merged_survey_extent("20.86.00") == ("2", 0.86)
    assert split_merged_survey_extent("garbage") is None


DATE_CASES = [
    ("28.10.2022", "2022-10-28"),
    ("03/11/2022", "2022-11-03"),
    ("21.03.2025", "2025-03-21"),
    ("26.02.2021", "2021-02-26"),
    ("31.12.2019", None),   # outside the 2020-2026 sanity range
    ("bad", None),
    (None, None),
]


@pytest.mark.parametrize("raw,expected", DATE_CASES)
def test_parse_date_iso(raw, expected):
    assert parse_date_iso(raw) == expected


def test_tamil_numerals_to_ascii():
    assert tamil_numerals_to_ascii("௧௨௩") == "123"
    assert tamil_numerals_to_ascii("173/1") == "173/1"


def test_cer_identical_and_different():
    assert cer("hello", "hello") == 0.0
    assert cer("hello", "") == 1.0
    assert cer("abc", "abd") == pytest.approx(1 / 3)
    assert cer("", "anything") is None
