# 14 · Domain and Technical Terms: a Plain-Language Dictionary

**Who this is for:** team members and reviewers who are not experts in land law, GIS (maps) or remote sensing (satellites).

**What it covers:** every term you can meet in the NilaSaatchi AI app ("Paper vs Planet"). The app checks Tamil Nadu land-acquisition documents for the Allikulam SIPCOT industrial park (Thoothukudi) against Sentinel-2 satellite evidence.

**Each term has four parts:**
- **What it is:** the plain meaning.
- **In our app:** what it denotes here.
- **Why:** why it exists or why we show it.
- **Example:** a concrete one, from our real data where possible.

**Where the numbers come from.** Every count below was read from the local database (`nilasaatchi`, read-only queries) on 2026-09-30, or from the code or config file named next to it. The project-wide metrics are in `docs/metrics.md`. This dictionary does not repeat the evaluation scores. Where I could not confirm something from code or data, the text says so.

**Privacy.** No owner names appear here. Examples use parcel ids (`Village|survey`) and document ids only. The app shows owners as `Owner ••17` while "Demo mask" is ON.

## Table of contents
1. [The law and the acquisition lifecycle](#1-the-law-and-the-acquisition-lifecycle)
2. [Document types, forms and folders](#2-document-types-forms-and-folders)
3. [Land, survey and map vocabulary](#3-land-survey-and-map-vocabulary)
4. [Linking papers to parcels: evidence level, stalled, match status](#4-linking-papers-to-parcels)
5. [Reading documents: extraction terms](#5-reading-documents-extraction-terms)
6. [Satellite terms](#6-satellite-terms)
7. [Seasons and land-use states](#7-seasons-and-land-use-states)
8. [The DiD panel (difference-in-differences)](#8-the-did-panel)
9. [Findings: categories, severity, evidence packs](#9-findings)
10. [Idle land bank](#10-idle-land-bank)
11. [The Map page: layers and colourings](#11-the-map-page)
12. [Caveats the app shows](#12-caveats-shown-in-the-app)
13. [AI and system terms](#13-ai-and-system-terms)
14. [Cheat sheet](#14-cheat-sheet)
15. [Terms I could not fully confirm](#15-things-not-fully-confirmed)

---

## 1. The law and the acquisition lifecycle

### 1.1 The big picture

**Acquisition**
- *What it is:* the government compulsorily buys private land for a public or industrial purpose. The owner is paid compensation.
- *In our app:* every parcel in the park is being (or has been) acquired for SIPCOT. The app tracks how far each parcel has got.
- *Why:* if a step is skipped or a number does not match, that is worth checking. That is the whole "paper" side of the product.
- *Example:* `Melathattaparai|233` has 29 dated events across the stages below.

**SIPCOT**
- *What it is:* State Industries Promotion Corporation of Tamil Nadu. It builds industrial parks.
- *In our app:* the acquiring body. Our park is "Allikulam Oil Refinery Industry Formation", Thoothukudi taluk (7 villages, Blocks 1–10). The map also shows 18 SIPCOT park points in the district.
- *Why:* it gives the study area and context.
- *Example:* the "SIPCOT parks" map layer shows those 18 points.

**The Act (TN Act 10 of 1999)**
- *What it is:* the Tamil Nadu Acquisition of Land for Industrial Purposes Act, 1997 (published as Act 10 of 1999). It sets the steps and the section numbers such as 3(1), 3(2), 7(2), 7(3).
- *In our app:* the stage names `SEC_3_1`, `SEC_3_2`, and the award types `AWARD_7_2` and `AWARD_7_3`, come from its sections. Private negotiation under the revival rules also runs alongside it.
- *Why:* the order of steps is the backbone of the "lifecycle" check.
- *Example:* "s.3(1)" means "section 3(1) of the Act".

**Lifecycle (stage order)**
- *What it is:* the fixed sequence a parcel passes through, from sanction to the land record changing.
- *In our app:* stored in `config`/taxonomy as `STAGES`, in this order: GO_AS_LPS → SEC_3_1 → SEC_3_2 → EXEMPTION → PRICE_NEGOTIATION → POSSESSION_NOTICE → AWARD → PAYMENT → POSSESSION → MUTATION.
- *Why:* it lets the app say "this parcel is at stage X", spot skipped steps, and colour the map by stage.
- *Example:* Melathattaparai|233 is currently at `MUTATION` (stage 9).

### 1.2 The ten stages

Counts are **acquisition events** (one document row can give several events) in the table `acquisition_event`: 15,342 in total. Source: `land-domain-knowledge` skill §2, `pipeline/classify/taxonomy.py`.

| Stage code | What it is (plain words) | In our app | Why we show it | Example |
|---|---|---|---|---|
| **GO_AS_LPS** (stage 0) | **G**overnment **O**rder + **A**dministrative **S**anction + **L**and **P**lan **S**chedule. The government approves the scheme and lists village-wise land. | Doc types GO, AS, AS_PROPOSAL, LPS. 12 events. | It is the sanctioned total that the map and later papers are compared against. | GO No.100 of 26.02.2021 and the AS letter of 31.03.2021 both sanction **904.40 ha**. |
| **SEC_3_1** (1) | The gazette notification under section 3(1) (Form C): "these lands are to be acquired". | Doc type `SEC31_GAZETTE`. 73 events. | Mandatory first step; the DiD "t_3_1" event date. | Gazettes of 28.10.2022 to 28.11.2022. A single gazette file can hold unrelated notices, so relevance is judged per notice. |
| **SEC_3_2** (2) | Notice to owners and interested persons. Errata correct earlier notices. | Doc types `SEC32_NOTICE`, `SEC32_ERRATA`. 281 events. | Mandatory second step. | 142 notices and 104 errata documents in the catalogue. |
| **EXEMPTION** (3) | A parcel is proposed for, or granted, release from acquisition. | Doc types `EXEMPTION_PROPOSAL`, `EXEMPTION_GO`. 14 events. | A parcel with an exemption must **not** have an award or payment. | 9 `EXEMPTION_GO` documents. The map's "stage by season" view leaves this stage out. |
| **PRICE_NEGOTIATION** (4) | Committees fix the price per acre for each block, then funds are allotted. | Doc types `DLPNC`, `SLPNC`, `LAND_VALUE`, `FUNDS`. 34 events. | The rate is the "expected" value in the compensation check (amount = acres × rate). | Rates seen: ₹5,00,000, ₹7,00,000 and ₹9,00,000 per acre, varying by block. |
| **POSSESSION_NOTICE** (5) | Notice under s.4(2) (Form E): "surrender the land within 30 days". | Doc type `FORM_E`. 679 events. | Shows the owner was formally told. Often handwritten, so it goes to human review more often. | 461 Form E documents. |
| **AWARD** (6) | The formal decision on compensation. | Doc types `AWARD_7_2`, `AWARD_7_3`, `FORM_F`. 10,195 events (the biggest stage). | Amounts here drive the compensation check. | See 7(2), 7(3) and Form F in section 2. |
| **PAYMENT** (7) | Compensation is paid (demand draft or bank transfer) or deposited in court. | Doc types `DISBURSEMENT`, `COURT_DEPOSIT`, `BANK_INSTRUMENT`. 215 events. | "Awarded but not paid" is a stall signal. | Demand drafts carry no survey number, so they link only at **block** level. |
| **POSSESSION** (8) | The land is physically handed to SIPCOT (Land Delivery Receipt/Certificate). | Doc type `LDR`. 1,308 events. | **The most important date for the satellite test**: after possession the land should stop being farmed. | Doc 2211 is an LDR for Umarikottai, Block 1 (2 pages). |
| **MUTATION** (9) | The land record (patta/chitta) is changed to show SIPCOT as owner. | Doc type `CHITTA`. 2,531 events. | The last step. "Possession but no mutation" is a stall signal. | 22 chitta documents. Parcel-level current stage counts are in section 4. |

**Stage order rules used by the app** (from the skill file, not invented):
- A parcel cannot properly reach stage N without evidence of the earlier mandatory stages 1, 2, 6 and 8.
- "Awarded but not paid", "Paid but no possession" and "Possession but no mutation" are called *stalled*.
- An exemption plus an award or payment is an `EXEMPTION_CONFLICT`.
- **Uncertain:** the live parcel table's `missing_mandatory` column shows, for Melathattaparai|233, `{SEC_3_1}` (no s.3(1) evidence linked to that parcel). I did not check whether the app's UI displays this column.

### 1.3 Other legal terms

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Gazette** | The official government newspaper. | Source of s.3(1) notices; digital text layer, so we do not need OCR. | Legal proof of notification. | Gazette No. 527 (28.11.2022) cites Allikulam lands, but the same file also holds a railway erratum and a solar-plant notice. |
| **Form C / Form E / Form F** | Form C = s.3(1) notification. Form E = possession notice. Form F = per-owner compensation apportionment. | Doc types `SEC31_GAZETTE`, `FORM_E`, `FORM_F`. | Tells you what a page is before reading it. | Form F is the largest group: 754 documents, about 4,000 pages. |
| **7(2) award** | Consent (negotiated) award: the owner agrees to the price. | `AWARD_7_2`, stage AWARD. | Most awards. | 286 documents. |
| **7(3) award** | Final award when the owner does not consent. | `AWARD_7_3`, stage AWARD. | A different legal route; the page may list lands *already paid under 7(2)*, so those rows are not read as 7(3) lands. | 56 documents. |
| **Errata** | A correction to an earlier notice. | `SEC32_ERRATA`. Its "should read as" rows override the "published as" rows. | The corrected value is the truthful one. | 104 documents. |
| **DLPNC / SLPNC** | District / State Level Price Negotiation Committee. | Doc types `DLPNC`, `SLPNC`. `committee_level` records which. | Source of the rate per acre. | 8 DLPNC and 3 SLPNC documents. The "DLPNC" folder holds only SLPNC material. |
| **LDR** | **L**and **D**elivery **R**eceipt/Certificate: land handed to SIPCOT. | Doc type `LDR`, stage POSSESSION. Folders "LDR Issued" and "Possession Taken" hold the same form, so we treat them as one type. | Defines the possession date. | 150 documents. Doc 2211: type LDR, stage POSSESSION, folder "LDR Issued", Umarikottai Block 1, classifier confidence 0.95. |
| **Patta / Chitta** | Patta = the ownership record. Chitta = its e-services extract. | Doc type `CHITTA`, stage MUTATION. | Shows who owns the land *now*. | Tamil printouts with a digital text layer. |
| **Poramboke** | Government (not private) land. | Land class in extracted facts. | Poramboke that is farmed is an encroachment signal (PV1). | Village totals list "patta + poramboke = total". |
| **Punsey / Nansey (புன்செய் / நன்செய்)** | Dry land / wet (irrigated) land. | Land class: `dry`, `wet`, `poramboke`. | The PV1 finding compares the paper class with what the satellite sees. | Finding: "DRY land with irrigated_multi in 2 of 3 years before 3(1)". |
| **Court deposit** | Money is paid to a court instead of the owner (disputed title, unknown heirs). | Doc type `COURT_DEPOSIT`, stage PAYMENT. | Explains why some payments never reach an owner. | 45 documents. |
| **Demand draft** | A bank cheque-like instrument. | Doc type `BANK_INSTRUMENT` with `payee_kind` (for example `court`). | Linked to parcels only by amount and date. | 116 documents. |

**Uncertain:** 38 of the 2,362 documents have no classified type. I did not confirm whether these are unclassified or zip-archive files. The catalogue also stores `scheme_relevance`: 2,273 `allikulam`, 9 `other_scheme`, 42 `unknown`, and 38 empty.

---

## 2. Document types, forms and folders

**Classified type vs folder label**
- *What it is:* the *folder name* a PDF sits in versus what the AI classifier says the page really is.
- *In our app:* two separate columns, `folder_label` and `classified_type` (Documents page). A badge appears when they disagree.
- *Why:* folders are unreliable. In a 100-document check, 33% were in the "wrong" folder.
- *Example:* the "Amount Disbursed" folders match the content in only 1 of 17 checked files. They mostly hold award orders and court-deposit demand drafts.

**doc_type vs stage** (D-025)
- *What it is:* `doc_type` = the document's *form* (for example `BANK_INSTRUMENT`). `stage` = the lifecycle step it proves (for example PAYMENT).
- *In our app:* two different fields. The mapping is in `pipeline/classify/taxonomy.py`.
- *Why:* form and stage do not always line up one to one.
- *Example:* a demand draft to the District Judge is `BANK_INSTRUMENT`, stage PAYMENT (court deposit).

**All document types the classifier can output** (`DOC_TYPES`). Counts are catalogue documents (2,362 in total):

| doc_type | What it is | Stage | Count |
|---|---|---|---|
| `GO` | Government Order | GO_AS_LPS | 0 found (see note below) |
| `AS` | Administrative Sanction letter | GO_AS_LPS | 4 |
| `AS_PROPOSAL` | The *Tamil proposal* to the sanction (asks 911.395 ha; not the sanction) | GO_AS_LPS | 1 |
| `LPS` | Land Plan Schedule | GO_AS_LPS | 7 |
| `SEC31_GAZETTE` | s.3(1) gazette | SEC_3_1 | 73 |
| `SEC32_NOTICE` | s.3(2) notice to owners | SEC_3_2 | 142 |
| `SEC32_ERRATA` | Correction to a notice | SEC_3_2 | 104 |
| `EXEMPTION_PROPOSAL` | Land proposed for exemption | EXEMPTION | 5 |
| `EXEMPTION_GO` | Exemption approved | EXEMPTION | 9 |
| `DLPNC` | District price committee | PRICE_NEGOTIATION | 8 |
| `SLPNC` | State price committee | PRICE_NEGOTIATION | 3 |
| `LAND_VALUE` | Land value fixation | PRICE_NEGOTIATION | 21 |
| `FUNDS` | Funds allotted | PRICE_NEGOTIATION | 2 |
| `FORM_E` | Possession notice | POSSESSION_NOTICE | 461 |
| `AWARD_7_2` | Consent award | AWARD | 286 |
| `AWARD_7_3` | Final award | AWARD | 56 |
| `FORM_F` | Compensation apportionment | AWARD | 754 |
| `DISBURSEMENT` | Payment record | PAYMENT | 53 |
| `COURT_DEPOSIT` | Court deposit | PAYMENT | 45 |
| `BANK_INSTRUMENT` | Demand draft or bank paper | PAYMENT | 116 |
| `LDR` | Land Delivery Receipt | POSSESSION | 150 |
| `CHITTA` | Patta/chitta extract | MUTATION | 22 |
| `CALCULATION_SHEET` | Calculation working sheet | none | 1 |
| `CLASSIFICATION_ORDER` | Order on land classification | none | 0 in the catalogue |
| `CORRESPONDENCE` | Substantive letter | none | 1 |
| `OTHER` | Catch-all | none | 0 in the catalogue |

- *Why the "none" rows exist:* some documents fit no single lifecycle stage.
- *Note:* `GO` did not appear in the database's classified-type counts. The GO No.100 content is inside `LPS.pdf`, so I could not confirm any document is typed `GO`.
- `COVERING_LETTER` was dropped as a class (D-025): those files are substantive letters and are classified by content.

**Scheme relevance** (`allikulam` / `other_scheme` / `unknown`)
- *What it is:* whether a notice belongs to our Allikulam scheme.
- *Why:* mixed gazettes hold unrelated notices.
- *Example:* a solar-plant notice from Tirunelveli in a gazette that also has an Allikulam notice is `other_scheme` (9 documents in total).

**Type confidence**
- *What it is:* the classifier's certainty from 0 to 1.
- *Example:* doc 2211 has 0.95.

**date_precision**
- *What it is:* how exact a date is (day, month, year).
- *Why:* many documents print a blank day (".02.2025").
- *Example:* two events for Melathattaparai|233 on 2025-03-21 have precision "month", so the day is not certain.

---

## 3. Land, survey and map vocabulary

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Village** | The revenue village (the smallest admin unit that names a land record). | 7 villages: Allikulam, Keelathattaparai, Melathattaparai, Umarikottai, Peroorani, Ramasamypuram, South Silukanpatti. Spelling variants are in `config/aliases.yaml`. | Survey numbers repeat across villages, so we never match across villages. | "Melathattaparai" also appears as "Mela Thattaparai" or in Tamil. |
| **Taluk** | An administrative area above the village. | Thoothukudi taluk (the FMB layer spells it "Thoothukkudi"). | Context only. | — |
| **Unit** | A grouping of the park's land used in the scheme files. | Column `unit_id`. Files are organised in Units (Unit 4, Unit 7…). | Documents cite Unit + Block; used to find a village when a document only names the scheme. | Peroorani appears under both Unit 4 and Unit 5 in the papers. |
| **Block** | A numbered sub-area of the park (1–10). | Column `block_id`. Price rates, awards, payments and the idle-land finding are per block. | Payments carry only a block, not a survey number. | Melathattaparai|233 is in Unit 4, Block 2. |
| **Survey number** | The number of a land survey field within a village. In Tamil, புல எண். | Column `survey_no`. Cadastral map = 396 whole-survey polygons. | The key to find a parcel in a document. | `177` in "177/4A". |
| **Subdivision (sub_div)** | A survey field is split into parts. In Tamil, உட்பிரிவு. | Column `sub_div`. | Documents often name the exact part. | The `4A` in `177/4A`. |
| **KIDE** | The FMB's key: `survey/subdivision`, or just `survey` when no subdivision. | Column `kide`. 157 FMB rows have no subdivision. | It repeats across villages. | `177/4A`, or `233`. |
| **parcel_uid** | Our unique parcel key: `Village|KIDE`. | Used everywhere (URLs, findings). Decision D-020. | Because KIDE alone is not unique. | `Melathattaparai|233`, `Allikulam|16/3`. |
| **Parcel** | One FMB polygon: one surveyed piece of land. | The 1,242 shapes on the map. Each has a stage, findings and satellite history. | Everything is judged per parcel. | Melathattaparai|233 = 2.0306 ha geodesic. |
| **FMB (Field Measurement Book)** | The sketch map of each surveyed subdivision. | The provided layer `Park_fmb_Map.geojson`: **1,242 subdivision polygons**. It is our matching target. | Papers name survey numbers, so we can put them on a map. | 366 pairs overlap; 28 spill outside their survey. |
| **Cadastral map** | The map of whole survey fields (one polygon per survey). | 396 polygons. Only used to check the FMB (the "outside their survey" test). | Shows how far the FMB drifts. | South Silukanpatti: 17.40 ha cadastral against 6.82 ha FMB against 6.66 ha in papers. |
| **Park boundary** | The polygon of the whole industrial park. | 1 polygon, 934.32 ha. | Context on the map. | — |
| **Inside park boundary** | A parcel is counted inside if a point on its surface falls within the park polygon. | Column `inside_park_boundary` (code: `ST_Intersects(boundary, ST_PointOnSurface(parcel))`). | A sanity check on the map data. | All 1,242 parcels are `true`. |
| **Extent** | The area a document states for a parcel. In Tamil, விஸ்தீரணம். | Extracted facts `extent_ha` (hectares), `extent_ac` (acres). Also `total_extent_ha`, `cents`. | Compared with the map's area (`EXTENT_MISMATCH`). | 9,905 `extent_ha` facts and 5,016 `extent_ac` facts. |
| **extent_ha / extent_ac** | Extent in hectares / acres, as read from a document. | See above. | Two units are used in papers. | 1 ha = 2.47105 ac exactly, but papers often use **2.47**. Both are accepted. |
| **H.AA.SS notation** | Hectare.Are.Square-metre. | Parsed to decimal hectares. | A common OCR bug source. | `0.95.50` = 0.9550 ha. `904.40.0` = 904.40 ha. |
| **Cent** | 0.01 acre = 40.4686 m². | Used on Form E (84 `cents` facts). | Form E lists house plots in cents. | — |
| **area_ha_gis** | The area computed from the map polygon, in hectares. | Column on `parcel` and `survey`. | The map-side number to compare with `extent_ha`. | Melathattaparai|233 = 2.0306. |
| **Geodesic area** | Area measured on the curved Earth (an ellipsoid), not on a flat sheet. | All our areas, to 4 decimals (`ST_Area(geom::geography)`). | Flat maps distort areas. | Flat "UTM" areas run about +0.22% too high here. |
| **UTM 44N (EPSG:32644)** | A flat map projection in metres, for zone 44 North. | Used only for distances, buffers and overlays (`geom_utm`). | Accurate for metres near the park. | `dist_major_road_m` is computed in UTM. |
| **CRS84 / EPSG:4326** | Plain latitude/longitude. | All provided GeoJSON layers and the web map. | Standard for web maps. | — |
| **Dissolved union** | All polygons merged, overlaps counted once. | Used for village and park totals. | The plain *sum* double-counts overlaps. | Sum of FMB areas 908.82 ha; union **901.50 ha**; sanction 904.40 ha (D-023). |
| **Overlap** | Two FMB polygons cover the same ground. | 366 pairs in table `fmb_qa_overlap` (7.3 ha double-counted). | It is a data-quality issue in the map itself. | Melathattaparai|175 × 176/1A overlap by 0.4592 ha. |
| **Cross-village overlap** | An overlap between parcels of two different villages. | Flag `cross_village`; 110 cross-village lines noted earlier. | Might be a drawing error across a village boundary. | Melathattaparai|188 and Umarikottai|190 (0.2634 ha). |
| **Outside their survey** | An FMB parcel that extends beyond the cadastral survey it belongs to. | 28 parcels, table `fmb_qa_outside_survey`. | Suggests a drawing error. | — |
| **Tamil terms** | நில உரிமையாளர் = landowner; பட்டா எண் = patta no.; இழப்பீட்டுத் தொகை = compensation; தொகுதி = block; வாரிசுதாரர்கள் = legal heirs. | Used in prompts, aliases and the Tamil UI. | The papers are mostly Tamil. | — |

Other GIS words that appear on the map or in the API:

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Roads — major** | Trunk, primary and secondary roads (and links), or roads with NH/SH/MDR reference numbers. | Layer `ref_layer_roads` with `is_major = true`. 37,590 lines in total. | A main road matters for how usable idle land is. | 519 secondary, 492 trunk, 448 primary lines. |
| **Roads — other** | Everything else (residential, service, tracks, paths). | `is_major = false`. Off by default. | Context. | 25,293 residential lines. |
| **dist_major_road_m / dist_substation_m** | Distance (m) from the parcel to the nearest major road / electrical substation. | Columns on `parcel`. | Used in the idle land bank for how easy the land is to develop. | Feeds the "km to road / substation" columns. |
| **Waterbodies** | Village tanks (185 polygons). | Layer + `intersects_waterbody`. | Water context. | No parcel touches a tank; the nearest is 5.2 km away. |
| **Substations** | 36 electrical substations. | Layer, with `voltage`. | Idle-land context. | — |
| **Rail** | 511 rail lines + 37 stations. | Layers. | Context. | — |
| **Schools** | 63 educational institutions. | Layer, off by default. | Context. | — |
| **Airport / Seaport** | 1 airport polygon, 2 seaport points. | Tables exist; not on the Map page's layer list. | Context for later. | — |

---

## 4. Linking papers to parcels

Extracted facts are matched to parcels by `make match`. The matcher accepts a link at score ≥ 0.80 with a margin ≥ 0.10 over the runner-up (`config/thresholds.yaml`), and never links across villages.

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Evidence level (lifecycle)** | How precisely the documents point to this parcel. | Column `evidence_level` in the lifecycle view, shown on the Parcel page. Values: `parcel`, `survey_level`, `block_level`, `none`. | A block-level paper cannot prove a single parcel. | 1,081 parcels `parcel`; 149 `block_level`; 11 `survey_level`; 1 `none`. |
| **parcel-level** | A document row names this exact parcel (survey + subdivision). | Highest confidence. | — | Doc 2207 names Melathattaparai|233 as possessed on 2025-03-21. |
| **survey-level** | The document names the survey but not the subdivision. | Linked to the parent survey. | Less exact. | — |
| **block-level** | The document names only a Unit + Block (for example payment drafts). | Lower confidence (0.50 in config). Never presented as parcel-level. | Honest labelling of weaker evidence. | The `PAYMENT` events of Melathattaparai|233 are block-level. |
| **Match status (map colour)** | The same idea as evidence level, in plain words. | Map option "Document link level": *Parcel-level documents / Survey-level documents / Block-level documents only / No documents linked*. | Shows where our paper coverage is thin. | — |
| **Current stage** | The furthest stage reached (highest stage order with an event). | Column `current_stage`. | The main map colouring. | Of the 1,242 parcels: MUTATION 1,007, POSSESSION 167, SEC_3_2 35, AWARD 31, PAYMENT 1, none 1. |
| **Days in stage** | Days since that stage's first event. | `days_in_stage`. | For stall detection. | — |
| **Stalled flag** | A parcel stuck between two steps. | `stalled_flag` values: `AWARD_NOT_PAID` (31), `PAID_NO_POSSESSION` (1), `POSSESSION_NO_MUTATION` (167). | Points to slow or missing paperwork. | Stall limits in config: award→payment 60 days, payment→possession 30, possession→mutation 90. |
| **Missing mandatory stage** | A required earlier stage has no evidence. | `missing_mandatory`. | Lifecycle check. | Melathattaparai|233: `{SEC_3_1}`. |
| **NOT_STARTED** | Map-only value: no event by the selected season's end. | Appears in "stage by season". | Grey on the map. | — |

**Note.** The agent's built-in assumption text says "Stalled = no next lifecycle event for 90 days or more" (`app/domains/land_acquisition/pack.py`). The real thresholds are the three in config above. See section 15.

---

## 5. Reading documents: extraction terms

Most pages are scanned Tamil. A vision-language model reads the tables, Tesseract reads headers, and our code maps columns and checks arithmetic. Table: `extraction`, 26,269 rows.

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Extraction** | One row of structured data read from a page (a survey row, a header, a total). | Table `extraction`; `row_json` holds the values. | Everything checkable comes from these rows. | Doc 2211 page 2 gives one header row and several parcel rows. |
| **Extractor / engine** | What produced the row (column `extractor`). | Four values: `text_layer` (245 rows; the PDF's own text), `tesseract` (5,450; classic OCR), `vlm:primary` (18,747; the vision model's first read), `vlm:second` (1,827; its second read). | So a reviewer knows how trustworthy a value is. | Doc 2211 header = `tesseract`; its parcel rows = `vlm:primary`. |
| **VLM** | Vision-language model: reads a page image and returns text. | Main reader: Bedrock Ministral 3 8B (see doc 04). | Tesseract fails on table cells and handwriting. | — |
| **Bbox (evidence box)** | The rectangle on the page image where a value was read. | Stored as fractions of the page. The UI draws a **red box** on the scanned page. | Every value must have visible evidence. | Click "View document" and the popup opens at that page with the box. |
| **bbox_method** | How the box was found. | `grid_row` (a row-sized band found by the table grid: 7,201 rows), `grid_table` (only the whole table: 7,023), `page` (the whole page: 12,045). | Tells you how precise the red box is. | Doc 2211 parcel rows are `grid_row`, which is the most precise. |
| **Confidence** | The system's certainty in a row, 0–100%. | Shown as a percentage. Capped for unverified rows. | For triage and downgrading claims. | Doc 2211 page 2: header 65.0% (Tesseract), parcel rows **85.0%** (VLM). |
| **self_consistency** | Did the page's own arithmetic add up? | Values: `pass` (15,371 rows), `fail` (5,041), `n/a` (5,857). Checks: row sums = printed totals; ha ↔ ac (2.47 or 2.47105); amount = acres × block rate; every row has a survey number. | A failed check often reveals a misread number *or* a genuine error in the paper. | A page whose column sum ≠ printed total. |
| **survey_not_serial check** | Guards against reading the row-number column (1, 2, 3…) as survey numbers. | Fails if the first survey numbers run 1..N in order. Triggers a second read or review. | Found on fresh uploads: such a read passed every other check. | — |
| **Second read** | The page is read again with a different crop/zoom and prompt when checks fail. | Column `second_read_decision`: `second_improved` (2,297), `reads_agree` (6,564), `kept_first` (9,773). Accepted only if consistency improves. | Cheap way to fix misreads. | — |
| **Owner read status** | Whether the owner-name read was confirmed. | `vlm_agreed` (3,811: Tesseract's words support the VLM) or `vlm_single` (9,592). | Names are hard; votes help. | Never shown unmasked in demo. |
| **review_status** | Where a row is in the review process. | `auto` (14,790: passed), `auto_unverified` (5,194: accepted but unchecked, confidence capped), `queued` (6,283: waiting for a person), `approved`, `corrected` (2), `rejected`. | Only `queued` rows need a human. | — |
| **Auto vs queued** | *auto* = accepted by checks. *queued* = a person must look. | See above. | Keeps human effort small. | — |
| **Review queue** | The page (and table `review_queue`) where people approve, correct or reject uncertain pages. | 1,302 items. Open reasons: `self_consistency_fail` 622, `prose_as_table` 332, `owner_sample` 272, `handwriting` 58, `vlm_failed` 15, `blurred_newsprint` 2. One `prose_as_table` was decided. | Uncertain values are never quietly trusted. | UI text: "Numbers don't add up", "Text read as a table", "Owner-name quality sample". |
| **Doc id** | The catalogue number of a document. | Table `document.id`. Deep links open the viewer. | An unambiguous reference that avoids file names. | Doc 2211 (LDR, Umarikottai). |
| **View document** | The green button on the Parcel page. | Opens the shared document popup at the value's page, with the red box. | One click from a finding to the paper. | — |
| **schema_name** | The kind of row: `parcel_row` (18,108), `page_header` (5,695), `form_f` (2,019), `village_totals` (377), `summary_item` (63), `payment_instrument` (4), `price_rate` (2), `errata_correction` (1). | Column on `extraction`. | Different rows are checked differently. | — |
| **Parcel fact** | A single value (owner, extent, amount, class…) attached to a parcel. | Table `parcel_fact`. Types: `owner` 13,310, `extent_ha` 9,905, `amount_rs` 7,612, `extent_ac` 5,016, `classification` 2,138, `patta_no` 1,982, `total_extent_ha` 1,232, `boundary` 1,101, `cents` 84. | The unit we compare with the satellite/map. | — |
| **Held-out / golden / dev set** | Test pages: golden = first tuning set; held-out = unseen test; dev = tuning for fixes. | Only in `eval/`. Scores are in `docs/metrics.md`. | Avoids fooling ourselves. | — |
| **Text layer** | Text embedded in a PDF (searchable). | Present in 3(1) gazettes, chitta printouts and possession certificates. | Free and exact when it exists. | — |
| **OCR** | Optical Character Recognition: turn a picture of text into text. | Tesseract (`tam+eng`) locally. | Good at prose, poor at tables. | — |
| **Upload job** | A background job with 7 steps when a PDF is uploaded. | save → catalogue → classify → read tables → load → link → run checks. | Shows the pipeline live. | The same MD5 is never processed twice. |
| **Declared document type** | The type the uploader picks on the form. | Overrides the AI's guess, which stays visible ("AI suggested X"). | The AI's type guess without folder names is weak (right on 11/20 in a test). | — |

---

## 6. Satellite terms

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Sentinel-2** | Two European Copernicus satellites; free images every ~5 days. | Our source, 2019 → today, from the open Earth Search catalogue. | The independent witness of what happened on the ground. | Tile 43PHK; 588 scenes after the last refresh. |
| **L2A** | The Sentinel-2 product corrected to ground reflectance. | The product we use. | Comparable over time. | — |
| **STAC** | A standard catalogue for satellite scenes. | How we search for scenes. | No account needed. | — |
| **10 m pixel** | One image square is 10 × 10 metres on the ground. | Bands used are 10 m. | Small plots blend with neighbours. | A house or a shed smaller than 10 m is invisible. |
| **Scene / acquisition** | One satellite picture on one date. | Table `s2_scene`. | The raw material. | 587 unique acquisitions, of which 307 have ≥ 20% of the park clear; 588 after refresh. |
| **SCL (scene classification)** | A per-pixel label for cloud, shadow, cirrus, etc. | Used to mask bad pixels. | Clouds would fake "bare soil". | — |
| **Chip** | A small cut-out image around one parcel on one date. | Shown on the Parcel page (true colour and NDVI). | Lets a person see what the number means. | Files under `data/s2/chips/`. |
| **Band** | One colour channel of the satellite (red, near-infrared…). | Used to build indices. | — | — |
| **NDVI** | Normalised Difference Vegetation Index = (NIR − Red) / (NIR + Red). High = green plants. | The main line on the Parcel timeline. | Shows crops and weeds. | Crops peak near 0.5–0.8. Bare ground is near 0. |
| **NDWI** | Normalised Difference Water Index = (Green − NIR) / (Green + NIR). | Computed per observation (water maximum feature). | Spots standing water. | No `water` season state was observed (section 7). |
| **BSI** | Bare Soil Index: high over bare, ploughed soil. | The "show BSI" tick on the timeline. Drives the plough signal. | Ploughing exposes soil before green-up. | — |
| **NDBI** | Built-up index. | Computed per observation. | Meant to flag construction. | No `cleared_or_built` state was observed. |
| **Observation (parcel_obs)** | One index summary of one parcel on one scene date. | Table `parcel_obs`: 382,536 rows after refresh. | The raw time series. | 307 scenes × 1,242 parcels = 381,294 before refresh. |
| **Edge buffer / mixed pixel** | Parcel edges are trimmed by 5 m inward. Tiny parcels use the unbuffered outline and are flagged `mixed_pixel` (confidence capped at 0.6). | Config `seasons.yaml`. | Edge pixels mix with neighbours. | 239 parcels are "structurally small" (D-026). |
| **Whittaker smoothing** | A method that smooths a noisy series and fills gaps. | Applied to each parcel's NDVI over time (λ = 1000, picked by cross-validation). | Cloudy dates leave holes. | — |
| **Gap flag** | A season with a gap longer than 45 days between usable observations. | `gap_flag`; needs ≥ 2 usable observations. | Warns that the seasonal number is thin. | — |
| **Phenology** | The timing of a plant's life cycle: green-up, peak, decline. | Per season: peak, minimum, amplitude, peak date, number of peaks. | Crops have a pattern; bare ground has none. | Peaks need height ≥ 0.45 and prominence ≥ 0.15. |
| **Peak NDVI** | The highest smoothed NDVI in a season. | Feature `ndvi_max`. | A proxy for how green the parcel got. | — |
| **Amplitude** | Peak minus the season's minimum. | Feature used in `vigour_z`. | How strongly it greened up. | Ploughing is only measured where amplitude ≥ 0.15. |
| **Integral** | Sum over days of (NDVI − 0.15), never below 0. Unit: "NDVI-days". | Feature used in `vigour_z`. | How much green, for how long. | — |
| **Green-up** | The start of the rise in NDVI. | Used to date the ploughing window. | — | — |
| **WorldCover** | ESA's 2021 global land-cover map (10 m). | Land-cover fractions per parcel (a prior for the teacher labels). | A cross-check on the model. | — |
| **Teacher / student** | The teacher is a big vision model (Gemini) that looked at chips and curve plots to label 300 parcel-seasons. The student is a small **LightGBM** model trained on those labels and on the seasonal numbers. | The student labels all parcel-seasons (40,986) for free. | Big-model quality at small-model cost. | Validated leave-one-village-out (so no spatial leakage). Scores: `docs/metrics.md`. |
| **LightGBM** | A fast tree-based ML library. | The student model (`data/models/landuse_lgbm_v1.*`). | Small, local, explainable. | — |
| **p_state** | The student's probability for the chosen land-use state. | Column on `parcel_season`; shown as season-state confidence. | Uncertain predictions can go for a second opinion. | — |
| **Control cell** | A ~farmland square in the 2–6 km ring outside the park that was never acquired. | Table `control_cell`: 388 cells. Shown as a map layer. | The "what farms normally do under the same rain" yardstick. | 119,116 control observations. |
| **Satellite refresh** | Fetch scenes newer than the latest and update only the affected parcels. | Overview → "Check for new satellite images". No retraining, no model calls. | Keeps the evidence current. | The first refresh added the 2026-09-26 scene. |

---

## 7. Seasons and land-use states

**Agricultural year (ag_year)**
- *What it is:* the farming year starting in June.
- *In our app:* `ag_year` Y runs from June Y to May Y+1. Column `parcel_season.ag_year`.
- *Why:* the rains that matter fall late in the calendar year.
- *Example:* `2024-rabi` is October 2024 to February 2025.

**Seasons** (`config/seasons.yaml`)

| Season | Months | What it is | Why it matters here |
|---|---|---|---|
| **kharif** | June–September | The south-west monsoon season. | In Thoothukudi's rain shadow, kharif is mostly dry except on irrigated land. |
| **rabi** | October–February | The north-east monsoon (winter) cropping season. | The main rain and crop season here. **Most tests use rabi**, because that is when farms are green. |
| **summer** | March–May | The hot dry season. | Bare-soil signals are cleanest. |
| **annual** | Whole ag-year | An aggregate row in `parcel_season`. | Not shown on the slider. |

**Season names in the UI.** The slider runs `2018-rabi`, `2018-summer`, then `2019-kharif` … `2026-summer` (26 steps). `2018-rabi` is **partial**, because imagery starts in January 2019 (the app says so). The database holds seasons only up to `2026-kharif` (still running); `2026-rabi` and `2026-summer` have no data yet.

**Land-use state** (the student model's label for one parcel in one season). The state values, with counts across all 40,986 parcel-season rows (including the yearly rows):

| State | What it is | Map colour | Count |
|---|---|---|---|
| `cropped` | Green, crop-like seasonal curve. | light green | 16,864 |
| `irrigated_multi` | Green in several seasons, so likely irrigated. | dark green | 782 |
| `perennial_veg` | Steady green (trees, scrub). | very dark green | 1,125 |
| `bare_fallow` | Little vegetation: bare or fallow. | beige | 22,205 |
| `cleared_or_built` | Cleared land or a building. | grey | **0 observed** |
| `water` | Standing water. | blue | **0 observed** |
| `insufficient_data` | Too few clear observations. | pale grey | 10 |

- *Why the two zeros matter:* the model never produced `cleared_or_built` or `water`. This drives two things.
  - Every possessed parcel counts as "idle" in the idle land bank.
  - The caveat "construction smaller than a 10 m pixel is missed" is important.
- *Key finding (D-035):* after the north-east monsoon, almost everything greens (weeds included). So "green after possession" is **not** proof of farming. That is why the DiD test exists.

---

## 8. The DiD panel

The Parcel page shows a table of **difference-in-differences** rows. This section explains it in full. Source: `planet/controls/relative.py`, table `parcel_did` (22,356 rows; 1,242 parcels × 3 events × 3 metrics × 2 scopes).

### 8.1 The idea in three sentences
1. After the north-east monsoon every field turns green, so "is it green?" proves nothing.
2. Instead we ask: **did this parcel change, relative to never-acquired farms next door, after an event?**
3. DiD = (how this parcel differs from the controls after the event) minus (how it differed before the event).

**Difference-in-differences (DiD)**
- *What it is:* a "change in the gap" test. It removes rain, weather and satellite effects that hit everyone equally.
- *In our app:* `DiD = mean over post seasons of (parcel − control mean) − mean over pre seasons of (parcel − control mean)`.
- *Why:* it separates "the parcel behaved differently" from "it rained".
- *Example:* if a parcel was 0.2 above the controls before possession and 0.2 below after, DiD = −0.4.

**Control group**
- *What it is:* 388 cells of never-acquired farmland in a 2–6 km ring outside the park.
- *In our app:* processed through exactly the same feature code as parcels. Map layer "Control-group cells".
- *Why:* they show what farms normally do under the same rain.
- *Example:* 119,116 control observations.

### 8.2 Column by column

| Column | What it means |
|---|---|
| **event** | The moment we split "before" and "after". `t_3_1` = the s.3(1) notification date. `t_award` = the award date. `t_possession` = the possession (LDR) date. |
| **metric** | What is compared. `vigour_z`: greenness strength. `plough`: was ploughing seen (0 or 1)? `plough_z`: how strong the ploughing signal is. Details below. |
| **scope** | Which seasons count. `all` = kharif, rabi and summer. `rabi` = winter seasons only (the cleanest comparison). |
| **n pre/post** | The number of seasons with usable data **before / after** the event (not the number of observations). |
| **DiD** | The estimated change in the gap (see above). Positive = the parcel moved up relative to the controls after the event. Negative = moved down. |
| **95% CI** | A range that would contain the true value 95 times out of 100 (a bootstrap interval, 1,000 resamples). |
| **"—"** | No value could be computed (no post-event seasons with data, or the number is missing). |
| **Amber row** | The UI shades a row when the whole CI is above 0 or below 0. That is "statistically clear", called **significant** here. |

**The three metrics**
- **vigour_z:** for each parcel-season, take the season's amplitude and its integral. Convert each to a *robust z-score* against the controls of the *same* season (`z = (x − control median) / (1.4826 × MAD)`). Average the two. Units are "control spread". About −1 is at the low edge of normal farmland.
- **plough** (0 or 1): 1 if a ploughing-like signal is present. Only seasons with a real green-up (amplitude ≥ 0.15) are scored. The signal is a rise in bare-soil BSI in the 2–6 weeks before green-up, measured as a robust z-score ≥ 2.0.
- **plough_z:** the same ploughing signal as a number, clipped to ±5.

**Event dates in the current data.** The DiD event dates come from **document-level fallback dates** in `planet/events/fallback_dates.yaml` (parcel > village > default), not from each parcel's extracted document date. Examples: default s.3(1) = 2022-10-28; default award = 2024-01-01; default possession = 2025-03-21; Allikulam possession = 2025-02-27. This is why many parcels show *identical* DiD rows. D-056 also records that Melathattaparai|233's extracted possession date is 2024-12-02, not the fallback date.

### 8.3 The five example rows, in plain words

| # | Row | What it says |
|---|---|---|
| 1 | `t_3_1 · plough · all · 3/3 · 0.039 · [-0.04, 0.12]` | Around the s.3(1) notice, 3 seasons before and 3 after had a measurable plough signal. The parcel's ploughing frequency, relative to controls, moved up by about 0.04 on a 0-to-1 scale. The CI includes zero, so **no significant change**. Measured: 54 parcels share exactly this row (same event date, same controls). |
| 2 | `t_3_1 · vigour_z · rabi · 4/4 · 0.374 · [0.02, 0.72]` | Using 4 winter seasons before and 4 after the notice, the parcel became about 0.37 "control spreads" *greener* than the controls. The whole CI is above zero, so the row is amber and **significant**. The lower end is only 0.02, so it is barely so. It is not a sign of trouble; it just says "different". Measured: 3 parcels share this row (for example `Allikulam|40/3A`). |
| 3 | `t_award · plough · all · 5/1 · 0.096 · [0.06, 0.14]` | Around the award, 5 seasons before and only **1** after. Ploughing relative to controls rose by about 0.1 (roughly 10 percentage points) and the CI is above zero, so it is significant. **Caution:** with a single post-award season, the bootstrap cannot vary the post seasons, so the narrow CI probably understates the real uncertainty. (This is my reading of the code, not a measured fact.) 51 parcels share it. |
| 4 | `t_possession · plough · all · 6/0 · — · [—, —]` | 6 seasons before possession had ploughing information, but **none after** (`n post = 0`). With nothing on the "after" side, DiD cannot be computed, so DiD and CI show "—". Ploughing is only scored in seasons with a green-up, so after a recent possession there may simply be no such season yet. 74 parcels show this row. |
| 5 | `t_possession · vigour_z · rabi · 7/1 · -0.205 · [-0.45, 0.04]` | After possession the parcel's greenness fell about 0.2 control spreads below the controls. But the CI reaches +0.04, so it includes zero. It is **not significant**, and only 1 post-possession winter season exists. Compare the "drop" rule in section 9 (a drop needs the whole CI below zero). Examples: `Peroorani|215/5A`, `Umarikottai|184/2`. |

### 8.4 A real parcel: Melathattaparai | 233

Land Delivery Certificate on 2025-03-21 (doc 2207); D-056 notes the extracted possession date is 2024-12-02 (doc 2248).

| event | metric | scope | n pre/post | DiD | 95% CI | Reading |
|---|---|---|---|---|---|---|
| t_possession | vigour_z | rabi | 7/1 | 0.354 | [-0.13, 0.90] | Stays within normal farmland range: **no significant change** in greenness. |
| t_possession | plough_z | rabi | 6/1 | 2.146 | [0.85, 3.70] | Ploughing-like signal **significantly stronger** than controls after handover. |

- *Combined message:* it still behaves like farmland and shows ploughing-like activity. It is a **lead for field verification**, not proof (only one post-possession season). Finding: `PV3_POST_POSSESSION_ACTIVITY`, low severity, confidence 0.55.
- **Significance** here means "the 95% CI excludes 0". It is not a legal or proof standard.

**farmland_like_p**
- *What it is:* the bootstrap share of runs where the parcel's post-event vigour stays at or above −1 control spread.
- *In our app:* stored with the `vigour_z` row. A parcel counts as "farmland-like" when it is ≥ 0.7.
- *Why:* it is the gate for the "still farmed" lead.
- *Example:* 1,143 of 1,242 parcels behave like farmland after possession (see `docs/metrics.md`).

**did_signal (map colouring)**
- *What it is:* a plain summary of the PV3 finding for the parcel.
- *In our app:* four values on the map option "Change vs never-acquired farmland (after possession)": *Vigour dropped vs controls*, *Still farmland-like (lead)*, *No significant change*, *No possession evidence*.
- *Why:* one glance at where the satellite disagrees with the paperwork.
- *Example:* in the DB, 142 parcels have a `t_possession · vigour_z · rabi` CI entirely below zero.

---

## 9. Findings

**Finding**
- *What it is:* an evidence-backed issue the system detected. It is a **signal to verify**, not a legal conclusion.
- *In our app:* table `finding`. There are 1,388, all `status = open`.
- *Why:* they are what a reviewer acts on.
- *Example:* "FMB parcels Melathattaparai|175 and Melathattaparai|176/1A overlap by 0.4592 ha".

**Category**
- *What it is:* the kind of issue.
- *In our app:* eight categories. The UI shows friendly names (Findings page).
- *Example:* `FMB_QUALITY` shows as "Map quality (FMB)".

| Category (UI label) | Count (by severity) | What it is | Rule (from `pipeline/findings/rules.py`) | Example |
|---|---|---|---|---|
| **EXTENT_MISMATCH** ("Extent mismatch") | 485: 21 high, 13 medium, 451 low | The document's extent differs from the map area. | Fires when the closest document value is more than 5% from the map area. High if the difference is ≥ 1 ha or ≥ 50%; medium if ≥ 0.2 ha. | "Document extent x ha vs GIS y ha (+/- ha, %)". |
| **COMPENSATION_MISMATCH** ("Compensation mismatch") | 127: 43 high, 32 medium, 52 low | Amount paid ≠ acres × the document's rate. | The rate is the modal ₹/acre of the same document's rows. High if off by ≥ ₹1,00,000; medium if ≥ ₹10,000. Confidence 0.70 for a parcel, 0.55 for a block. | "Amount ₹14,14,100 ≠ 1.099 ac × ₹9,00,000/ac = ₹9,89,100 (+4,25,000)". |
| **DOC_VERSION_CONFLICT** ("Proposal vs sanction") | 2, both high | Different documents give different village totals. | Fires when sources disagree by more than 0.01 ha. | Ramasamypuram (spread 4.665 ha); Keelathattaparai (spread 2.33 ha). The proposal asks 911.395 ha; the sanction is 904.40 ha. |
| **FMB_QUALITY** ("Map quality (FMB)") | 270: 5 high, 48 medium, 217 low | A drawing problem in the survey map itself. | Overlaps ≥ 0.001 ha, or a parcel outside its survey. Severity by area: ≥ 0.25 ha high, ≥ 0.05 ha medium, else low. Confidence 0.95 (overlap) / 0.90 (outside). | The largest overlap is 0.4592 ha (high). |
| **EXTRACTION_ERROR** ("Extraction error") | 32, all low | A value that is implausible, likely misread. | A document value that is more than 10× or less than 1/10 of the expected value (map area or acres × rate). | "Amount ₹58,87,700 is 21x of 8.411 ac × ₹34,000/ac: likely a misread". |
| **PV1_CLASSIFICATION_CONFLICT** ("Land-class conflict") | 13, all low | The paper's land class disagrees with what the satellite saw before acquisition. | DRY land irrigated in ≥ 2 of the 3 ag-years before s.3(1); WET land never irrigated; poramboke with crops in ≥ 2 seasons. | "DRY land with irrigated_multi in 2 of 3 years before 3(1)". |
| **PV3_POST_POSSESSION_ACTIVITY** ("Change after possession") | 380: 117 medium, 263 low | After possession the parcel behaves differently from controls (a vigour drop), or is still farmland-like. | *Drop:* `vigour_z` rabi CI entirely below 0 → medium, confidence 0.70 (parcel-level) or 0.50 (block-level). *Still farmed lead:* plough CI above 0 and `farmland_like_p` ≥ 0.7 → low, confidence 0.55 (parcel-level). | Melathattaparai|233: "Ploughing-like signal stronger than controls after possession…". |
| **PV4_IDLE_LAND_BANK** ("Idle land") | 79 (one per block): 77 medium, 2 low | Possessed land with no clearing or construction detected. | See section 10. | Allikulam Unit 9 Block 2: 13.4736 ha. |

**About the "PV" names.** PV1, PV3 and PV4 are the "Paper vs Planet" checks. I found no PV2, PV5 or PV6 category in the findings code. The domain skill mentions PV6 (waterbody check) as an idea only.

**Severity (high / medium / low)**
- *What it is:* how large the discrepancy is, by that category's own yardstick (hectares, rupees, or the type of signal).
- *In our app:* used for sorting and filters. It is **not** the same as confidence.
- *Why:* to see the biggest problems first.
- *Example:* an FMB overlap of 0.46 ha is high; 0.03 ha is low.

**"FMB_QUALITY · low"**
- *What it means:* the map has two parcels overlapping by less than 0.05 ha (about 500 m²), or a small spill outside the survey. It is a small drawing flaw, not a legal issue. It matters mainly because overlaps inflate the total area.
- *Example:* Melathattaparai|233 has an overlap of 0.0348 ha with Peroorani|218/6 (low), and one of 0.2095 ha with Peroorani|219/1 (medium).

**Confidence (of a finding)**
- *What it is:* how sure the system is that the finding is real (0–1). It is separate from severity.
- *In our app:* a fixed function of the rule and the evidence level (parcel-level higher, block-level lower).
- *Why:* uncertain findings show visibly lower confidence.
- *Example:* 0.95 for FMB overlaps (pure geometry); 0.55 for a block-level compensation mismatch.

**Status: open**
- *What it is:* the finding's workflow state. All 1,388 are `open`; I found no UI action that changes it.

**Finding evidence level** (different from the parcel's lifecycle evidence level)
- *What it is:* what the finding is about: `parcel`, `block`, `village` or `fmb` (a map-only issue).
- *In our app:* the "Evidence level" filter on the Findings page.
- *Example:* `COMPENSATION_MISMATCH` has 87 block-level findings and 40 parcel-level.

**Evidence pack**
- *What it is:* the proof behind one finding, all in one place.
- *In our app:* opens at the top of the Findings page. It contains the **paper side** (document page, extracted values, red evidence box), the **planet side** (satellite numbers and chips), the checks run, verdict, confidence and caveats. It can be exported as JSON.
- *Why:* nothing should be a bare claim.
- *Example:* the pack for a compensation finding shows the document page, the row, the modal rate and the arithmetic.

**"In simple words" summary**
- *What it is:* a short explanation for non-experts at the start of an evidence pack: what was found, why it matters, how sure we are, what to check next.
- *In our app:* written by an AI model via the router from the finding's own facts (cached). A standard template is used if no model answers.
- *Why:* readable by people who do not know land law.

**Verification report**
- *What it is:* a downloadable PDF and CSV per uploaded document. Owner names are never included.

---

## 10. Idle land bank

**Idle land bank**
- *What it is:* land that the government has taken possession of but which shows no sign of use.
- *In our app:* one finding **per block** (`PV4_IDLE_LAND_BANK`; 79 blocks, total **834.28 ha**). A parcel counts if possession was ≥ 6 months ago and no `cleared_or_built` state was seen since.
- *Why:* to see where land is sitting unused.
- *Example:* the four biggest blocks are between 13.05 and 13.47 ha.

**Columns of the Idle land bank table**
- **ha (`idle_ha_sum`):** sum of the geodesic areas of the block's idle parcels. Overlaps double-count (D-023).
- **n parcels:** how many parcels are in that block's total.
- **min major road km / min substation km:** the *nearest* parcel in the block to a major road / substation, in km.
- **Oldest possession / months since oldest:** how long the earliest possession has been idle.

Severity: ≥ 20 ha high, ≥ 5 ha medium, else low. In our data no block reaches 20 ha; 77 are medium and 2 are low.

**Real example**

| Village | Unit | Block | Idle ha | Parcels | Nearest major road | Nearest substation | Severity |
|---|---|---|---|---|---|---|---|
| Allikulam | 9 | 2 | 13.4736 | 31 | 1.433 km | 4.448 km | medium |
| Peroorani | 5 | 7 | 13.3982 | 8 | 1.955 km | 3.580 km | medium |

**Important limit.** The model never produced `cleared_or_built`, so *every* possessed parcel counts as idle. The number is therefore an upper bound on idle land (D-056), not a measured count of empty land.

---

## 11. The Map page

### 11.1 Layers (right-hand list)
Each layer is fetched from `GET /layers/<name>.geojson`. Defaults: on = FMB parcels, Park boundary, Roads — major, Waterbodies, Substations, Rail. The rest are off.

| Layer | What it is | Why it is there | Data |
|---|---|---|---|
| **FMB parcels (1,242)** | The subdivision polygons; the main layer. Click one to open its Parcel page. | The unit of analysis. | Provided by FarmwiseAI. |
| **Park boundary** | Outline of the whole park. | Context. | Provided. |
| **Roads — major** | Trunk/primary/secondary roads (red, thick). | Access. | Provided (OSM). |
| **Roads — other** | All other roads (orange, thin). | Detail. | Provided (OSM). |
| **Waterbodies** | Tanks (blue). | Water context. | Provided. |
| **Substations** | Electrical substations (purple). | Power access. | Provided. |
| **Rail** | Rail lines (grey). | Access. | Provided. |
| **Schools** | Educational institutions (green). | Context. | Provided. |
| **SIPCOT parks** | 18 SIPCOT park points in the district. | Context. | Provided. |
| **FMB QA issues (overlaps + cross-village)** | Parcels involved in FMB overlaps (view `fmb_qa`; red). | Shows map-quality problems. | Derived by us. |
| **FMB overlaps only** | Overlap parcels only (pink). | Same, narrower. | Derived by us. |
| **Parcels outside their survey** | The 28 parcels that spill beyond their cadastral survey (violet). | Same. | Derived by us. |
| **Control-group cells (2–6 km ring)** | The never-acquired farmland squares used as controls (teal). | Shows what the DiD test is compared against. | Sourced/derived by us. |

**Why a layer shows "(unavailable)".** In `web/src/components/map/MapView.jsx`, each layer's data is requested when the map opens. If that request **fails** (server error, missing table or view, or, in the read-only cloud build, a layer that was not exported in the snapshot), the layer is recorded as failed. Its tick box is greyed out and disabled, the label gets "(unavailable)", and hovering shows the error text. If *all* layers fail, the page shows "No layer could be loaded — is the API running?". **Uncertain:** I could not tell from the code which specific layers are unavailable in a given deployment; it depends on what the API or snapshot serves.

### 11.2 Colour by (top-left drop-down)

| Option | What it is | Values / colours | Time-aware? |
|---|---|---|---|
| **None (single colour)** | All parcels one blue. | — | No |
| **Acquisition stage (today)** | The furthest legal stage reached now (`current_stage`). | Stage codes (section 1). | No |
| **Acquisition stage by season (slider)** | The furthest stage reached **by the end of** the chosen season (`stage_at_season`; `EXEMPTION` events ignored). | `NOT_STARTED`, then the stage codes in order. | Yes |
| **Finding count** | Number of open findings on the parcel. | Sequential scale. Severity/category needs the Parcel page. | No |
| **Land-use state (season slider)** | The student's state for that parcel-season. | Section 7 colours. | Yes |
| **Change vs never-acquired farmland (after possession)** (`did_signal`) | PV3 summary. | *Vigour dropped vs controls*, *Still farmland-like (lead)*, *No significant change*, *No possession evidence*. | No |
| **Document link level** (`match_status`) | How exactly documents point to the parcel. | *Parcel-level*, *Survey-level*, *Block-level only*, *No documents linked*. | No |

**Season slider.** Steps through `2018-rabi` → `2026-summer` and can autoplay. It recolours only the two time-aware options. For all others the app says the colouring does not change over time.

---

## 12. Caveats shown in the app

These strings are attached to findings and shown as "Keep in mind" (source: `pipeline/findings/rules.py`).

| Caveat text | Plain-language meaning | Why it is there |
|---|---|---|
| **"area is the geodesic sum of FMB parcels (overlaps double-count, D-023)"** | The hectares are simply the sum of the parcels' areas. Where two parcels overlap, that ground is counted twice, so the total is slightly too high (7.3 ha double-counted park-wide). | Honest area reporting. Decision D-023 also withdrew the claim that the map exceeds the sanction. |
| **"no cleared_or_built season state observed; construction smaller than a 10 m pixel is missed"** | The satellite model never saw a cleared or built parcel, and anything smaller than one 10 m pixel cannot be seen. So "idle" may include sites with small works. | Prevents over-claiming. |
| **"single post-possession season (lead, needs field verification)"** | Only one season after possession has been observed, so the result is a lead, not proof. | Short history. |
| **"10 m Sentinel-2 pixels mix neighbouring land cover at parcel edges"** | Parcels are small, so pixels blend with neighbours. | Mixed pixels. |
| **"NE-monsoon weed flush greens fallow land (D-035)"** | After the rains weeds make idle land look green, so greenness alone is not farming. | Why the DiD test exists. |
| **"clouds/haze and observation gaps"** | Missing or hazy satellite dates make numbers noisy. | Data quality. |
| **"possession evidenced only by a block-level document (no survey number on it, D-052)"** | The paper proves possession for a block, not for this exact parcel. | Confidence lowered. |
| **"classification wording in the award may follow the revenue record, not current use"** | The paper's "dry/wet" label may be outdated. | PV1 caveat. |
| **"irrigated_multi is a model state (student/teacher, P3)"** | It is a prediction, not an observation. | PV1 caveat. |
| **"FMB polygons are digitised sketches; overlaps and slivers affect area (D-023)"** | The map is hand-drawn, so areas differ from paper. | Extent caveat. |
| **"co-owner rows may each print a share; the closest of row / document-sum is used"** | Several owners of one parcel may each list a share, so the check uses whichever fits best. | Extent caveat. |
| **"rate inferred as the modal ₹/acre of the same document's rows"** | We take the most common rate in that document as the notified rate. | Compensation caveat. |
| **"rows may include tree/structure compensation not itemised"** | A higher payment may include trees or buildings. | Compensation caveat. |
| **"the document's own arithmetic may be wrong (§11b)"** | The paper itself can contain an error. | Compensation caveat. |
| **"a proposal page is not a sanction: read each source's role before concluding"** | A proposal is only a request. | DOC_VERSION caveat. |
| **"hectare notation parsed from OCR/VLM (e.g. 106.01.01 vs 106.01.00)"** | A digit may have been misread. | DOC_VERSION caveat. |
| **"geodesic area of the geometric intersection (D-023)"** | The overlap area is measured on the Earth's curved surface. | FMB caveat. |
| **"the cadastral layer is at survey level and has its own digitising error"** | The reference map we compare to is not perfect either. | FMB caveat. |
| **"review the extraction before any land/compensation finding"** | The value is probably misread, so a person should check. | EXTRACTION_ERROR caveat. |

---

## 13. AI and system terms

| Term | What it is | In our app | Why | Example |
|---|---|---|---|---|
| **Model router** | Our single gateway for every AI call (`app.router.call(...)`). It picks the model, logs why, checks the JSON reply and falls back. | Models & routing page; `data/router_log.sqlite`. | We may not call an AI SDK directly. It enforces free models, privacy and cost. | The "why?" button on an agent step shows models considered, filtered out (and why), and scores. |
| **Task chain** | The ordered list of models tried for a job. | Planner: Gemini → gpt-oss → Qwen → Cohere. | If one fails, the next is used. | — |
| **Fallback** | Automatically using the next model when one fails. | `fallback` events on the agent's stream. | Keeps the app working during outages or quota limits. | A 429 (rate limit) makes it switch model. |
| **Circuit breaker** | After 3 failures in a row, skip a model for 60 s. | Router. | Avoids hammering a broken model. | — |
| **Chaos** | A top-bar switch that pretends a provider is down (`ROUTER_CHAOS=gemini:down`). | Demo of fallback. | Proves fallback works. | Turn on chaos, ask a question, watch the planner switch to another model. |
| **Privacy tiers** | How sensitive a payload is: `PUBLIC` (no personal data), `PSEUDO` (names replaced by tokens like `⟨OWNER_17⟩`), `PII` (contains owner data). | Enforced in the router. | Models on free tiers may train on inputs. | `PII` goes only to models with `trains_on_free_tier: false` (Bedrock, Groq). The router log shows 0 of 14,202 PII-tier calls went to a training model. |
| **Fails closed** | If a provider's training policy is unknown, treat it as "trains". | Router rule. | Safe by default. | — |
| **Pseudonymisation gateway** | Swaps names, patta numbers and amounts for tokens before a training-tier model sees them. | Router + view `v_owner_pseudo`. | Allows use of models like Gemini safely. | — |
| **Demo mask** | A top-bar toggle, ON by default, that hides owner names (`Owner ••17`). | Client-side backstop over server masking. | Safe to present. | — |
| **Bedrock** | Amazon's hosted-AI service. | Ministral 3 8B (table reading), Ministral 3B (classification), Titan (embeddings). Region ap-south-1. | AWS does not train on customer data, so it can see owner data. | The main table reader. |
| **Groq** | A fast hosted-model provider (free tier). | Text-to-SQL, JSON repair, vision fallback. Models: Qwen, gpt-oss-120b. | No training on inputs. | — |
| **Gemini** | Google's models (free tier; flash-lite). | Planner, judge, narrative, and satellite chip **teacher**. | Strong reasoning and vision. It trains on inputs, so it only sees public or pseudonymised data. | — |
| **Cohere** | A different AI vendor (Command A, Command A Vision). | Critic, judge fallback, second vision opinion. | A different vendor makes different mistakes. | — |
| **Shadow cost** | What a call would cost at list price. | Models page; separate from actual cost. | Shows value even when calls are free. | Agent eval: actual $0, shadow $0.31. |
| **Agent** | The pipeline that answers a question: intake → planner → tools → verifier → critic → judge → presenter. | Agent console. | Answers should be checked, not just generated. | — |
| **Planner** | Turns a question into a JSON step plan. | Shown in "Technical details". | Transparent steps. | — |
| **Tool** | A function the agent may call (guarded SQL, spatial query, findings query, evidence pack, timeline…). | Listed per step. | Facts come from data, not from the model's memory. | SQL is SELECT-only on a read-only role with a 5-second limit. |
| **Claim** | One statement in the answer that can be checked, with an id. | The answer's numbers and sentences are claims. | Each claim gets a verdict. | — |
| **Verifier** | Deterministic checks on claims (re-run SQL, recompute areas, arithmetic, "value appears inside its evidence box"). | "Automatic checks passed" card. | Catches errors without a model. | — |
| **Critic** | A model from a *different vendor* that proposes a testable reason a claim is wrong. | "Second opinion" card; the system runs its test. | Reduces shared blind spots. | 24 of 24 challenges came from another vendor in our eval. |
| **Judge** | Decides the verdict per claim: `ACCEPT`, `DOWNGRADE`, `REROUTE` or `REVIEW`, with confidence. | "Final verdicts" card. | Unproven claims are visibly downgraded, never silently kept. | KPI status: verified / downgraded / review. |
| **Ledger** | A tamper-evident log of every agent step, chained by SHA-256 hashes. | "Verify ledger" button; table `agent_ledger`. | Proves nothing was altered afterwards. | `make verify-ledger RUN=…`. |
| **SSE** | Server-sent events: the live progress stream from server to browser. | Progress timeline. | See the agent working. | — |
| **Domain pack** | The land-specific part (tools, prompts, checks) plugged into a generic agent. | `land_acquisition` (built); `agri_claims` (scaffold only). | Reusability. | — |
| **Read-only mode** | The cloud build only browses. Uploads, the live agent and review actions are not available. | AWS Lambda + S3 snapshot. | The event rules allowed no database server for that build. | — |
| **Snapshot** | Pre-computed read-only API answers stored in S3. | Serves the cloud demo. | Fast and cheap. | 28,128 files. |

---

## 14. Cheat sheet

| Term | One-line meaning |
|---|---|
| Acquisition | Government buys land for the park; owner gets compensation |
| SIPCOT | Tamil Nadu industrial-parks agency; the acquirer |
| GO_AS_LPS | Stage 0: sanction papers (904.40 ha sanctioned) |
| SEC_3_1 | Stage 1: gazette notice "land will be acquired" |
| SEC_3_2 | Stage 2: notice to owners (plus errata) |
| EXEMPTION | Land released from acquisition; must not have award/payment |
| PRICE_NEGOTIATION | Committees fix ₹/acre per block |
| POSSESSION_NOTICE | Form E: "surrender within 30 days" |
| AWARD | 7(2) consent, 7(3) final, Form F per-owner split |
| PAYMENT | Compensation paid or deposited in court |
| POSSESSION | LDR: land handed to SIPCOT |
| MUTATION | Patta/chitta changed to SIPCOT |
| LDR | Land Delivery Receipt/Certificate |
| Form C / E / F | Notification / possession notice / compensation split |
| DLPNC / SLPNC | District / State price committees |
| Errata | Correction of an earlier notice |
| Chitta / Patta | Ownership record and its extract |
| Poramboke | Government land |
| Punsey / Nansey | Dry / wet land |
| Village / Unit / Block | 7 villages; Units 1–…; Blocks 1–10 |
| Survey number / Subdivision | Field number / part of a field (177/4A) |
| KIDE | FMB key "survey/subdivision" |
| parcel_uid | `Village\|KIDE`, our unique key |
| FMB | Field Measurement Book map: 1,242 subdivision polygons |
| Cadastral map | Whole-survey polygons (396) |
| Extent (ha, ac) | Area stated by a document |
| H.AA.SS | Hectare.are.square-metre notation |
| Geodesic area | Area on the curved Earth (all ours) |
| UTM 44N | Flat metric projection for distances only |
| Dissolved union | Merged polygons, overlaps counted once (901.50 ha) |
| Inside park boundary | Parcel sits inside the park polygon (all 1,242) |
| Evidence level | How exactly papers point to a parcel: parcel / survey / block / none |
| Stalled | Stuck between steps (award not paid, possession not mutated) |
| Extractor / engine | text_layer, tesseract, vlm:primary, vlm:second |
| bbox_method | grid_row (best) / grid_table / page |
| Confidence | 0–100% certainty in a row or finding |
| self_consistency | pass / fail / n/a: does the page's arithmetic add up |
| auto / queued | Accepted by checks / waiting for a person |
| Second read | Re-read of a page when checks fail |
| survey_not_serial | Catches row numbers misread as survey numbers |
| Review queue | Human approval of uncertain pages (1,302) |
| Sentinel-2 | Free 10 m satellite images every ~5 days |
| NDVI / BSI / NDWI / NDBI | Greenness / bare soil / water / built-up indices |
| Chip | Small satellite image cut-out of a parcel |
| Whittaker smoothing | Smoothing and gap-filling of a time series |
| Kharif / Rabi / Summer | Jun–Sep / Oct–Feb / Mar–May |
| ag_year | Farming year starting in June |
| cropped, bare_fallow, … | Season land-use states; `cleared_or_built` and `water` never observed |
| Teacher / student | Gemini labelled 300 examples; LightGBM labels the rest |
| Control group | 388 never-acquired farm cells 2–6 km outside the park |
| DiD | Change in the parcel-vs-control gap, after vs before an event |
| event t_3_1 / t_award / t_possession | The split date used |
| vigour_z / plough / plough_z | Greenness strength / ploughing 0-1 / ploughing strength |
| n pre/post | Seasons with data before/after the event |
| 95% CI | Range for the true value; excluding 0 = significant |
| "—" in DiD | No value computable (for example no post-event seasons) |
| Finding | Signal to verify; not a legal conclusion |
| Severity | Size of the discrepancy (high / medium / low) |
| Evidence pack | Paper side + planet side + checks + verdict |
| FMB_QUALITY | Overlaps or spills inside the survey map |
| EXTENT_MISMATCH | Paper extent ≠ map area by > 5% |
| COMPENSATION_MISMATCH | Amount ≠ acres × rate |
| DOC_VERSION_CONFLICT | Proposal vs sanction totals differ |
| EXTRACTION_ERROR | Implausible value, likely misread |
| PV1_CLASSIFICATION_CONFLICT | Paper land class ≠ satellite history |
| PV3_POST_POSSESSION_ACTIVITY | After possession the parcel differs from controls (or looks farmed) |
| PV4_IDLE_LAND_BANK | Possessed land with no clearing seen, per block (834.28 ha) |
| Model router | Single gateway for every AI call |
| Privacy tiers | PUBLIC / PSEUDO / PII |
| Critic / judge / verifier | Other vendor challenges / decides / deterministic checks |
| Ledger | Hash-chained, tamper-evident agent log |
| Chaos | Pretend a provider is down to show fallback |
| Demo mask | Hides owner names |
| Bedrock / Groq / Gemini / Cohere | AWS / fast free host / Google / second vendor |

---

## 15. Things not fully confirmed

- **38 documents with no classified type.** I did not confirm why (unclassified, or zip members).
- **Doc type `GO`.** It is in the taxonomy, but I found no document with that type. GO No.100 content lives inside `LPS.pdf`.
- **Which layers show "(unavailable)" in a given deployment.** It depends on the API or snapshot at run time.
- **Whether the UI shows `missing_mandatory`.** It is in the lifecycle view, but I did not check the UI.
- **Finding `status`.** All 1,388 are `open`. I did not find where (or whether) it changes.
- **Exact pixel rule for the five example DiD rows.** The rows are reproduced from the live `parcel_did` table. My "CI understates uncertainty with 1 post season" remark is an inference from the bootstrap code (post seasons are resampled within a set of size 1).
- **Doc/code differences** worth fixing:
  - `pack.py` assumption text says "Stalled = 90 days"; the config uses 60 / 30 / 90 days by transition.
  - The FMB geodesic total is written as 908.51 ha in the skill file (table) but 908.82 ha in the same file's text and in D-023. I used 908.82.
  - Chapter 07 and D-045 say 143 parcels show a significant vigour drop; the live table has 142 with `t_possession · vigour_z · rabi` CI entirely below 0. This might be a filter or refresh difference.
  - Melathattaparai|233 has a `MUTATION` event dated 2023-01-01, earlier than its award and possession events, and its "stage entered" is therefore 2023-01-01 (1,367 days in stage). I did not investigate the source document.
