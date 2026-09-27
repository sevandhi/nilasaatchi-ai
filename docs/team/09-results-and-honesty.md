# 09 · Results, Metrics and Honesty Log

All numbers are measured. The source is `docs/metrics.md` (each with the command that produced it); the final table is its "Phase 7 final evaluation" section.

## 1. Key results
| Area | Result |
|---|---|
| Dataset | 3,212 PDFs → **2,362 unique documents, 12,859 pages**; 7 villages; 1,242 FMB parcels |
| GIS | Geodesic FMB union 901.50 ha; 366 overlapping parcel pairs; 28 parcels outside their survey |
| Document drift | Proposal 911.395 ha vs sanction 904.40 ha (Keelathattaparai +2.33, Ramasamypuram +4.665) |
| Classifier | v1 rules 45% → **v2 LLM 90%** stage accuracy (type 80%, scheme 96%; 100 labelled documents) |
| OCR bake-off | Bedrock whole-page 63/83/59% (numbers/surveys/owners) vs Groq 26/38/16% vs Tesseract 0% owners |
| Extraction, **fresh never-seen set** | Survey **89.5%**, owner **83.3%**, extent 79.2%, acres 100%, land class 93.8%, doc type 100%, headers 91.1% |
| Bulk extraction | 5,796 pages → 26,269 rows, 42,380 facts, 15,342 acquisition events (≈ US$5.6) |
| Matching | 82.1% of matchable facts linked to parcels; 87% of parcels have document facts; spot check 50/50 |
| Satellite | **588 scenes, 382,536 parcel observations (2019 → 2026-09-26)**; 40,986 parcel-seasons; land-use student accuracy 0.936 (macro-F1 0.634) |
| Control-group DiD | 143 parcels with a significant post-possession drop vs controls; demo parcel Melathattaparai 233: ploughing-like signal +2.15 (CI 0.85–3.70) |
| **Findings** | **1,388** in 8 categories, each with an evidence pack (chapter 13) |
| **Agent** | 8/8 answers match SQL references (4 demo + 4 unseen incl. Tamil); plan validity 100%; tool success 88%; verified-claim rate 78.5%; the critic is always another vendor |
| **New documents** | 20/20 unseen uploads completed, **387 parcel links, 806 facts**; the AI type guess was right on 11/20 (hence the declared type) |
| **Privacy** | **0 of 14,202** owner-data (PII) model calls went to a model that trains on inputs |
| Safety | The SQL guard refuses DROP / DELETE / stacked statements / owner reads; the agent's DB role can't read owners |
| Tests | **928** backend tests, 13 UI e2e (+1 skipped when no second AI quota) |
| Cloud | Read-only demo on Lambda + API Gateway; Bedrock from Lambda verified (167 ms) |
| AWS spend | Router estimate ≈ US$8, Cost Explorer US$0.79 (lags), of the US$100 event budget; everything else on free tiers |

## 2. Honesty log: corrections we made ourselves
Reviewers respect this. Each correction came from checking our own work:

| # | Original claim | What we found | Correction |
|---|---|---|---|
| 1 | "The AS letter says 911.395 ha" | 911.395 is the Tamil *proposal*; the English AS letter says 904.40 | Reframed as proposal-vs-sanction drift (D-014) |
| 2 | "The parcel map exceeds the sanction by 4.11 ha" | The FMB parcels overlap (366 pairs); the union is 901.50 ha | Claim withdrawn; FMB-quality finding added (D-023) |
| 3 | "170 clear satellite scenes" | Reprocessed duplicates were double-counted | 587 unique, 122 clear (D-015) |
| 4 | "532 parcels still cultivated after possession" | Mixed baselines; post-monsoon green-up is near-universal | **Redesigned as a control-group DiD** (D-035) |
| 5 | "Golden extraction ≈ 100%" | Over-fitted; held-out showed 74/71/39% | Held-out, dev sets and repair loops (D-034) |
| 6 | "Cohere Vision can't see images" | Our token accounting hid image tokens | Fixed the adapter; doctor now proves vision (D-036b) |
| 7 | "Bedrock 8B can't see images" | The 64-px probe was too small | 256-px probe (D-037) |
| 8 | Held-out stayed blind | We inspected held-out outputs to find a bug | Disclosed; a fresh held-out set (heldout2) for final scoring (D-043) |
| 9 | "Re-uploading a document gives 100% the same data" | That run read from the cache; with fresh reads and AWS logged out it gave 0% | Reported both; the extraction step now shows "failed" when nothing was read (D-064) |
| 10 | "Every check passed, so the rows are right" | A fresh read put the row serial numbers (1, 2, 3…) into the survey column and passed every check | New `survey_not_serial` check → second read / review (D-065) |
| 11 | "The AI classifies uploaded documents" | Without folder names it was wrong on 4/7, then 9/20 | Uploader can declare the type; the AI guess stays visible (D-065, D-066) |
| 12 | An owner-name leak (`owner_vlm_raw`) in stored rows | Raw owner text survived in 4,153 rows | Scrubbed and loader patched (D-054) |

## 3. Known limitations (say them before reviewers do)
- **Extraction below targets on fresh pages:** survey 89.5, extent 79.2, owner 83.3, headers 91.1% (targets 92/90/85/95); uncertain rows go to review (D-055).
- **Handwriting and blurred newsprint** go to human review; handwritten chitta pages often give no survey numbers, so no parcel link.
- **No field ground truth for satellite:** outputs are *signals for field verification*. The land-use model's macro-F1 is 0.634 (target 0.80).
- **Crop vs weed:** greenness alone can't separate them, hence the control group. Ploughing at 10 m is rarely detectable.
- **Agent speed:** ~50 s median per question (target 25 s); free-tier daily quotas can run out.
- **Cloud demo is read-only:** uploads and the live agent need the local app (no RDS/EC2/ECR allowed).
- **Not measured:** the findings-precision audit and the routing-policy comparison.
