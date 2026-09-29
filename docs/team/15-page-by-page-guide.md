# 15 · Page-by-page guide to the web app

**Who this is for:** team members who have to explain NilaSaatchi AI ("Paper vs Planet") to reviewers, in simple words.

**How to use it:** every page and every visible section has four things:
1. **What it shows** (what you can see).
2. **What it means** (the plain-language reading).
3. **How it works underneath** (which API call, pipeline or model produces it).
4. **Example** from our real data.

At the end of each page there is a **Say this in one line** sentence and a **Common reviewer question + answer**.

**Companion docs:**
- Domain words (Sentinel-2, NDVI, FMB, LDR, mutation, DiD and so on): [14-domain-terms.md](14-domain-terms.md). Every term below is explained briefly in place, and the glossary has the long version.
- Older, shorter walkthrough: [../ui/01-user-guide.md](../ui/01-user-guide.md).
- Numbers: [../metrics.md](../metrics.md). Where a measured number is quoted here, the metrics row is named next to it.

**Where the examples come from.** All examples were read on 2026-09-30 from the running local API and database. Owner names never appear in this guide. Live counts change as new data arrives, so treat them as "as read on that date".

---

## 0. The frame that is the same on every page

The app is React + Vite, in `web/src`. The routes are in `web/src/App.jsx`. The screen has five permanent pieces.

### 0.1 Left menu (NavRail)
- **What it shows:** eight numbered entries: 1 Overview · 2 Map workspace · 3 Parcel · 4 Findings · 5 Agent console · 6 Documents · 7 Models & routing · 8 Review queue.
- **What it means:** the app is a story in eight screens, from "what is this" (1) to "what did the AI not trust" (8).
- **How it works underneath:** plain client-side links. The labels come from `i18n/labels.js`, so they switch with the EN / தமிழ் button.
- **Note:** an older "Evaluation & honesty" page is not in the menu. It was removed in D-062, because the UI should show the product and not how we built it.

### 0.2 Top bar (TopBar)

| Control | What it shows | What it means | How it works underneath |
|---|---|---|---|
| **Command bar** ("Ask the agent (EN/தமிழ்)… e.g. Verify Melathattaparai survey 233") | A text box on every page | Type a question in English or Tamil. Enter jumps to the Agent console and runs it. | Navigates to `/agent?q=…`. The Agent console then starts a run with `POST /runs`. Hidden in the read-only cloud demo. |
| **Chaos** checkbox | A tick box | Pretend one AI provider (Gemini) is down, so you can watch the system switch to another model. | When ticked, the Agent console sends `chaos: "gemini:down"` with the run. The server calls `app.router.chaos.set_chaos()`, so the router treats that provider as failing. Hidden in the read-only cloud demo. |
| **Demo mask** checkbox (ON by default) | A tick box | Landowner names appear as tokens like "Owner ••17". Keep it ON when presenting. | Two layers. (1) The **server always masks** owner names in the API replies (`masked: true`; unmasking only works if the server is started with `ALLOW_DEMO_UNMASK=true`, local only). (2) The tick box adds a **client-side backstop** (`lib/mask.js`) that masks any owner-like field in raw JSON shown in the browser. |
| **EN / தமிழ்** | Two buttons | Switches the *labels* (menu, headings, buttons) between English and Tamil. Document data stays in its original language. | `i18n/labels.js` and `components/ingest/labels.js`. The Agent console answers in the language you *ask* in. |

Two things to know about the top bar:
- Demo mask OFF does **not** make the browser ask the server for real names. The UI never sends `demo_mask=false` (I found no such request in `web/src`). So even with the tick off, the API still returns masked names. This is a safe default, but do not tell a reviewer that the toggle "reveals names". I could not confirm what a server started with `ALLOW_DEMO_UNMASK=true` would show, because the UI does not request it.
- The read-only banner (below) is shown instead of the command bar and Chaos in the cloud demo.

### 0.3 Read-only banner (ReadOnlyBanner)
- **What it shows:** a slim amber strip at the top: "Read-only cloud demo: browse all data, maps, parcels and findings. Uploading documents, running the live AI agent and review actions are available in the full application."
- **What it means:** you are on the AWS Lambda snapshot, which serves frozen answers. Anything that writes or calls a live model is switched off.
- **How it works underneath:** the web build is compiled with `VITE_READ_ONLY=true`. It is not shown on the laptop build or on the EC2 full app.

### 0.4 Footer (StatusFooter)

| Item | What it shows | What it means | How it works underneath |
|---|---|---|---|
| **API: ok (live)** | A green dot and the API mode | The web page can reach the backend. Red means it cannot. | `GET /health` |
| **DB: reachable via API** | Text | The database is answering, because the API needs it. | Inferred from the same `/health` call. The browser never talks to the database directly. |
| **AWS spend vs US$100 event budget** | Two numbers: "Cost Explorer (lags ~1 day)" and "router estimate (live)" | How much of the FarmwiseAI event budget of US$100 we have used. Cost Explorer is AWS's own bill but is about a day late. The router estimate counts our own Bedrock calls immediately. | `GET /stats/overview`, KPI ids `aws_spend_usd` and `aws_spend_router_estimate_usd`. Read on 2026-09-30 locally: Cost Explorer US$10.07, router estimate US$8.01. The budget cap of 100 is written in the footer code. |
| **Last refresh** | The time the page rendered | When the footer last drew, not when the data was last updated. | Browser clock. |

### 0.5 The four states every box can be in
Every data box (`Widget`) shows one of: **loading** (a grey pulse), **empty** ("No data" plus a reason), **error** (a red box with a Retry button), or **not available yet**. The app never draws a blank panel. If a reviewer asks "what if the backend is down?", point at these.

### 0.6 The three badges you will see repeatedly

| Badge | Values | Meaning |
|---|---|---|
| **Confidence pill** (coloured dot and %) | green ≥ 80%, amber 50–79%, red < 50% | How sure the extraction or check was. It is a model and verification score, not a probability of being legally right. |
| **Status badge** | `auto`, `queued`, `auto_unverified`, `corrected`, `approved` | Extraction status. `auto` = the arithmetic checks passed and it was accepted without a human. `queued` = a check failed, so it went to the Review queue. `auto_unverified` = accepted but the checks could not verify it, so confidence is capped. |
| **Verdict badge** | ACCEPT, DOWNGRADE, REVIEW, REROUTE | The agent's judge decision on a claim (see the Agent console). |

---

## 1. Overview

**Route:** `/` · **Source:** `web/src/pages/Overview.jsx`

**Purpose:** a one-minute answer to "what is this system?"

| Section | What it shows | What it means | How it works underneath |
|---|---|---|---|
| **Explanation paragraph** | A short paragraph: about ten legal stages produce documents that make claims about parcels. Those claims sit in scanned paper and are rarely checked against the ground. | The one-sentence problem statement. It ends with the honest limit: the system "does not decide what happened; it surfaces measured disagreements for a human to check". | Static text. In Tamil mode a small note says only the labels are translated. |
| **Pipeline** (six clickable boxes) | Sources → Paper → Planet → Knowledge graph → Agent → Findings, each with a one-line description | The system in the order the data flows. Click a box to jump to the page that shows that stage. | Static links: Sources and Paper go to Documents, Planet to Parcel, Knowledge graph to Map, Agent to the Agent console, Findings to Findings. |
| **Key measured numbers** (KPI tiles) | Tiles such as Documents 2,362; Pages 12,859; Extraction rows 26,269; Parcel facts 42,380; Acquisition events 15,342; Parcels 1,242; Parcels with facts 1,082; Satellite observations 382,536; Parcel-seasons 40,986; Findings 1,388; Review queue 1,301; Classified document types 24; the two AWS spend tiles | Real counts from our database. The tile hover shows the source, and database counts show the SQL behind them. | `GET /stats/overview`. Each count is a SQL `COUNT` over the real tables. The values are read live, so they can drift slightly from older docs (see "Inconsistencies" at the end). |
| **Satellite data freshness** | Three figures: latest image date, scene count, last checked. A button "Check for new satellite images" (full app only). | How up to date the satellite record is. The button fetches any Sentinel-2 scenes newer than the last one. It re-predicts only the parcels and seasons affected, refreshes the control-group comparison and findings, and shows job progress. | `GET /ingest/satellite/status`, then `POST /ingest/satellite-refresh`, then polling `GET /ingest/jobs/{id}` every 2 s. Measured: the first real run added the 2026-09-26 scene (588 scenes in total, 382,536 parcel observations) - see [13-phases5-7-as-built.md](13-phases5-7-as-built.md) §3 and metrics rows for D-064. It uses the existing model (no retraining, no model calls). |

