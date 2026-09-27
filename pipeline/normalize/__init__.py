"""P2 normalisers: pure, total functions (bad input → None, never raise).

Promoted from spikes/ocr_bakeoff/normalize.py (D-010) and extended per land-domain-knowledge
§5-§8 and §11b. Table-driven tests live in tests/normalize/.
"""
from .classification import normalise_classification
from .dates import find_dates, parse_date, parse_date_iso
from .names import OwnerName, latin_key, normalise_name, owner_display, split_owners, tamil_to_iso
from .numbers import (
    HA_TO_AC,
    HA_TO_AC_DOC,
    ac_to_ha,
    cents_to_ac,
    cents_to_ha,
    compensation_matches,
    extents_agree,
    ha_to_ac,
    parse_amount,
    parse_cents,
    parse_extent_ac,
    parse_extent_ha,
    parse_number_words,
    tamil_numerals_to_ascii,
)
from .survey import (
    SurveyRef,
    canon_subdiv,
    kide_of,
    parcel_uid_of,
    parse_survey_pair,
    parse_survey_ref,
    split_merged_survey_extent,
    survey_key,
)
from .text import cer
from .village import find_village_in_text, normalise_village

parse_money_inr = parse_amount   # bake-off name

__all__ = [
    "HA_TO_AC", "HA_TO_AC_DOC", "OwnerName", "SurveyRef", "ac_to_ha", "canon_subdiv", "cents_to_ac",
    "cents_to_ha", "cer", "compensation_matches", "extents_agree", "find_dates",
    "find_village_in_text", "ha_to_ac", "kide_of", "latin_key", "normalise_classification",
    "normalise_name", "normalise_village", "owner_display", "parcel_uid_of", "parse_amount",
    "parse_cents", "parse_date", "parse_date_iso", "parse_extent_ac", "parse_extent_ha",
    "parse_money_inr", "parse_number_words", "parse_survey_pair", "parse_survey_ref",
    "split_merged_survey_extent", "split_owners", "survey_key", "tamil_numerals_to_ascii",
    "tamil_to_iso",
]
