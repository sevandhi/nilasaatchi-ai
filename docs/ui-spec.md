# NilaSaatchi UI Spec (P6): the "glass box" workspace

**Goal (user, 27 Sep):** reviewers must be able to see and understand *everything we built* from the UI itself: documents, extraction, parcels, satellite evidence, findings, the agent's reasoning, models and routing, cost, evaluation and limitations. Nothing implemented stays hidden.

**Tone: honest, not overrated.**
- Show measured numbers with their source.
- Show confidence and caveats wherever a result is shown.
- Show the limitations page.
- No marketing superlatives, no fake data, no invented metrics.
- Owner names are masked by default ("Owner ••17"), and bank data never appears.

**Stack (D-011):** React 19 + Vite, **JavaScript (JSX)**, MapLibre GL (free OSM/OpenFreeMap basemap), ECharts, TanStack Table, Zustand, Tailwind. The API is FastAPI on :8000 (`make api`); zod schemas come from `make gen-schemas`. The app lives in `web/`; `make web` runs the dev server.

**Layout:** a left navigation rail with 9 pages; a top bar with the global search/command box, a "Demo mask" toggle (ON by default) and an EN/தமிழ் label toggle; a status footer with the API/DB state, AWS spend vs cap, and the last data refresh.

---

## Pages
### 1. Overview ("What this system is")
- A one-paragraph plain explanation of Paper vs Planet, plus a pipeline diagram (Sources → Paper → Planet → Knowledge graph → Agent → Findings). Each box is clickable and leads to its page.
- KPI tiles, each with its source query:
  - documents 2,362 / pages 12,859;
  - classified types;
  - extraction rows 26,269 / facts 42,380 / events 15,342;
  - parcels 1,242 (with facts 1,082);
  - satellite observations 381,294 / parcel-seasons 40,986;
  - findings 1,388 by category;
  - review queue 1,302;
  - AWS spend vs cap.
- The phase status (from docs/progress.md) with honest percentages.

### 2. Map workspace
- **Base:** FMB parcels (1,242) and the park boundary. Toggle layers: roads (major/other), waterbodies, substations, rail, schools, SIPCOT parks, FMB overlaps, parcels outside their survey, control-group cells (2–6 km ring).
- **Colour-by selector:** acquisition stage (lifecycle), finding category/severity, land-use state for a chosen season (**season slider** 2019→2026 with play), DiD vs controls, match status.
- Legend always visible. Hover card: parcel_uid, village, area (geodesic), stage, number of findings.
- **Click a parcel → the Parcel page.**

### 3. Parcel page (the signature view)
- **Header:** parcel_uid, village/unit/block, geodesic area vs document extent, stage, evidence level.
- **Paper vs Planet timeline:** smoothed NDVI (plus BSI on a toggle) 2019→now with observation dots. Document events are vertical markers (3(1), award, payment, possession, mutation); season bands are coloured by land-use state; findings appear as flags. Clicking a marker opens the document page; clicking a dot opens that date's chip.
- **Documents:** every linked extraction row. It shows the page image with the **bounding box** drawn, the extracted fields, confidence, read status (vlm_single/vlm_agreed), self-consistency checks, and the review status.
- **Satellite:** chips (true colour/NDVI) at key dates; season states with model confidence and route (student / VLM second opinion); **DiD vs control group** chart (parcel vs control distribution before/after each event, with the CI) and its plain-language meaning and caveats.
- **Findings:** each with category, severity, confidence, caveats, and an "Open evidence pack" button.

### 4. Findings
- A table of 1,388 findings, filterable by category, severity, village, block and evidence level (parcel/block). A summary chart by category × severity. Idle land bank by block (ha, road/substation distance).
- **Evidence pack view:** paper side (document crop + fields), planet side (DiD numbers + chips), checks run, verdict, confidence, caveats. Exportable as JSON.