**Worked example:** the "Findings 1,388" tile is the same number as the sum of the category counts on the Findings page (485 + 380 + 270 + 127 + 79 + 32 + 13 + 2). It is not a separate number typed into the UI.

**Say this in one line:** "Paper versus Planet reads the acquisition documents, builds each parcel's satellite history, and shows where the two disagree, with the evidence for every claim."

**Common reviewer question:** *"Are these KPI numbers real or hard-coded?"*
**Answer:** They come from `GET /stats/overview`, which runs SQL against the live database on each request. The project rule is that no brief query or answer is hard-coded in `app/`, `pipeline/` or `web/`. The only static text on this page is the explanation and the pipeline captions.

---

## 2. Map workspace

**Route:** `/map` · **Sources:** `pages/MapWorkspace.jsx`, `components/map/MapView.jsx`, `SeasonSlider.jsx`, `Legend.jsx`

**Purpose:** see all 1,242 parcels of the Allikulam SIPCOT park and their surroundings on one full-screen map. A parcel here is an **FMB polygon**. FMB (Field Measurement Book) is the survey sketch drawn for each sub-division of a survey number. Controls float over the map: colour-by and slider at top left, layer list at top right, legend at bottom left.

### 2.1 The map itself
- **What it shows:** the parcels as polygons, over a free OpenStreetMap-derived basemap (OpenFreeMap "liberty" style; no key needed). Zoom and rotate buttons are at top right.
- **What it means:** each polygon is one surveyed piece of land that some acquisition document may talk about.
- **How it works underneath:** MapLibre GL draws GeoJSON from `GET /layers/parcel.geojson` (1,242 features). Each feature carries properties such as `parcel_uid`, `area_ha_gis`, `current_stage`, `n_findings`, `did_signal`, `match_status`, and the distances to roads, substation and rail.
- **Hover card:** hover any parcel to see its ID, village, area (ha), current stage, finding count, and (if a seasonal colouring is on) the land-use state or stage for the selected season.
- **Click:** click a parcel to open its Parcel page. A red outline marks the selected parcel.

### 2.2 "Colour by" dropdown
Choosing an option repaints the parcels and rewrites the legend.

| Option | What the colours mean | Where it comes from |
|---|---|---|
| **None (single colour)** | All parcels blue. The legend shows one entry, "Parcel (FMB)". | - |
| **Acquisition stage (today)** | The furthest legal stage each parcel has reached now: SEC_3_2 (3(2) notice), AWARD, PAYMENT, POSSESSION, MUTATION (records changed to the new owner). | `current_stage` from the `v_parcel_lifecycle` view. |
| **Acquisition stage by season (slider)** | The furthest stage the parcel had reached **by the end of the chosen season**. Grey = not started. Uses the season slider. | `stage_at_season`, served when the parcel layer is requested with `?season=…`. |
| **Finding count** | Darker = more findings on that parcel. The legend says the severity and category need the parcel drill-down. | `n_findings` (sequential colour scale). |
| **Land-use state (season slider)** | What the satellite says the land was doing that season: `cropped`, `irrigated_multi`, `perennial_veg`, `bare_fallow`, `cleared_or_built`, `water`, `insufficient_data`. | `season_state`, from a small LightGBM "student" model trained on labels from a vision-model "teacher" (accuracy 0.936, macro-F1 0.634 - metrics, "Phase 7 final evaluation", Planet row; the rare classes are weak). |
| **Change vs never-acquired farmland (after possession)** | "Vigour dropped vs controls", "Still farmland-like (lead)", "No significant change", "No possession evidence". | `did_signal`, from the control-group DiD test (see §3.6). |
| **Document link level** | How closely documents could be tied to the parcel: "Parcel-level documents", "Survey-level documents", "Block-level documents only", "No documents linked". | `match_status` from the matcher. Block-level evidence is never presented as parcel-level. |

### 2.3 Season slider and Play
- **What it shows:** a slider from **2018-rabi** to **2026-summer**, a Play/Pause button and the current season name. It appears **only** for the two seasonal colourings. For the other colourings a grey note says the colouring does not change over time.
- **What it means:** seasons are Indian farming seasons: **kharif** (Jun-Sep, monsoon crop), **rabi** (Oct-Feb, winter crop), **summer** (Mar-May). "2019-rabi" means Oct 2019 - Feb 2020. 2018-rabi is marked partial, because Sentinel-2 imagery only starts in January 2019.
- **How it works underneath:** each season is fetched once with `GET /layers/parcel.geojson?season=2024-rabi` and cached. All seasons are prefetched in the background once you choose a seasonal colouring. Play moves on every 0.7 s, but only when the map already shows the current season, so there are no skipped frames. While a season loads, the old colours and legend stay on screen (D-073, D-075).
- **Reading it:** press Play on "Land-use state". You should see most parcels turn `cropped` in each rabi season and `bare_fallow` in summer and kharif. That is the normal cycle. It is why "green" alone can't prove farming (see §3.6).

### 2.4 Legend
- **What it shows:** colour swatches with labels, always visible at bottom left.
- **What it means:** the colours are fixed per value (a value keeps its colour as the slider moves). Grey means "not started" or "no data".
- **How it works underneath:** built by `lib/colorScale.js` from the categories that the API returned. Seasonal legends always list every category in a fixed order.

### 2.5 Layer list (top right)
Each entry is a tick box that shows or hides a layer. Layers that failed to load are greyed out and marked **(unavailable)** (§2.6).

| Layer (label in the list) | Default | What it is | Underlying layer name |
|---|---|---|---|
| **FMB parcels (1,242)** | on | The parcel polygons | `parcel` |
| **Park boundary** | on | The Allikulam SIPCOT park outline | `ref_layer_park_boundary` |
| **Roads - major** | on | Trunk and primary roads, thick red | `ref_layer_roads` with `is_major = true` |
| **Roads - other** | off | Smaller roads, thin orange | `ref_layer_roads` with `is_major = false` |
| **Waterbodies** | on | Tanks, ponds and streams | `ref_layer_waterbodies` |
| **Substations** | on | Electrical substations | `ref_layer_substations` |
| **Rail** | on | Railway lines | `ref_layer_rail` |
| **Schools** | off | School points | `ref_layer_schools` |
| **SIPCOT parks** | off | Other SIPCOT industrial parks, for context | `ref_layer_sipcot_parks` |
| **FMB QA issues (overlaps + cross-village)** | off | Survey-map faults: parcels that overlap each other or cross a village line | `fmb_qa` |
| **FMB overlaps only** | off | Just the overlaps (394 features in `fmb_qa`; the overlaps-only subset is smaller) | `fmb_overlaps` |
| **Parcels outside their survey** | off | Parcels whose polygon lies outside the survey boundary they belong to | `outside_survey` |
| **Control-group cells (2-6 km ring)** | off | 388 small cells of never-acquired farmland in a ring 2-6 km outside the park, used as the comparison group in the satellite test | `controls` |

*How it works underneath:* reference layers are the given GIS files loaded into PostGIS (`ref_layer_*` tables). `fmb_qa`, `fmb_overlaps` and `outside_survey` are single-issue views over the FMB quality view; `controls` are the control cells. All are served by `GET /layers/{name}.geojson`.

