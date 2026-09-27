---
name: phase5-verification-analytics
description: Phase 5 (Day 7–11) — the "Proof" pillar. Document-to-parcel matcher with candidates, lifecycle state machine, Paper-vs-Planet checks PV1–PV6 plus document-side discrepancy rules, evidence-pack assembly, document drift (GO/AS/LPS/FMB), idle-land-bank and light multi-layer suitability, the agri_claims domain pack, and the 25-query unseen evaluation suite. Load for matching, findings, evidence packs, domain packs or scenario/eval work.
---

# Phase 5 — Proof: Verification & Analytics

Also load `land-domain-knowledge`.

**Owners:**
- gis-engineer: T5.1–T5.4, T5.6
- eo-engineer: planet inputs to T5.3
- agent-architect: T5.5, T5.7 and prompt tuning
- qa-evaluator: T5.8 and the findings audit

## T5.1 Matcher (`pipeline/match/`, tool `match_parcels`)
- Match within one village only, in this order:
  - (1) exact `KIDE`
  - (2) normalised survey + subdivision
  - (3) parent survey, expanded and marked `survey_level`
  - (4) OCR-confusion variants (3↔8, 1↔7, 0↔6, 5↔6, A↔4), with a penalty
  - (5) block agreement as a bonus
- Score = identifier + extent agreement + block + header-village confidence. Keep the top 3 candidates with reasons.
- Accept when score ≥ τ_match and the margin ≥ δ. Otherwise mark `ambiguous`, which goes to review.

## T5.2 Lifecycle (`pipeline/lifecycle/`, tool `lifecycle_status`)
- Per parcel: ordered events, current stage, days in stage, next expected stage, blockers and completeness.
- Aggregates: a village × stage matrix, and the funnel from 3(1) to mutation.

## T5.3 Findings engine (`pipeline/discrepancy/`, tool `paper_vs_planet`)
Each rule is a pure function producing `Finding{category, kide, paper_evidence_ids[], planet_evidence{obs_ids, chip_refs, features}, verdict: null, confidence, caveats[]}`. Thresholds live in `config/thresholds.yaml`.

**Paper vs Planet**

| Code | Rule (defaults) |
|---|---|
| PV1 CLASSIFICATION_CONFLICT | Wet (நன்செய்) with no `irrigated_multi` or dry-season green in ≥ 2 of the 3 years before 3(1). Dry (புன்செய்) with `irrigated_multi` in ≥ 2 years. Poramboke with `cropped` in ≥ 2 seasons (encroachment signal) |
| PV2 STANDING_ASSET_CONFLICT | Trees or crops compensated, but `perennial_veg` < 20% of observations in the 12 months before the award. Or the award says "excluding trees" / no tree line, while `perennial_veg` ≥ 60% (possible under-compensation, which protects the farmer) |
| PV3 POST_POSSESSION_ACTIVITY | **D-035:** do NOT use the absolute state (rabi green-up is near-universal). Use `planet` control-group metrics: after T_possession + 60 days, the parcel's seasonal behaviour stays within the control distribution of active farmland (DiD of amplitude/integral vs controls ≈ 0, ploughing signature present) while comparable acquired parcels diverge. Report effect size + confidence, with caveats |
| PV4 IDLE_LAND_BANK | T_possession ≥ 6 months ago and no `cleared_or_built` since. Aggregate ha by block with road and substation distances |
| PV5 PRE_NOTIFICATION_CHANGE | A state transition in `pending` versus `pre_notification`: bare→perennial, bare→built, or a sudden multi-cropping onset |
| PV6 WATER_CONFLICT | Dry classification with NDWI/JRC water occurrence > 25% or a tank overlap > 10% |

**Document side:** MISSING_IN_GIS, MISSING_IN_DOCS, EXTENT_MISMATCH (> 5%), OWNER_MISMATCH, STAGE_ORDER_VIOLATION, STALLED, EXEMPTION_CONFLICT, DOC_VERSION_CONFLICT, COMPENSATION_MISMATCH, OUT_OF_SCOPE_DOC (definitions as in v1: `plan.md` §5.4).

- **Severity** is based on the size of the impact (ha, ₹), the confidence and the stage.
- Every rule has synthetic unit tests plus a real-data test.
- **Caveat templates** are mandatory: clouds/gaps, mixed pixels, weed flush, classification wording.

## T5.4 Document drift
- A village-total comparison across GO, AS, LPS, FMB and cadastral, with sources and the AS-text explanation.
- **Must reproduce:** proposal (Tamil, AS.pdf p2) vs sanction (GO/English AS letter): Keelathattaparai 195.48.00 vs 193.15.0, Ramasamypuram 99.89.50 vs 95.23.0, totals 911.395 vs 904.40; and GIS vs sanction: FMB 908.51 vs 904.40 (+4.11 ha). Label each source correctly (proposal ≠ sanction).

## T5.5 Evidence packs (tool `evidence_pack`; schema `schemas/evidence/EvidencePack.json`)
- Contents:
  - parcel inset
  - paper claims with page crops and bboxes (original Tamil, plus a gloss)
  - the timeline figure: document events over the smoothed NDVI/BSI with valid observations as dots
  - chips at the pre, event and post dates
  - checks run, the critic's challenge and the test result
  - verdict, confidence, caveats, ledger hash
- Rendered to the UI panel, and to PDF via the backend.
- Generate packs for the top 20 findings. The qa-evaluator audits 40 findings from the evidence alone (precision ≥ 80%).

## T5.6 Idle land bank and light suitability
- `v_idle_land_bank` feeds PV4. Also build a `suitability_rank` template: area, road and substation distance, water/slope/flood exclusion, with weights exposed. This satisfies the Task 2 multi-layer query at low cost.

## T5.7 Domain pack `agri_claims` (the reuse proof)
- Tools: `crop_presence`, `fallow_streak`, `satellite_timeseries`, `satellite_chip`, `spatial_query`, `compose_workspace`.
- Its prompts and glossary cover crop seasons and claims. There is **no acquisition vocabulary**, and **no graph change**.
- Demo: upload a GeoJSON of plots → "crop presence per season 2023–25 and fallow streaks ≥ 2 seasons". This mirrors FarmwiseAI's crop-insurance claim verification, loan-utilisation and Digital Crop Survey cross-checks.

## T5.8 Unseen suite (`eval/queries.yaml`, qa-evaluator)
- 25 queries:
  - 5 Task 2 comparables
  - 6 paper-vs-planet queries (PV1–PV5 variants on different villages and blocks)
  - 4 lifecycle and drift queries
  - 4 agri-claims queries on new polygons
  - 3 Tamil queries
  - 3 needs-clarification or cannot-answer queries
- Reference answers are computed by SQL or scripts in the harness, never stored in `app/`.
- Rubric per query (0–4): data, views, verification, trace.

## Exit gate
```
make eval-match && make eval-findings && make eval-queries && make test
```
The report covers:
- the match P/R table
- findings counts by category and village, each with 1 evidence pack
- audit precision
- a drift table screenshot
- the agri-claims demo output
- unseen-suite scores
