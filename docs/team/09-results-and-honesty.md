# 09 · Results, Metrics and Honesty Log

All numbers are measured; the source is `docs/metrics.md` (with the command that produced each).

## 1. Key results
| Area | Result |
|---|---|
| Dataset | 3,212 PDFs → 2,361 unique; 12,564 unique pages; 7 villages; 1,242 FMB parcels |
| GIS | Geodesic FMB union 901.50 ha; 366 overlapping parcel pairs; 28 parcels outside their survey |
| Document drift | Proposal 911.395 ha vs sanction 904.40 ha (Keelathattaparai +2.33, Ramasamypuram +4.665) |
| Classifier | v1 rules 45% → **v2 LLM 90%** stage accuracy (100 labelled documents) |
| OCR bake-off | Bedrock whole-page 63/83/59% (numbers/surveys/owners) vs Groq 26/38/16% vs Tesseract 0% owners |
| Extraction, **fresh never-seen set** (legible) | Survey **89.5%**, owner **83.3%**, extent 79.2%, acres 100%, land class 93.8%, doc type 100%, headers 91.1% (earlier partly-seen set: 96.3 / 90.9 / 72.4%) |
| Satellite | 381,294 parcel observations; 40,986 parcel-seasons; land-use student accuracy 0.936 (macro-F1 0.634) |
| Control-group DiD | 143 parcels with a significant post-possession drop vs controls; 1,143 still farmland-like; demo parcel 233 has a significant ploughing-like signal |
| Stage B (bulk extraction) | 5,796 pages → 26,269 rows, 42,380 facts, 15,342 events; ≈ US$5.6 |
| Matching | 82% of matchable facts linked to parcels; 87% of parcels have document facts; spot check 50/50 |
| Tests | ~770 automated tests passing |
| AWS spend | ≈ US$7.9 of a US$15 cap (router ledger; mostly Stage B + classifier); all other models on free tiers |

## 2. Honesty log: corrections we made ourselves
Reviewers respect this. Each correction came from checking our own work:

| # | Original claim | What we found | Correction |
|---|---|---|---|
| 1 | "The AS letter says 911.395 ha" | 911.395 is the Tamil *proposal*; the English AS letter says 904.40 | Reframed as proposal-vs-sanction drift (D-014) |
| 2 | "The parcel map exceeds the sanction by 4.11 ha" | The FMB parcels overlap (366 pairs); the union is 901.50 ha | Claim withdrawn; FMB-quality finding added (D-023) |
| 3 | "170 clear satellite scenes" | Reprocessed duplicates were double-counted | 587 unique, 122 clear (D-015) |
| 4 | "532 parcels still cultivated after possession" | Mixed processing baselines; then we found post-monsoon green-up is near-universal | Restated as 444, then **redesigned as a control-group DiD** (D-035) |
| 5 | "Golden extraction ≈ 100%" | Over-fitted; held-out showed 74/71/39% | Held-out, dev sets and repair loops → 96/91% on survey/owner (D-034) |
| 6 | "Cohere Vision can't see images" | Our token accounting hid image tokens | Fixed the adapter; doctor now proves vision (D-036b) |
| 7 | "Bedrock 8B can't see images" | The 64-px probe was too small | 256-px probe (D-037) |
| 8 | Held-out stayed blind | We inspected held-out outputs to find a bug | Disclosed; a fresh held-out set for final scoring (D-043) |

## 3. Known limitations (say them before reviewers do)
- **Extraction slightly below targets on fresh pages:** survey 89.5, extent 79.2, owner 83.3, headers 91.1% (targets 92/90/85/95); accepted and documented (D-055).
- **Handwriting and blurred newsprint:** these go to human review.
- **No field ground truth for satellite:** satellite outputs are *signals for field verification*.
- **Crop vs weed:** greenness alone can't separate them, hence the control group. Ploughing at 10 m is rarely detectable (16 of 1,144).
- **AWS sessions last 2 h:** bulk Bedrock jobs run in chunks until FarmwiseAI grants the Lambda role Bedrock access.
- **Not yet built:** the P5 findings engine, the P6 UI, the P7 deployment/demo.