### 2.6 Why some layers say "(unavailable)"
The map asks the API for each layer separately. If a request fails (an HTTP error, a timeout, or a layer missing from the server), that layer is greyed out with "(unavailable)"; hover it to see the reason. The other layers still work. If *every* layer fails, the whole map shows "No layer could be loaded - is the API running?"

**What I observed on 2026-09-30:**
- The local API returned all 13 layers (HTTP 200).
- On the EC2 server built on 2026-09-30, three layers returned **HTTP 500**: `fmb_qa`, `fmb_overlaps` and `outside_survey`, so they show "(unavailable)" there. **Cause (found and fixed):** they are materialized views, and `pg_restore` created them empty ("has not been populated"). `scripts/migrate.py` now refreshes any empty materialized view after a restore (tested: 366 overlaps and 28 outside-survey parcels, same as the laptop). The laptop bundle setup and any newly built server get the fix; the current EC2 server keeps the old state until it is rebuilt.


Say to a reviewer: "A layer that cannot load is greyed out and named; it never silently disappears or shows fake data."

### 2.7 Example - how to read the map
Open the map, choose **Change vs never-acquired farmland**, and hover a parcel. The card gives, for example, its village, area, `current_stage` (MUTATION for parcel `Melathattaparai|233`) and its 3 findings. Click it and you land on its Parcel page (§3).

**Say this in one line:** "Every parcel is one polygon, and I can colour the whole park by legal stage, by what the satellite saw each season, or by whether the land behaves differently from nearby farmland after possession."

**Common reviewer question:** *"What is the 'control group' on the map?"*
**Answer:** The teal cells switched on in the layer list are real never-acquired farmland 2-6 km outside the park, under the same monsoon. We compare each parcel against them before and after each legal event, so that "the whole area turned green" cannot be mistaken for "this parcel is being farmed". See [../metrics.md](../metrics.md) row "P3 DiD vs control group".

---

## 3. Parcel page (the signature view)

**Route:** `/parcel/:uid` (for example `/parcel/Melathattaparai%7C233`; `%7C` is the `|` in the parcel ID) · **Sources:** `pages/Parcel.jsx`, `components/parcel/*`, `components/evidence/EvidenceViewer.jsx`

**Purpose:** everything we know about one parcel, side by side: what the papers say and what the satellite saw. A parcel ID reads `Village|survey/sub-division`. With no parcel selected the page just says "No parcel selected. Pick one from the map".

### 3.1 Header

| Field | What it shows | What it means | Underneath |
|---|---|---|---|
| **village / unit / block** | e.g. Melathattaparai / 4 / 2 | The village, and the acquisition "unit" and "block" the project groups parcels into. | `GET /parcels/{uid}` |
| **geodesic area (GIS / UTM)** | 2.0306 / 2.0344 ha | The area of the FMB polygon measured two ways. *Geodesic* means measured on the curved earth. UTM is a flat-map projection. They agree to about 0.2%, which shows the area is not a projection artefact. | Computed in PostGIS from the polygon. The document extent is compared against this on the Findings page. |
| **stage (days in stage)** | MUTATION (1,367d) | The furthest legal stage reached, and how long it has been there. *Mutation* = revenue records changed to the new owner. | The `v_parcel_lifecycle` view. |
| **evidence level** | parcel | How closely documents are tied to this parcel: *parcel* (its own survey number), *fmb* (map-only), *block* (block-level documents only), *village*. | The matcher's `match_status`. |
| **open findings** | 3 | Issues currently recorded for this parcel. | Count from the `finding` table. |
| **inside park boundary** | yes | Whether the polygon lies within the SIPCOT park outline. | A spatial test in PostGIS. |

### 3.2 Paper vs Planet timeline (the chart)
- **What it shows:** one chart, 2019 to 2026.
  - A **dark green line** is NDVI. NDVI = (NIR - Red)/(NIR + Red), a greenness score from satellite bands. Higher means more vegetation.
  - **Small dots** are the actual satellite observations. The line breaks where a date is not backed by a real observation (for example clouds), rather than joining across it.
  - **Coloured bands behind the line** are season states (§3.3).
  - **Coloured circles along the top** are document events (award, possession…). A number badge on a circle means several documents share that date and stage. Hover for the date. Click a circle to open its evidence when an extraction is attached.
  - A tick box **"show BSI (bare-soil index)"** adds a dashed brown line. BSI goes up when soil is bare, so it helps spot ploughing.
- **What it means:** this is the paper and the planet on one time axis. If a document says "possession 21 March 2025" and the green line drops sharply soon after, the two agree. If the line just keeps its farming rhythm, they may not.
- **How it works underneath:** `GET /parcels/{uid}/timeline` returns `series` (date, ndvi, bsi, ndwi, supported), `events` (stage, date, document id, link kind) and `season_states`. The satellite numbers come from Sentinel-2 scenes through the `planet` pipeline. The events come from the extracted documents, through the matcher, into `acquisition_event`.
- **Example (`Melathattaparai|233`):** the series has 565 points, of which 561 are backed by real observations. Events run from SEC_3_2 (2021-05-04) through AWARD (2023-11-10, 2024-02-17…), POSSESSION (2024-12-02 from doc 2248, and 2025-03-21 from doc 2207) and MUTATION (2023-01-01, 2025-01-08, 2025-04-04). Several are `block_level` links, meaning the document names the block but not this survey number.

### 3.3 Season state (bands and legend under the chart)
- **What it shows:** a colour key: `cropped`, `irrigated_multi`, `perennial_veg`, `bare_fallow`, `cleared_or_built`, `water`, `insufficient_data`.
- **What it means:** the model's reading of the land in each season. **`bare_fallow`** = bare or resting land (beige). **`cropped`** = a crop is growing (light green). **`cleared_or_built`** would mean the land was cleared or built on.
- **How it works underneath:** a LightGBM student model predicts each parcel-season (`parcel_season` table) from seasonal features. Uncertain cases route to a vision second opinion.
- **Example:** for `Melathattaparai|233` the states from 2023 to 2025 alternate `bare_fallow` (kharif), `cropped` (rabi), `bare_fallow` (summer), each with p ≈ 0.97-0.99. That is the normal local farming rhythm and it continues **after** the 2025-03-21 possession: the 2025 rabi season is still `cropped`. No `cleared_or_built` season was observed.

### 3.4 Documents (linked extractions)
- **What it shows:** a list on the left (each row: `fact type · doc N`, a status badge, a confidence pill and "self-consistency: pass/fail"), and a viewer on the right with prev/next arrows and "n / total".
- **What it means:** each row is one value the AI read from a scanned page and tied to this parcel. Click a row to see the scanned page with a **red box around the exact place the value was read from**. Beside it are the details:
  - **schema, doc type**: the kind of table the row came from.
  - **extractor / engine**: `vlm:primary` = the first read by a vision-language model (Bedrock Ministral 8B). `vlm:second` = a second read with a different crop and prompt, used when checks failed. `tesseract` and `text_layer` = classic OCR and embedded text, mostly for page headers.
  - **bbox method**: how precisely the red box was placed. `grid_row` = the box wraps that single table row. `grid_table` = the box wraps the whole table (the exact row is not known). `page` = the whole page (no finer location).
  - **auto / queued**: whether the row was accepted automatically or sent to the Review queue.
  - **self-consistency**: an arithmetic check on the page. Row sums must equal the printed totals, hectares and acres must agree (about 2.47 acres per hectare), and amount must equal acres times the block rate. `fail` means the page read may be wrong.
  - **confidence**: the pipeline's confidence in the read.
  - **Extracted row (raw)**: the values as JSON. Owner fields are masked.
