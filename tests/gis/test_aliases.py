"""Village/district alias normalisation and parcel_uid construction (D-020)."""
import pytest

from pipeline.gis.aliases import (
    build_table,
    canonical_village,
    fold,
    load_aliases,
    normalise_kide,
    parcel_uid,
    split_kide,
)


@pytest.mark.parametrize("raw,canon", [
    ("Allikulam", "Allikulam"),
    ("ALLIKULAM ", "Allikulam"),
    ("அல்லிக்குளம்", "Allikulam"),
    ("Keel Thattaparai", "Keelathattaparai"),
    ("keel-thattaparai", "Keelathattaparai"),
    ("கீழதட்டப்பாறை", "Keelathattaparai"),
    ("Melathataparai", "Melathattaparai"),
    ("Mela Thattaparai village", "Melathattaparai"),
    ("Perurani", "Peroorani"),
    ("பேரூரணி", "Peroorani"),
    ("இராமசாமிபுரம் கிராமம்", "Ramasamypuram"),
    ("south  silukanpatti", "South Silukanpatti"),
    ("தெற்கு சிலுக்கன்பட்டி", "South Silukanpatti"),
    ("Umari\u200dkottai", "Umarikottai"),       # zero-width joiner from OCR/copy-paste
])
def test_canonical_village(raw, canon):
    assert canonical_village(raw) == canon


@pytest.mark.parametrize("raw", ["Tirunelveli", "", "Silukanpatti North", None])
def test_unknown_village_is_none(raw):
    assert canonical_village(raw) is None


def test_district_variants():
    t = load_aliases()
    assert t.canonical_district("Thoothukkudi") == "Thoothukudi"
    assert t.canonical_district("thoothukudi") == "Thoothukudi"
    assert t.canonical_district("Chennai") is None


def test_all_seven_villages_configured():
    names = {v.name for v in load_aliases().villages}
    assert names == {"Allikulam", "Keelathattaparai", "Melathattaparai", "Umarikottai", "Peroorani",
                     "Ramasamypuram", "South Silukanpatti"}


def test_conflicting_alias_rejected():
    with pytest.raises(ValueError):
        build_table({"villages": {"A": {"aliases": ["X"]}, "B": {"aliases": ["x "]}}})


def test_fold_is_case_space_punct_insensitive():
    assert fold("Keel. Thatta-parai") == fold("keelthattaparai")


@pytest.mark.parametrize("kide,exp", [("177/4a ", "177/4A"), (" 168", "168"), (168, "168"), ("16/3-4-5", "16/3-4-5")])
def test_normalise_kide(kide, exp):
    assert normalise_kide(kide) == exp


def test_split_kide():
    assert split_kide("177/4A") == ("177", "4A")
    assert split_kide("168") == ("168", None)
    assert split_kide("16/3-4-5") == ("16", "3-4-5")


def test_parcel_uid_uses_canonical_village_and_kide():
    assert parcel_uid("Keel Thattaparai", "177/4a") == "Keelathattaparai|177/4A"
    # KIDE '168' repeats across villages -> distinct uids (D-020)
    assert parcel_uid("Umarikottai", "168") != parcel_uid("Melathattaparai", "168")


def test_parcel_uid_unknown_village_raises():
    with pytest.raises(ValueError):
        parcel_uid("Tirunelveli", "1/1")


def test_empty_kide_raises():
    with pytest.raises(ValueError):
        normalise_kide("  ")
