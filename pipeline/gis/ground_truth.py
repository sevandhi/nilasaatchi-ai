"""Verifier anchors from land-domain-knowledge §9 (corrected 2026-09-26, D-014).

These are reference totals that kg_check reconciles against; they are not answers served to
users. Hectare triples are converted: 186.45.5 -> 186.455 ha.
"""
from __future__ import annotations

# GO (Ms) No.100 (26.02.2021) == English AS letter Lr.No.LA/TUT/Allikulam/2020 (LPS.pdf p1).
SANCTIONED_HA: dict[str, float] = {
    "Allikulam": 186.455,
    "Keelathattaparai": 193.150,
    "Melathattaparai": 175.835,
    "Umarikottai": 106.010,        # GO prints 106.01.01 (probable typo)
    "Peroorani": 141.060,
    "Ramasamypuram": 95.230,
    "South Silukanpatti": 6.660,
}
SANCTIONED_TOTAL_HA = 904.40

# Tamil proposal table, AS.pdf p2 (not sanctioned).
PROPOSAL_HA: dict[str, float] = {
    "Allikulam": 186.455,
    "Keelathattaparai": 195.480,
    "Melathattaparai": 175.835,
    "Umarikottai": 106.010,
    "Peroorani": 141.060,
    "Ramasamypuram": 99.895,
    "South Silukanpatti": 6.660,
}
PROPOSAL_TOTAL_HA = 911.395

# Computed-layer anchors: skill §9 geodesic sums (D-023).
FMB_EXPECTED_HA: dict[str, float] = {
    "Allikulam": 186.77,
    "Keelathattaparai": 196.08,
    "Melathattaparai": 174.26,
    "Umarikottai": 105.56,
    "Peroorani": 141.92,
    "Ramasamypuram": 97.11,
    "South Silukanpatti": 6.82,
}
FMB_EXPECTED_TOTAL_HA = 908.51
# Dissolved union of all FMB parcels, geodesic (D-023, verified by the lead): the primary total.
FMB_UNION_EXPECTED_TOTAL_HA = 901.50

CADASTRAL_EXPECTED_HA: dict[str, float] = {
    "Allikulam": 188.92,
    "Keelathattaparai": 204.00,
    "Melathattaparai": 177.11,
    "Umarikottai": 107.49,
    "Peroorani": 142.68,
    "Ramasamypuram": 97.70,
    "South Silukanpatti": 17.40,
}
CADASTRAL_EXPECTED_TOTAL_HA = 935.30

PARK_BOUNDARY_EXPECTED_HA = 934.32

# Feature counts per layer (skill §10).
EXPECTED_COUNTS: dict[str, int] = {
    "parcel": 1242,
    "survey": 396,
    "ref_layer_park_boundary": 1,
    "ref_layer_sipcot_parks": 18,
    "ref_layer_roads": 37590,
    "ref_layer_rail": 511,
    "ref_layer_rail_stations": 37,
    "ref_layer_waterbodies": 185,
    "ref_layer_substations": 36,
    "ref_layer_airport": 1,
    "ref_layer_seaport": 2,
    "ref_layer_schools": 63,
}

FMB_TOLERANCE = 0.005   # 0.5 % (phase1 skill T1.4)
