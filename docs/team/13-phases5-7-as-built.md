# 13 · Phases 5–7: Findings, UI, New Data, Cloud and Packaging

This chapter covers everything built after chapter 08. The as-built diagrams are in `docs/architecture.md`.

## 1. Phase 5: the findings engine (✅) · owners: Shivani, Sevandhi
The findings engine turns the knowledge graph into **1,388 findings**, each with an **evidence pack** (`make findings`, `make evidence-pack ID=…`).

| Category | Count (severity) | What it means |
|---|---|---|
| COMPENSATION_MISMATCH | 127 (43 high) | The awarded amount ≠ acres × the notified rate |
| EXTENT_MISMATCH | 485 (21 high) | The document extent differs from the FMB map area |
| DOC_VERSION_CONFLICT | 2 (high) | The proposal says 195.48 ha, the sanction 193.15 ha (Keelathattaparai) |
| FMB_QUALITY | 270 (5 high) | Overlapping or sliver polygons in the survey map itself |
| PV1_CLASSIFICATION_CONFLICT | 13 (low) | A "dry" land class, but the satellite shows irrigation-like seasons |
| PV3_POST_POSSESSION_ACTIVITY | 380 (117 medium, 263 low) | After possession the parcel behaves differently from never-acquired farmland (a vigour drop), or is still farmland-like |
| PV4_IDLE_LAND_BANK | 79 blocks, 834.3 ha | Possessed land with no clearing or construction |
| EXTRACTION_ERROR | 32 (low) | Implausible extracted values, flagged for a human |

**Evidence pack** = the paper side (document page + extracted fields) + the planet side (satellite numbers + chips) + the checks run + verdict, confidence and caveats. Findings are **signals for verification, not legal conclusions**, and block-level evidence (payments without survey numbers) is never presented as parcel-level.

## 2. Phase 6: the workspace UI (✅) · owner: Preethi
React 19 + Vite in JavaScript (JSX), MapLibre (free OpenFreeMap basemap), ECharts, TanStack Table, Zustand, Tailwind. There are **8 pages**:

| Page | What it shows |
|---|---|
| Overview | What the system is, the pipeline diagram, live counts (with the SQL behind each), satellite data freshness |
| Map workspace | A full-page map: 1,242 parcels, colour by stage / findings / land use per season (slider 2019→2026), 11 layers incl. control-group cells |
| Parcel | **The key screen:** the Paper vs Planet timeline (NDVI 2019→2026 with document events on top), linked extractions with the value boxed on the scanned page, satellite chips, the DiD comparison, findings |
| Findings | Filters, a summary chart, idle land bank, evidence pack with JSON export |
| Agent console | A live plan, each step's model and "why?", verifier checks, critic challenges, the judge, the answer, the ledger with *Verify* |
| Documents | Catalogue, page viewer, **upload a document** |
| Models & routing | Every model's role, privacy flag and health; task chains; usage and cost; privacy audit |
| Review queue | Uncertain extractions with accept / edit / reject |

**User rule (D-062): the UI shows what the product does, not how we built it.** There are no phase tables, no dev commands and no decision numbers in the UI. Demo mask is ON by default (owner names hidden).

## 3. New data: not only the given dataset (✅) · owners: Mohana (documents), Sevandhi (satellite)
### Upload a document (Documents page)
Upload a PDF → a background job of 7 steps, each shown live:
1. save
2. catalogue
3. classify the document type
4. read tables
5. load
6. link to parcels
7. run checks

Uncertain rows go to the Review queue, and an exact duplicate (same MD5) is recognised and not processed twice.

- **Declared document type:** the AI's own type guess is weak without folder names (right on 11/20 in our test: all LDRs right, all awards wrong). So the uploader can pick the type, and the report still shows "AI suggested X" (honest).
- **Downloadable report:** a *Document verification report* (PDF) plus the rows (CSV). It contains:
  1. a plain summary
  2. the steps
  3. the linked parcels with their legal stage
  4. findings with a plain explanation of each category
  5. every row read

  Owner names are never included. The guide is `docs/ui/04-report-guide.md`.
- **Measured:** 20 unseen documents (rescans of real awards, LDRs, a notice, Form F) all completed with parcels linked: **387 parcel links, 806 facts**. Files and results are in `data/demo_uploads/working/`.
- **Two bugs found by fresh reads and fixed:**
  - a read that took the row serial column (1, 2, 3…) as survey numbers passed every check, so a new `survey_not_serial` check sends it to a second read or review;
  - a table-reader outage was shown as "done" and now shows as failed.

### Satellite refresh (Overview → "Check for new satellite images")
Fetches Sentinel-2 scenes newer than the latest one. It updates only the affected parcels × seasons, re-predicts with the existing model (no retraining, no model calls), and refreshes the control-group comparison and findings. It is resumable, and re-running it is a no-op. The first real run added the **2026-09-26** scene (588 scenes, 382,536 parcel observations).

## 4. Phase 7: validation, cloud demo, packaging (✅) · owners: Shivani (gates), Preethi (demo)
### Read-only cloud demo on AWS
**https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/** (API Gateway → Lambda → private S3, Mumbai).

- **What's in the cloud:** a snapshot of every read-only API answer (28,128 files, owner-leak scan passed) plus page images is stored in S3. The Lambda runs a small FastAPI app that serves the API *and* the React build: lists are filtered with DuckDB over Parquet, and page images are served via short-lived presigned links (the bucket stays private).
- **What stays local:** uploads, the live agent and review actions return "read-only" in the cloud. They need the PostGIS database and the Docker OCR, and the event rules allow no RDS, EC2 or container registry.
- **Bedrock from Lambda:** FarmwiseAI enabled it on 27 Sep; our `/health/bedrock` check confirmed it (Ministral 3B, 167 ms).
- **Throttling:** the account allows ~10 Lambdas at once, so the UI automatically retries throttled requests and images.
- **Cost:** a few cents. Teardown is `make cloud-down`; every resource is logged in `infra/RESOURCES.md`.

### Packaging and running anywhere
- `make package` → `dist/nilasaatchi-demo.zip` (2.3 GB: code + 49 MB database dump + data).
- **Linux/macOS:** `bash scripts/demo_setup.sh` once, then `bash scripts/demo_run.sh` (or `make demo`).
- **Windows (no WSL):** double-click `SETUP-WINDOWS.cmd` once, then `START-WINDOWS.cmd`. See `WINDOWS.md`.
- The dump restore was verified table by table in a scratch database.

### Code on GitHub
https://github.com/sevandhi/nilasaatchi-ai (public). The dataset, derived data, keys and real owner names are **not** in the repository. We checked before pushing: key values searched, the held-out labels with real names git-ignored, and test names replaced with synthetic ones.

### Final evaluation
The full table is in `docs/metrics.md` ("Phase 7 final evaluation") and summarised in chapter 09.
