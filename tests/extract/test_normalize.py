"""Table-driven tests for pipeline.normalize — every example in land-domain-knowledge §5–§7."""
import pytest

from pipeline.normalize.dates import parse_date
from pipeline.normalize.numbers import (
    compensation_matches, extents_agree, parse_amount, parse_cents, parse_extent_ac, parse_extent_ha,
    tamil_numerals_to_ascii,
)
from pipeline.normalize.survey import canon_subdiv, parse_survey_ref, split_merged_survey_extent
from pipeline.normalize.village import normalise_village


@pytest.mark.parametrize("raw,ha", [
    ("0.95.50", 0.955), ("10.30.50", 10.305), ("904.40.0", 904.40), ("904.40.00", 904.40),   # §5 triple
    ("0 - 53.50", 0.535), ("0-53.50", 0.535),                                               # §5 chitta
    ("௦.௯௫.௫௦", 0.955),                                                                     # Tamil numerals
    ("", None), ("--", None),
])
def test_extent_ha(raw, ha):
    v = parse_extent_ha(raw)
    assert (v is None and ha is None) or abs(v - ha) < 1e-9


@pytest.mark.parametrize("ha,ac,ok", [
    (186.455, 460.54, True),     # §5 AS letter, factor 2.47
    (186.455, 460.74, True),     # same extent, factor 2.47105
    (0.955, 2.35, True), (0.86, 2.12, True), (11.31, 27.947, True),
    (0.955, 2.60, False), (186.455, 470.0, False),
])
def test_ha_ac_agree(ha, ac, ok):
    assert extents_agree(ha, ac) is ok


@pytest.mark.parametrize("raw,val", [("2.35", 2.35), ("460.54", 460.54), ("0.79", 0.79)])
def test_extent_ac(raw, val):
    assert abs(parse_extent_ac(raw) - val) < 1e-9


@pytest.mark.parametrize("raw,val", [("3.5", 3.5), ("3½", 3.5), ("12", 12.0)])
def test_cents(raw, val):
    assert parse_cents(raw) == pytest.approx(val)


@pytest.mark.parametrize("raw,val", [
    ("₹12,60,000/-", 1260000), ("1,35,496/-", 135496), ("Rs.5,00,000", 500000), ("11,75,000", 1175000),
    ("95,64,800/-", 9564800),
])
def test_amount(raw, val):
    assert parse_amount(raw) == val


@pytest.mark.parametrize("amt,ac,rate,ok", [
    (1260000, 2.52, 500000, True),     # §5 example
    (1175000, 2.35, 500000, True), (1300000, 2.52, 500000, False),
])
def test_compensation(amt, ac, rate, ok):
    assert compensation_matches(amt, ac, rate) is ok


@pytest.mark.parametrize("raw,out", [("௦௧௨௩௪௫௬௭௮௯", "0123456789"), ("173/௧", "173/1")])
def test_tamil_numerals(raw, out):
    assert tamil_numerals_to_ascii(raw) == out


@pytest.mark.parametrize("raw,iso,prec", [
    ("26.02.2021", "2021-02-26", "day"), ("02/12/2021", "2021-12-02", "day"), ("31.3.2021", "2021-03-31", "day"),
    ("21.03.2025", "2025-03-21", "day"),
])
def test_dates(raw, iso, prec):
    assert parse_date(raw) == (iso, prec)


@pytest.mark.parametrize("raw", ["12.05.2019", "01.01.2031"])
def test_dates_out_of_range(raw):
    assert parse_date(raw)[0] is None


@pytest.mark.parametrize("raw,expected", [
    ("173/1", [("173", "1")]), ("177/4A", [("177", "4A")]), ("179/1A", [("179", "1A")]),
    ("18/3G", [("18", "3G")]), ("224/2", [("224", "2")]), ("173", [("173", None)]),
    ("172/1 மற்றும் 172/2", [("172", "1"), ("172", "2")]),
])
def test_survey_refs(raw, expected):
    assert [(r.survey_no, r.sub_div) for r in parse_survey_ref(raw)] == expected


def test_survey_list_of_subdivisions():
    # §6 "6,7,8,10A" — a list of subdivisions under one survey (LDR g18: survey 1)
    assert canon_subdiv("6,7,8,10A") == "6,7,8,10A"
    assert canon_subdiv(" 4a ") == "4A"


def test_merged_survey_extent():
    assert split_merged_survey_extent("20.86.00") == ("2", 0.86)


@pytest.mark.parametrize("raw,canon", [
    ("அல்லிகுளம்", "Allikulam"), ("Allikulam", "Allikulam"),
    ("Keel Thattaparai", "Keelathattaparai"), ("கீழத்தட்டப்பாறை", "Keelathattaparai"),
    ("Mela Thattaparai", "Melathattaparai"), ("Melathataparai", "Melathattaparai"),
    ("மேலத்தட்டப்பாறை", "Melathattaparai"), ("உமரிக்கோட்டை", "Umarikottai"),
    ("Perurani", "Peroorani"), ("பேரூரணி", "Peroorani"), ("இராமசாமிபுரம்", "Ramasamypuram"),
    ("தெற்கு சிலுக்கன்பட்டி", "South Silukanpatti"), ("Thoothukudi", None),
])
def test_village_aliases(raw, canon):
    assert normalise_village(raw) == canon


@pytest.mark.parametrize("raw,ha", [("053.50", 0.535), ("019.50", 0.195)])
def test_extent_ha_dot_dropped(raw, ha):
    # VLM drops the first dot of H.AA.SS ("0.53.50" → "053.50"): never 53.5 ha
    assert parse_extent_ha(raw) == pytest.approx(ha)