### 5. Agent console (make the backend visible)
- A command box with example queries (from `/examples`, not hard-coded), EN/Tamil.
- **Live run view (SSE):**
  - the plan (steps and dependencies) as it is produced;
  - each step: tool or model, **why this model** (router candidates, scores, filtered reasons), privacy tier, latency, tokens, shadow cost, actual cost, and a fallback marker;
  - tool outputs: the SQL text for `sql_query`, the result table and map highlight;
  - verifier checks (pass/fail), each **critic challenge** (its vendor, the hypothesis, the test run, the outcome) and the **judge verdict** per claim;
  - the presenter's answer + WorkspaceSpec rendered (map layers + table + KPIs).
- **Ledger panel:** the hash chain for this run, and a "Verify" button (calls the API).
- **Run history:** past runs with status, duration and cost; reopen any run.
- **Chaos toggle** (e.g. "Gemini down") to demonstrate fallback live.

### 6. Documents
- A catalog table (type, stage, scheme relevance, village/unit/block, date, pages, folder vs classified type with a mismatch badge).
- A classifier panel: v1 45% → v2 90% stage accuracy; the confusion summary.
- A page viewer with extraction overlays; filter by doc type/stage. Duplicates are shown as a group.

### 7. Models & routing
- The model registry: role, vendor, open vs proprietary, modalities, privacy flag (trains on free tier?), limits, doctor status (latest probe latency, vision proof).
- The task → chain table (plan, critic, judge, present, sql, table_read_pii, satellite…).
- Usage from router_log: calls per model/task, success/fallback rates, latency p50/p95, shadow vs actual cost, and the PII-tier audit (0 PII to training-tier models).
- AWS: spend vs the $15 cap; the Lambda batch status (built, awaiting permission).

### 8. Evaluation & honesty
- **Extraction:** the fresh held-out set (heldout2) numbers vs targets (survey 89.5 / extent 79.2 / owner 83.3 / headers 91.1), plus golden/held-out history and the OCR bake-off table.
- **Classifier**, matcher (82%, eval 50/50), satellite student (acc 0.936, macro-F1 0.634), agent eval (8/8), control-group DiD summary.
- **Honesty log:** the corrections table (from docs/decisions.md highlights).
- **Known limitations**, listed plainly.
- **Decision log viewer:** searchable D-001… list.

### 9. Review queue
Uncertain items (1,302), each with a crop, candidate values and the reason. Accept/edit/reject posts to `/review/{id}`.

---

## Backend additions needed (backend-engineer)
Read-only JSON endpoints (all owner-masked by default):
- `GET /stats/overview`: all KPI numbers, each with a `source` string.
- `GET /parcels/{uid}`, `/parcels/{uid}/timeline` (events + smoothed NDVI/BSI series + season states), `/parcels/{uid}/extractions` (rows with page/bbox/checks), `/parcels/{uid}/satellite` (DiD rows, chips list, season states), `/parcels/{uid}/findings`.
- `GET /findings?category=&severity=&village=&block=&level=&limit=&offset=`, `/findings/summary`, `/findings/{id}/evidence-pack`, `/idle-land`.
- `GET /layers/{name}.geojson` for new layers: `controls` (control cells), `fmb_overlaps`, `outside_survey`, and parcel properties enriched with stage, finding counts, the season state (param `season=YYYY-rabi`) and the DiD signal.
- `GET /documents?type=&stage=&q=` (exists; add filters + classifier fields), `/classifier/summary`.
- `GET /router/models` (registry + doctor.json), `/router/usage` (aggregates from router_log), `/router/chains`.
- `GET /eval/summary` (parsed from docs/metrics.md + stored eval JSONs), `/decisions` (parsed docs/decisions.md), `/progress` (parsed docs/progress.md).
- `GET /runs` (history); `POST /runs` already streams SSE; make sure SSE events carry router candidates/scores, critic/judge details and the ledger hash.
- `POST /ledger/verify?run_id=`.

## Quality bar
- Every page loads from the real API (no mocked data in the shipped build).
- Every widget has loading, empty and error states.
- It works at a 1366×768 projector resolution.
- The first map render is < 2 s (simplified geometries).
- Playwright smoke tests cover: overview loads; map click → parcel page with timeline; findings → evidence pack; the agent console runs an example query and shows the plan, steps, critic and judge; the models page shows the registry; the evaluation page shows the heldout2 numbers.