- **How it works underneath:** `GET /parcels/{uid}/extractions` gives the list; `GET /evidence/{extraction_id}` gives the page reference, box, extractor, tier and row; the page image is `GET /documents/{id}/pages/{n}.webp`. The pipeline is in `pipeline/extract/` and is described in [06-phase2-document-intelligence.md](06-phase2-document-intelligence.md).
- **The privacy tag** in the viewer (`PII` / other) says whether the page contained personal data. `PII` rows are only ever routed to models that do not train on inputs (0 PII calls to training-tier models: metrics "Privacy" row).
- **Example A - the pass and the fail side by side (parcel `Umarikottai|182`, all read from the database):**

  | Row shown | Badge | Confidence | Self-consistency | Reading |
  |---|---|---|---|---|
  | `POSSESSION · doc 2211` (an LDR, "Block_4___19.pdf", page 2, `grid_row`, `vlm:primary`) | `auto` | 85.0% | pass | The receipt row was read, the checks passed, and a human was not needed. |
  | `MUTATION · doc 2348` (a chitta/patta extract, page 6, `grid_row`) | `queued` | 70.8% | fail | The page's arithmetic did not hold, so this row went to the Review queue and should not be trusted yet. |
  | `extent_ac · doc 2363` (a patta extract, page 6, `grid_row`) | `queued` | 70.8% | fail | Same: queued. |

  Note that `Umarikottai|182` has **no** findings and that its 2211 row is `POSSESSION`, not `extent_ha`. The `extent_ha` row from doc 2211-style LDRs appears on other parcels. On `Umarikottai|181/1` the row list includes `extent_ha · doc 598` (`auto`, 85.0%, pass, `grid_row`) beside `extent_ha · doc 2363` (`queued`, 85.0%, fail, `grid_table`).
- **Example B - an LDR row (document 2284, "LDR__3____21 (2).pdf", dated 2025-03-21):** its row for parcel `Peroorani|172/8` is `vlm:primary` (Bedrock Ministral 8B), `grid_row`, 85.0%, pass, `auto`. That is the best case: the box is on one row, the checks passed, and the row was accepted with no human.
- **Example C - `Melathattaparai|233`'s own list has 14 rows**, for instance `classification · doc 2207` (page 1, whole-page box, `auto`, pass), `AWARD · doc 372` (page 5, `grid_row`, 85.0%, pass), `extent_ha · doc 470` page 4 (`page` box, 70.8%, self-consistency fail, `queued`), `extent_ac · doc 2352` (`grid_table`, 70.8%, fail, `queued`) and `MUTATION · doc 2362` (`grid_row`, 70.8%, fail, `queued`). So even a "clean" parcel has some rows the system itself refuses to trust.

### 3.5 "View document" popup (DocumentModal)
- **What it shows:** a large popup with the scanned page, "‹ Prev / page 2 of 2 / Next ›", a Close button, and a title bar with document type, stage, village and folder. If it was opened from evidence, a **red box** marks the place, with the note "The red box on page N marks the rows the evidence comes from."
- **What it means:** the actual scan, not a summary. Close with the button, the Escape key, or a click outside. Left and right arrow keys turn pages.
- **How it works underneath:** `GET /documents/{id}/meta` (page count, type, village) and `GET /documents/{id}/pages/{n}.webp`. The same popup is used by the Findings evidence pack (button "View document"), the Documents catalogue and the Review queue. A deep link `/documents?extraction=<id>` opens it at the evidence page.

### 3.6 "Satellite: chips + DiD vs controls"
- **Chips:** small pictures per date - a **true-colour** chip (how it looks to the eye) and an **NDVI** chip (greenness) - so you can see the parcel itself at key dates. Example: `Melathattaparai|233` has 8 chip files (2024-01-23, 2024-12-03 and others). If none exist, the page says "No rendered chips for this parcel yet." These come from `GET /chips/{parcel_uid}/{date}.png?kind=truecolor|ndvi`.
- **The DiD table.** DiD is **difference-in-differences**: the parcel's change around a legal event minus the same change in the control group. In symbols: (parcel - controls after the event) minus (parcel - controls before it). It is the way to say "it changed *more than nearby never-acquired farmland did*".

  | Column | Meaning |
  |---|---|
  | **event** | Which legal event the before/after is around: `t_3_1` (the 3(1) notification), `t_award`, `t_possession` |
  | **metric** | What was measured. `vigour_z` = greenness (vegetation vigour) relative to controls. `plough` / `plough_z` = a bare-soil "ploughing-like" signal. The `_z` versions are standardised so they can be compared across parcels. |
  | **scope** | Which seasons were used: `all` seasons or `rabi` only (the most reliable, since rabi is the main crop season) |
  | **n pre/post** | How many observations sit before / after the event. A tiny "post" number means weak evidence. |
  | **DiD** | The estimated difference. Positive = the parcel went *up* relative to controls; negative = *down*. |
  | **95% CI** | The 95% confidence interval from bootstrapping. If it **does not include zero**, the difference is statistically significant, and the row is highlighted amber. |

- **The on-page "What this means" text** says: we do not ask "is the field green?" because everything greens up after the monsoon. We ask whether the parcel keeps behaving like never-acquired farmland after each event. A significant drop vs controls suggests possession changed something. Keeping pace, plus a ploughing signal, suggests continued farming. The caveat: this is a lead for field verification, not a legal conclusion; 10 m pixels under-detect ploughing; most parcels only have one post-possession season.
- **How it works underneath:** `GET /parcels/{uid}/satellite` returns the `parcel_did` rows and chip paths. The controls are 119,116 observations of never-acquired farmland (2-6 km ring), and the table has 22,356 rows across all parcels ([07-phase3-satellite-evidence.md](07-phase3-satellite-evidence.md) §5). Nothing on this page calls an AI model live.
- **Example (`Melathattaparai|233`, event `t_possession`, 2025-03-21, from the API):**
  - `vigour_z`, `rabi`: n = 7 / 1, DiD +0.35, 95% CI [-0.13, 0.90]. The interval includes zero, so **no significant change in greenness vs controls** - the parcel is still farmland-like. This is the value quoted in [../metrics.md](../metrics.md) ("Demo parcel Melathattaparai 233").
  - `plough_z`, `rabi`: n = 6 / 1, DiD +2.15, CI [0.85, 3.70]. The interval excludes zero, so the row is amber: **a ploughing-like signal significantly stronger than controls after possession**. Same metrics row: "plough DiD +2.15 [0.85, 3.70]; only 1 post season".
  - Read together: after possession the parcel neither browned out nor went bare. It kept farming-like behaviour. That is why the finding is a *low-severity lead* ("possibly still farmed"), not a conclusion. Only one post-possession rabi season exists (n post = 1).
- **Contrast:** a parcel whose DiD for `vigour_z` is negative with an interval entirely below zero is a "vigour drop vs controls". The metrics row counts 143 such parcels and 1,143 of 1,242 "farmland-like" ([../metrics.md](../metrics.md), "P3 DiD vs control group").

### 3.7 Findings (this parcel)
- **What it shows:** one card per finding: category · severity, a confidence pill, a title, any caveats in amber, and "Open evidence pack".
- **What it means:** the issues the rules engine has recorded for this parcel. The link opens the full proof on the Findings page.
- **How it works underneath:** `GET /parcels/{uid}/findings`.
- **Example (`Melathattaparai|233`, 3 findings):**
  - #3905 `PV3_POST_POSSESSION_ACTIVITY`, low, confidence 0.55: "Ploughing-like signal stronger than controls after possession while farmland-like: possibly still farmed (lead)". Its caveats: single post-possession season, 10 m pixels mix neighbouring land cover, a north-east-monsoon weed flush can green fallow land, clouds and gaps.
  - #2076 `FMB_QUALITY`, medium, 0.95: the FMB polygon overlaps `Peroorani|219/1` by 0.2095 ha across a village line.
  - #2077 `FMB_QUALITY`, low, 0.95: overlap with `Peroorani|218/6` by 0.0348 ha.

**Say this in one line:** "For any parcel I can show you what the paper says with the red box on the scan, what the satellite saw with the control-group comparison, and where they disagree, together with our caveats."

**Common reviewer question:** *"Green land after possession means it is still farmed, right?"*
**Answer:** No, and the page is built to avoid that trap. Almost every parcel greens up after the north-east monsoon, weeds included (6,130 "cropped" versus 27 "bare" rabi parcel-seasons since 2021), so greenness alone proves nothing. We compare against never-acquired farmland before and after each event, and we label the result a lead for a field visit ([07-phase3-satellite-evidence.md](07-phase3-satellite-evidence.md) §5; the same caveat is printed under the table).

---

## 4. Findings

**Route:** `/findings` (`/findings?evidence=<id>` opens a pack) · **Sources:** `pages/Findings.jsx`, `components/evidence/EvidencePackPanel.jsx`, `components/charts/ChartWidget.jsx`

**Purpose:** the list of paper-vs-planet issues the rules engine found, each with its evidence. The heading reads "Findings (N open)". A finding is a **signal for someone to check, not a legal conclusion**.

### 4.1 Evidence pack (opens at the top)
When you click a table row (or "Open evidence pack" on a Parcel page), the pack opens at the top of the page and scrolls into view. A Close button closes it.

| Part | What it shows | What it means | Underneath |
|---|---|---|---|
| **"In simple words" box** (green) | A plain-language explanation: a headline, what was found, "the numbers, explained", why the system flagged it, "it could also be explained by", how sure we are, what to check next, and a bottom line. A footer says either "Written by AI from the evidence below" or "Standard explanation". | A translation of the technical evidence for a non-expert. **The facts and numbers below the box are the source; this box is only an explanation.** It is a signal to check, not a legal conclusion. | `GET /findings/{id}/plain-summary?lang=en|ta`. Router task `present` (PSEUDO privacy tier: only the finding's facts, never owner rows). The result is cached per finding, rule version and language. If no model answers, a template text is used. In the read-only cloud demo the box uses cached explanations, or is hidden if none exist. |
| **Title, category · severity · parcel; verdict badge; confidence; Export JSON** | e.g. the finding's one-line title and a confidence pill | The claim in one line, and how sure the rule is. Export JSON downloads the whole pack. | `GET /findings/{id}/evidence-pack` |
| **Metrics block** | A small JSON of the numbers the rule used | The raw numbers behind the title, so you can recompute them. | Part of the pack (`pack.metrics`) |
| **Paper (document evidence)** | One card per document row: doc type · date, "folder · file name", a confidence pill, a status badge, the extracted values (owner fields masked), and a **View document** button | What the papers say. The button opens the scan in the popup (§3.5) with the red box on the exact rows. | Pack `paper[]`: document id, page, box, extraction id. Rows with no page number look it up via `GET /evidence/{extraction_id}`. |
| **Planet (DiD vs controls)** | The same DiD table as on the Parcel page | What the satellite says, against the control group. | Pack `planet.parcel_did` |
| **Chips** | True-colour and NDVI thumbnails | Pictures at key dates, when they exist. | `GET /chips/…` |
| **Caveats** | Amber bullet list | Why the finding might be wrong. Every finding carries at least its category caveats. | Pack `caveats`. A footer shows the rule version (`findings-v1`) and status. |

- **Example - finding 1977 (COMPENSATION_MISMATCH, high, confidence 0.70).** The title: "Amount ₹252,108 ≠ 2.125 ac × ₹500,000/ac = ₹1,062,500 (-810,392)".
  - Metrics: amount ₹252,108; extent 2.125 ac; rate ₹5,00,000 per acre (inferred as "the modal rate of document 647"); expected ₹10,62,500; difference -₹810,392.
  - Paper: an `AWARD_7_2` document, page 5 of document 647 ("7(2) Awarded / block_1_2nd_Award.pdf"), row 5, survey 227, box method `grid_table`, 85% confidence, self-consistency **fail**, status **queued**.
  - The **parcel is `Melathattaparai|227`**, not 233. (Metrics: "Demo finding 1977 (Melathattaparai 227) on paper - real: 2.125 ac paid ₹2,29,208 where every other row pays ₹5 lakh/ac".)
  - Caveats: the rate is inferred from the same document's rows; the row may include tree or structure compensation not itemised; the document's own arithmetic may be wrong.
  - **The honest reading:** this one was checked against the scan and is real. But our audit found that only **1 of 5** compensation mismatches held up on the page (merged cells, missed tree columns, tables with other rates), whereas extent mismatches held 5 of 5 ([../metrics.md](../metrics.md), "Added 2026-09-28: findings audit"). So compensation findings are presented as **leads**, not facts.

### 4.2 Filters (dropdowns)
- **What it shows:** dropdowns for **Category**, **Severity**, **Village**, **Block** and **Evidence level**, each option with a count (for example "Extent mismatch (485)"), plus **Clear filters** once any is set.
- **What it means:** narrow the table to what you want. Categories use plain names: Extent mismatch, Change after possession, Map quality (FMB), Compensation mismatch, Idle land, Extraction error, Land-class conflict, Proposal vs sanction.
- **How it works underneath:** the option lists and counts come from `GET /findings/facets`, so they always match the data. The table is `GET /findings` with the filters as query parameters (limit 200).
- **Numbers today (from the facets call):**
  - Categories: EXTENT_MISMATCH 485; PV3_POST_POSSESSION_ACTIVITY 380; FMB_QUALITY 270; COMPENSATION_MISMATCH 127; PV4_IDLE_LAND_BANK 79; EXTRACTION_ERROR 32; PV1_CLASSIFICATION_CONFLICT 13; DOC_VERSION_CONFLICT 2 (total 1,388).
  - Severities: low 1,030, medium 287, high 71.
  - Evidence levels: parcel 934, fmb 270, block 182, village 2.

### 4.3 "By category × severity" chart
- **What it shows:** a stacked bar chart. Each bar is a category, split by severity (high / medium / low).
- **What it means:** where the problems are, and how serious. Most findings are low. The high ones are concentrated in compensation and extent mismatches.
- **How it works underneath:** `GET /findings/summary` (from the `v_finding_summary` view). The page adds up the rows across evidence levels so each bar shows every finding.

### 4.4 "All findings" table
- **What it shows:** the columns ID, Parcel, Category, Severity, Confidence, Evidence level, Village, Status. Click a header to sort; there is a CSV download. Click a row to open its evidence pack.
- **What it means:** the working list. **Evidence level** tells you how much to trust the parcel attribution: `parcel` = tied to that survey number, `block` = only the block is known, `fmb` = a map-only finding.
- **Underneath:** `GET /findings`.

### 4.5 "Idle land bank by block (ha, road/substation distance)"
- **What it shows:** a table per village/block: idle hectares, number of parcels, oldest possession date, distance to the nearest major road (km), distance to the nearest substation (km), and confidence.
- **What it means:** land the government has taken possession of at least six months ago where the satellite shows **no clearing and no construction**. That is the "idle land bank", which matters for an industrial park because idle acquired land cannot be used by an investor. Distances show how well-connected each idle block is.
- **How it works underneath:** `GET /idle-land`, from the `v_idle_land_bank` view. There are 79 blocks and 834.3 ha in total ([13-phases5-7-as-built.md](13-phases5-7-as-built.md) §1). The measured figures are in [../metrics.md](../metrics.md), "Findings engine (P5)".
- **Example:** the largest row is Allikulam, unit 9, block 2: 13.47 ha over 31 parcels, oldest possession 2022-12-08, 1.43 km to a major road and 4.45 km to a substation (finding 1257, confidence 0.75).
- **Caveats** (shown in the block's evidence pack, not in the table): "area is the geodesic sum of FMB parcels (overlaps double-count, D-023)" - the area adds up the polygons, and where FMB polygons overlap that ground is counted twice, so the hectares can be slightly high. "No cleared_or_built season state observed; construction smaller than a 10 m pixel is missed" - the satellite pixel is 10 m, so a small shed or a house would not show up. This means **"no construction detected" is not "confirmed empty"**.

**Say this in one line:** "1,388 findings, each with the scanned page, the satellite numbers, the checks, and its caveats, and each one framed as something to verify on the ground."

**Common reviewer question:** *"How do you know the findings are right?"*
**Answer:** Two audits, both in [../metrics.md](../metrics.md) ("Added 2026-09-28"). First, we recomputed 37 sampled findings from the source tables and all 37 followed from their stored evidence. Second, we checked the scanned page for the 10 paper-based ones: 6 of 10 held (extent mismatch 5 of 5, compensation mismatch 1 of 5). So we show compensation findings as leads. We report that failure rather than hiding it.

---

## 5. Agent console

**Route:** `/agent` (`/agent?q=…` auto-runs a question) · **Sources:** `pages/AgentConsole.jsx`, `components/agent/*`, `hooks/useRunEvents.js`

**Purpose:** ask a question in English or Tamil and watch an AI agent plan, look up the data, check its own numbers, get a second opinion from another AI company, and write the answer. The agent uses free models routed through one router; it does not answer from memory.

In the read-only cloud demo the command box is replaced by a note: you can browse past runs and replay their plan, evidence and ledger, but not ask new questions.

### 5.1 Command bar and example chips
- **What it shows:** a text box ("Ask in English or தமிழ்…"), a Run button, and clickable example questions such as "Verify Melathattaparai survey 233: what do the documents claim, and what does the satellite show?".
- **What it means:** any question about the data. The example chips are a starting point.
- **How it works underneath:** `POST /runs` with `{request, lang, chaos?}` returns a run id. `GET /examples` supplies the chips. The chips are not hard-coded in the web app.

### 5.2 Progress timeline (left column, RunProgress)
Seven stages in plain words. A grey circle = waiting, a pulsing blue circle = working, a green tick = done, a red mark = stopped. A live timer runs at the top ("Working… 34s", then "✓ Done in 52s").

| Stage label | What actually happens | Underneath |
|---|---|---|
| **Understanding the question** | Reads the request, pulls out the village, survey number, category and so on, and asks a clarifying question if something essential is missing. | Agent node `intake` |
| **Making a plan** | Writes a short list of steps (which tools to call). The goal is shown. | `plan` task, routed to a free model; the plan is checked against a schema |
| **Looking up the data** | Runs the steps: guarded SQL, GIS and satellite tools. Shows "2 of 3 data step(s) done (parcel timeline, findings query)". | Tools in `app/tools`; SQL passes a guard that refuses anything but a read-only SELECT |
| **Checking the numbers** | Automatic checks on every claim: does the number match the data returned? Shows "N of M checks passed". | The verifier (deterministic, no model) |
| **Second opinion from another AI company** | A model from a **different vendor** than the one that wrote the answer tries to prove one claim wrong. | The critic, routed so that it is never the producer's own vendor |
| **Deciding what to trust** | Gives each claim a verdict. | The judge |
| **Writing the answer** | Assembles the answer card. | `present` task |

If the run needs more information, an amber box shows "The agent needs more information: …". If it fails, a red box shows the error. That is by design, since the agent will not guess.

### 5.3 Answer card
- **What it shows:** a green-bordered card with **Answer**, a title, a narrative paragraph, KPI tiles, charts, tables, and sometimes a small map, then an amber **Keep in mind** list.
- **What it means:**
  - **Claim tags:** the small grey tags inside the narrative (for example `c_s1_0`) are claim ids. Every sentence that states a fact is tied to a claim, and each claim has a verdict in the technical details. Hover the tag for a hint.
  - **Keep in mind** = the caveats attached to the answer (for example "the sample is one season", or "the deterministic stub was used").
- **How it works underneath:** the final `workspace` object of the run (title, narrative, kpis, charts, tables, map layers, caveats), delivered through the live stream `GET /runs/{id}/events` (Server-Sent Events).

### 5.4 "How the answer was checked" (three boxes)
1. **Automatic checks**: "N of M passed", with the first four check names listed with ✓ or ✗.
2. **Second opinion**: either "Claim held" (green) or "Found a problem" (red), with the vendor's name and the hypothesis it tried to prove.
3. **Final verdicts**: counts in plain words:
   - ACCEPT = the checks support it;
   - REVIEW = needs a person to look;
   - DOWNGRADE = kept, but with lower confidence;
   - REROUTE = re-checked with another route.
- **Example** (a saved run, "How many acquisition events are recorded for Keelathattaparai survey 359/2?"): the judge returned 3 verdicts - claims `c_s1_0` and `c_s1_1` **ACCEPT** ("Checks passed, no critic challenge", confidence 0.9) and `c_s1_2` **REVIEW** ("critic challenge succeeded", confidence 0.35). So the answer keeps two claims at high confidence and visibly marks the third as needing a person. This is the "visibly downgraded" rule in action.

### 5.5 Technical details (collapsed by default)
Open it to see what a reviewer usually asks about.

| Part | What it shows | What it means |
|---|---|---|
| **Plan steps** | Each step with its tool, status, model, latency, tokens and cost, plus a **why?** link | The plan the agent actually followed |
| **"why?" (routing)** | The router's decision: candidates, scores, models filtered out and why, the model chosen, every attempt (with error kind and latency), and the privacy tier requested vs effective | Why this model. If a model failed, the amber "fallback: A → B (reason)" chip appears. |
| **Verifier / Critic challenges / Judge verdicts** | The full check list; for the critic the vendor, model, hypothesis, test and outcome; for the judge each verdict and reason | The complete audit of the checks |
| **SQL / GIS operations** | The exact SQL or spatial call that was run | Proof that the numbers came from the database, not from the model |
| **Audit ledger** | A list of `#seq node → hash…` lines, the head hash, and a **Verify ledger** button | A hash chain: every step's hash includes the previous one, so changing any past step breaks the chain. **Verify ledger** calls `POST /ledger/verify?run_id=…` and reports "Chain intact - N entries, head …" or "Tampering detected at seq …". |
| `run <id> · live connection: …` | The run id and stream state | For debugging |

- **Example:** verifying the saved run above returns `ok: true`, 15 ledger entries and head hash `71293d16…` (tested on 2026-09-30).

### 5.6 Earlier runs (run history)
- **What it shows:** a collapsible table of past runs (Run id, Status, Request, Created, Updated). Click a row to replay it: its timeline, answer, details and ledger.
- **What it means:** runs are saved, so a reviewer can re-open exactly what the agent did.
- **How it works underneath:** `GET /runs` and `GET /runs/{id}/trace`. It is open by default until you start or replay a run.

### 5.7 Chaos toggle (top bar)
Tick **Chaos**, then Run. The run should still complete: the router marks the Gemini models as failing and chooses the next model in the chain. You will see a "fallback: gemini-… → …" chip in the technical details. Measured: the fallback works when a second provider has free quota; the live drill was skipped on the evaluation day because quota was exhausted ([../metrics.md](../metrics.md), "Robustness - provider outage": *partial*).

**Say this in one line:** "The agent plans, queries the real database, checks its own numbers, gets a rival AI company to try to disprove it, and tells you which claims it still trusts."

**Common reviewer question:** *"How good is the agent, honestly?"*
**Answer:** From [../metrics.md](../metrics.md): on 27 Sep, 8 of 8 queries matched the SQL reference. On 28 Sep, the free planner left optional filters blank and only 3 of 8 matched. After a fix that fills filters from the question, the original 8 went to 5 of 8, and a 25-question demo set reached 24 of 25 (96%). Median latency is about 50 s (target 25 s, missed), tool success 15/17 and verified-claim rate 78.5% (targets 90%, missed). We show the failures and do not hide them.

---

## 6. Documents

**Route:** `/documents` (`/documents?extraction=<id>` opens the viewer at the evidence page) · **Sources:** `pages/Documents.jsx`, `components/ingest/*`, `DocumentModal.jsx`

**Purpose:** the catalogue of every source document, plus (in the full app) uploading a new one and getting a verification report. The heading reads "Documents (N)"; N is 2,362 today.

### 6.1 Catalogue and filters
- **What it shows:** dropdowns for **Village**, **Document type** and **Legal stage** (options come with counts), a **Search** box (file name, document number or page text), **Clear filters**, and a table with the columns ID, Classified type, Stage, Village, Date, Pages, Status, and **Folder≠type?**. It is paged 50 at a time.
- **What it means:** the documents come from stage folders (3(2) notices, awards, LDR, chitta/patta, Form F…). Each one was **classified** by the AI, which also records the legal **stage** it belongs to. **Folder≠type?** flags a document whose AI-classified type disagrees with the folder it was in. That is a data-quality signal, not necessarily an error.
- **How it works underneath:** `GET /documents` and `GET /documents/facets`. The classifier is a rules + model pipeline; stage accuracy on 100 labelled documents is 0.90, type accuracy 0.80 ([../metrics.md](../metrics.md), "Classification"; the type target was missed).
- **Example:** document 2211 is an `LDR` (Land Delivery Receipt, the paper that says the land was handed over), stage POSSESSION, Umarikottai, 2025-02-19, 2 pages. Document 2348 is a `CHITTA` (a revenue-record extract), stage MUTATION, Umarikottai, 2023-01-01, 14 pages.

### 6.2 Document popup
Click any row to open the same viewer as the Parcel page (§3.5), at page 1. From here you can flip through the whole scan.

### 6.3 Upload panel (full app only)
- **What it shows:** a drag-and-drop area ("Drag a PDF here, or choose a file"), an optional **Village** field, an optional **Document type** dropdown (default "Let the AI decide"; the options are Award 7(2), Award 7(3), Form F, Form E, Chitta / patta extract, LDR, and others), and an **Upload & process** button. PDFs only, up to 50 MB. A note says: "Extraction is AI-assisted. Values the model is unsure about go to the Review queue for a person to check."
- **What it means:** try the system on a document it has not seen. **Declaring the type is recommended**: the AI's own guess is weak without folder names (right on 11 of 20 in our test; all LDRs right, all awards wrong). The report still shows what the AI suggested, so that is visible.
- **How it works underneath:** `POST /ingest/documents` (multipart) starts a background job. An exact duplicate (same MD5) is recognised and not processed again.

### 6.4 Stage progress
After upload, a stepper shows the job live, refreshed every 2 s: **Saving file → Cataloguing → Classifying document type → Reading tables → Loading extracted data → Linking to parcels → Checking findings**. Each step has a tick and a duration. Statuses are Queued, Processing, Done, Failed and **Duplicate** (amber, with a link to the existing document).
- **Result panel** (when done): document type, pages, rows extracted, facts, the **linked parcels** as clickable links to their Parcel pages, "+N new findings" and "N review items" links, and two buttons: **Download report (PDF)** and **Download rows (CSV)**.
- **Underneath:** `GET /ingest/jobs/{id}`; the result comes from the job record.
- **Measured:** 20 unseen documents all completed - 387 parcel links and 806 facts - see [../metrics.md](../metrics.md) "New data" row.

### 6.5 Download report
The **Document verification report** (PDF) contains a plain summary, the steps, the linked parcels with their legal stage, findings with a plain explanation of each category, and every row read. The CSV holds the rows. **Owner names are never included.** Endpoint: `GET /ingest/jobs/{id}/report?fmt=pdf|csv|html`. The guide is [../ui/04-report-guide.md](../ui/04-report-guide.md).

### 6.6 Recent uploads
A table of the last 20 upload jobs (Job, File, Status, Created). Click a row to expand its stage stepper.

**Say this in one line:** "You can drop in a new award or receipt, watch the system read it, link it to parcels and run the checks, and download a report that has no owner names in it."

**Common reviewer question:** *"Does it work on documents you have never seen?"*
**Answer:** Yes, with a known weakness. 20 unseen rescans all ran end to end (387 parcel links, 806 facts). The weak point is the automatic document-type guess (11 of 20 right), which is why the uploader can declare the type. Extraction on a fresh held-out set is below target for extent (79.2% vs 90%) and owner (83.3% vs 85%), reported as missed in [../metrics.md](../metrics.md) ("Extraction" row).

---

## 7. Models & routing

**Route:** `/models` · **Source:** `pages/ModelsRouting.jsx`

**Purpose:** show that every model call goes through **one router**, that personal data never goes to a model that trains on inputs, and how much has been spent. The router is `app/router`; the rule is "free models only, every call through the router".

### 7.1 AWS spend vs US$100 event budget
- **What it shows:** one sentence: "Cost Explorer (lags ~1 day): $X of the US$100 event budget · router estimate (live, Bedrock calls only): $Y", with the time Cost Explorer was fetched.
- **What it means:** the budget FarmwiseAI gave each team is US$100. Our own cap is lower (US$30 since D-079). Two figures are shown because the AWS bill lags by a day and our own count is instant but covers only Bedrock calls.
- **Underneath:** `GET /stats/overview` (`aws_spend_usd`, `aws_spend_router_estimate_usd`). Read on 2026-09-30: Cost Explorer US$10.07 and router estimate US$8.01.

### 7.2 Model registry
- **What it shows:** a table with id, vendor, weights (open or proprietary), modalities (text, image, pdf), **"trains on free tier?"** (yes in red, no in green), cost class (free, local, capped_fallback), and the last health check (latency and vision proof).
- **What it means:** the "trains on free tier?" column is the privacy switch. Google Gemini and Cohere free tiers may train on inputs (shown "yes"); Groq, local Qwen and AWS Bedrock Ministral do not ("no"). Personal data is only allowed to go to the "no" models.
- **Underneath:** `GET /router/models`, built from the model registry and the latest health check. Today's registry lists 10 models: two Bedrock Ministral (3B, 8B), two Cohere, three Gemini, two Groq, one local.

### 7.3 Task → chain
- **What it shows:** one line per task: the task name, its privacy tier in brackets, then the ordered chain of models with a capability score, and a "terminal" fallback.
- **What it means:** the router tries models left to right. If one is down, over quota or fails validation, it goes to the next. The "terminal" is what happens if all fail (for example "template narrative", "clarify user", "review queue").
- **Privacy tiers:** **PII** = the payload may contain personal data (table reads, document classification, SQL and tool arguments) → only models that do not train. **PSEUDO** = pseudonymised (owner names replaced by tokens): planning, critic, judge, answer writing. **PUBLIC** = satellite images and public facts.
- **Example:** `table_read_pii` (PII) → `bedrock-ministral-8b` → `groq-qwen-vl`, terminal `review_queue`. `critic` (PSEUDO) → Gemini flash-lite, Groq Qwen VL, Cohere Command A, Groq GPT-OSS, Gemini flash-lite 3.1, terminal `deterministic_only`.
- **Underneath:** `GET /router/chains`.

### 7.4 Usage (router log)
- **What it shows:** a table (model, task, calls, success rate, fallback rate, shadow cost, actual cost), a latency p50/p95 list, and a **PII-tier audit** box.
- **What it means:** what actually happened. **Fallback rate** is how often the first-choice model failed and another took over. **Shadow cost** is what the call would have cost at list price; **actual cost** is what we paid (zero on free tiers). The audit box turns **green** when no PII call went to a training-tier model.
- **Underneath:** `GET /router/usage`, read from `router_log.sqlite`, which records every call.
- **Example (read 2026-09-30):** `bedrock-ministral-8b` on `table_read_pii`: 6,160 calls, 95.2% success, 4.8% fallback. The PII audit read "12,774 PII calls ok, 0 went to a training-tier model". The packaged metrics row records the same result at the Phase 7 evaluation: **0 of 14,202 PII-tier calls to a training-tier model** ([../metrics.md](../metrics.md), "Privacy" row). The counts differ because the router log keeps growing; the zero is what matters.

**Say this in one line:** "Every AI call goes through one router that knows which models may see personal data, falls back automatically, and logs its cost, and here is the proof that no personal data reached a model that trains on it."

**Common reviewer question:** *"Why not just use the best model everywhere?"*
**Answer:** Two reasons. Cost and privacy: the challenge is on free tiers, and some free tiers train on inputs, so owner data has to go only to non-training models. Robustness: a chain of models keeps working when one provider is down or out of quota. We also compared policies on the same 8 queries (routed, all-open, all-proprietary); routed had the best validity, verification and latency ([../metrics.md](../metrics.md), "Routing" row, D-071).

---

## 8. Review queue

**Route:** `/review` · **Sources:** `pages/ReviewQueue.jsx`, `components/review/ReviewItemCard.jsx`

**Purpose:** the human-in-the-loop. Any page the AI was not sure about ends up here as one card. A person looks at the scan and either approves the values, corrects them, or rejects the page. Heading: "Review queue (N open)"; N is 1,301 today.

### 8.1 Reason filter
- **What it shows:** a dropdown "Reason" with counts.
- **What it means (plain names):**
  - **Numbers don't add up** (`self_consistency_fail`) - totals, hectare/acre or amount checks failed;
  - **Text read as a table** (`prose_as_table`) - the page is prose, so "rows" may be invented;
  - **Owner-name quality sample** (`owner_sample`) - a 10% random sample of owner names for quality control;
  - **Handwritten page** (`handwriting`);
  - **Table reader failed** (`vlm_failed`);
  - **Blurred newspaper scan** (`blurred_newsprint`).
- **Counts today:** self_consistency_fail 622, prose_as_table 332, owner_sample 272, handwriting 58, vlm_failed 15, blurred_newsprint 2 (from `GET /review-queue/facets`).
- **Underneath:** routing rules in the extraction pipeline (D-033): a page goes to a human only for failed checks, handwriting, blurred newsprint, a table invented from prose, or the owner sample. Pages that pass their checks are accepted.

### 8.2 The review card
- **What it shows:** the item number and status; a thumbnail of the page (click "View full page" for the popup with prev/next); "**Why a human is needed**: …" in plain words with the AI confidence; "N row(s) were read from this page"; and "open full evidence" (which opens the extraction viewer with the red box above the list).
- **What it means:** this is the exact page, the exact reason, and how many values depend on the decision.
- **Underneath:** `GET /review-queue`, `GET /review/{id}/rows`, and `GET /documents/{id}/pages/{n}.webp`.

### 8.3 The three actions
| Button | What it does | What it means |
|---|---|---|
| **Approve page** (or "Confirm: nothing to extract" if no rows were read) | Marks the page approved; its rows count as verified. | "The values read from this page are right." If some rows were corrected earlier, the label is "Approve page (with corrections)". |
| **Correct values** → **Save corrections** | Opens an editable table of the rows the AI read (Survey, Sub-div, Extent ha and ac, Amount, Patta, Land class; owner names are masked and not editable). **Save corrections only saves your edits**; the page stays open with a blue note "N row(s) corrected and saved. Check them, then click 'Approve page' to finish." | Two steps on purpose: first save your values, then approve. The AI's original values are kept for the audit trail, and the parcel facts are updated. |
| **Reject page** | Marks the page unusable; its rows are excluded. | "This page is unreadable or not usable." |

- **Underneath:** `POST /review/{id}` with `decision: approved | corrected | rejected` and the corrected values. The reviewer is recorded as `reviewer@ui`. Actions are hidden in the read-only cloud demo.
- **Example:** an `owner_sample` item is a sample check on an owner column (for example a Form F page, one of the first open items). The reviewer compares the masked owner column to the scan, and approves if the read looks right.

**Say this in one line:** "Whatever the AI is not sure about goes to a person with the page, the reason and the values, and the person's correction is kept next to the AI's original."

**Common reviewer question:** *"What stops wrong AI reads from turning into findings?"*
**Answer:** Three lines of defence, all visible in this guide. Arithmetic self-consistency checks on every page (a failed page is `queued`, as on the Parcel page). The Review queue for those pages and a random sample of owner reads. And the findings caveats: a finding built on a `queued` row says so. We also report where this still leaks, for example the compensation findings that held on the scan only 1 of 5 times.

---

## 9. Quick reference: the same idea on different pages

| Idea | Where you see it |
|---|---|
| Red box on the scan | Parcel page viewer, Findings evidence pack (View document), Documents deep link, Review card |
| `auto` vs `queued` | Parcel page rows, Review queue |
| Control group / DiD | Map (controls layer, "Change vs never-acquired farmland"), Parcel page table, Findings pack, PV3 findings |
| Confidence pill | Every table row, every finding, every claim |
| "Signal, not a legal conclusion" | Parcel page text, "In simple words" box, evidence pack caveats |
| Router / fallback | Agent "why?", Models & routing chains and usage |
| Owner masking | Top bar toggle; server default; report and CSV |

---

## 10. Things I could not confirm, and doc/code inconsistencies

**Could not confirm:**
1. **Why three map layers said "(unavailable)" on the EC2 server.** Resolved: empty materialized views after the database restore; fixed in `scripts/migrate.py` (see §2.6).
2. **What Demo mask OFF does on a server with `ALLOW_DEMO_UNMASK=true`.** The UI never sends `demo_mask=false`, so the toggle affects only the browser-side masking.
3. **Whether the "Roads - other" and "Roads - major" layers show every road.** The API default `limit` is 5000 features, and the local roads layer returned exactly 5000. I did not test whether that truncates roads, and the UI does not pass a limit.
4. **Cloud read-only vs EC2 differences.** I did not click through the read-only Lambda demo in a browser. Statements about it come from the code (`READ_ONLY` flag) and [13-phases5-7-as-built.md](13-phases5-7-as-built.md).
5. I did not run the Playwright suite or the UI in a browser for this guide. Everything about what is on screen comes from reading the JSX, and every value comes from the API or the database.

**Inconsistencies found (no other file was changed):**
- **Finding 1977's parcel.** The finding is on `Melathattaparai|227` (document 647 page 5, survey 227), not `Melathattaparai|233`. This guide uses 227 for the finding and 233 for the timeline/DiD example.
- **Document ids in the "2211 / 2348 / 2363" example.** They are Umarikottai documents (2211 LDR, 2348 and 2363 chitta extracts) and appear on parcels such as `Umarikottai|181/1`, `Umarikottai|182`, `Umarikottai|183`, not on `Melathattaparai|233`. Also, 2211's row on `Umarikottai|182` is `POSSESSION`; the `extent_ha` `auto` 85.0% row on `Umarikottai|181/1` is document 598. And `Melathattaparai|233` links doc 2207 (LDR, 2025-03-21, "Block_2_21.pdf") - the LDR dated 2025-03-21 with the file name `LDR__3____21 (2).pdf` is document 2284 (parcels in Peroorani, for example `Peroorani|172/8`).
- **Two possession dates for `Melathattaparai|233`.** The timeline has POSSESSION on 2024-12-02 (doc 2248, parcel link) and 2025-03-21 (doc 2207). The DiD table and metrics use 2025-03-21; the AI "In simple words" text for finding 3905 says possession was on 2 December 2024. Both dates exist in the data; the guide uses 2025-03-21 for DiD. It is worth knowing before a reviewer asks.
- **`docs/ui/01-user-guide.md` is slightly stale:** it lists satellite observations as 381,294 and the review queue as 1,302; the live API says 382,536 and 1,301. [13-phases5-7-as-built.md](13-phases5-7-as-built.md) says the map has "11 layers"; the code has 13 entries in the layer list (12 toggleable overlays plus the parcels layer).
- **`web/src/components/agent/LedgerDrawer.jsx`** still has a comment and a message saying `POST /ledger/verify` is "not implemented". The endpoint exists (`app/api/routers/runs.py`) and returned `ok: true` for a saved run, so the button works; only the comment and the fallback text are stale.
- **`web/src/components/map/SeasonSlider.jsx`** has a comment saying the backend has not added `landuse_state` to the parcel layer. That was fixed in D-072/D-073 (the property is `season_state`); the comment is stale.
- **Spend cap wording.** The footer and Models page use a US$100 event budget; `CLAUDE.md` still says a US$15 self-set cap, while D-079 raised it to US$30. This guide follows the decisions log (D-079).
- **Nav label:** `i18n/labels.js` still contains a `nav_evaluation` label ("Evaluation & honesty") for a page that was removed (D-062); it is not used in the menu.
