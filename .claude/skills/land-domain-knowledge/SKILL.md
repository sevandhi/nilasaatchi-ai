---
name: land-domain-knowledge
description: Domain reference for the Allikulam SIPCOT land-acquisition dataset — acquisition stages under TN Act 10/1999, document types and folder mapping, Tamil vocabulary, extent/area unit parsing, survey-number and village-name normalisation, known ground-truth totals and known data defects. Load before writing any extractor, normaliser, matcher, SQL, discrepancy rule, prompt or test that touches land documents or parcels.
---

# Land Domain Knowledge — Allikulam SIPCOT Park (Thoothukudi)

This is the dataset's ground truth, taken from direct inspection on 2026-09-26. Treat it as authoritative. If data contradicts this file, record the contradiction in `docs/decisions.md` and escalate. Never quietly "fix" it.

## 1. What the project is
- **Scheme:** SIPCOT "Allikulam Oil Refinery Industry Formation" industrial park, Thoothukudi district and taluk, Tamil Nadu. Files are organised into **Units** (Unit-4, Unit-7, and so on) and **Blocks 1–10**.
- **Law:** Tamil Nadu Acquisition of Land for Industrial Purposes Act, 1997 (TN Act 10 of 1999), plus private negotiation under the TNALIP/TNLA revival rules.
- **Authorising orders:**
  - G.O.(Ms) No.100, Industries (SIPCOT-LA) Dept, dated 26.02.2021.
  - Administrative Sanction (AS) letter Lr.No.LA/TUT/Allikulam/2020, dated 31.03.2021.
  - Land Plan Schedule (LPS).
- **7 villages:** Allikulam, Keelathattaparai, Melathattaparai, Umarikottai, Peroorani, Ramasamypuram, South Silukanpatti.

## 2. Acquisition lifecycle (the canonical stage order)
| # | Stage code | Folder(s) | Meaning |
|---|---|---|---|
| 0 | `GO_AS_LPS` | Government Order (GO), Administrative Sanction (AS), Land Plan Schedule (LPS) | The government sanctions the scheme and village-level extents |
| 1 | `SEC_3_1` | `3(1) Publish`, `3(1) Published` | Form C gazette notification under s.3(1): lands to be acquired |
| 2 | `SEC_3_2` | `3(2) Notice`, `3(2) Noticed`, `3(2) Errata` | Notice to owners and interested persons. Errata correct earlier notices |
| 3 | `EXEMPTION` | `Exemption Initiated`, `Exemption GO Approved` | Lands proposed for, or granted, exemption from acquisition |
| 4 | `PRICE_NEGOTIATION` | `DLPNC`, `SLPNC`, `Land Value Fixation`, `Funds-Allocated` | District/State Level Price Negotiation Committee sets the land value (for example ₹5,00,000 per acre), then funds are allotted |
| 5 | `POSSESSION_NOTICE` | `Form E` | Notice under s.4(2): surrender possession within 30 days |
| 6 | `AWARD` | `7(2) Award`, `7(2) Awarded`, `7(3) Awarded`, `Form F` | s.7(2) is a consent/negotiated award. s.7(3) is a final award when there is no consent. Form F is the per-owner compensation apportionment |
| 7 | `PAYMENT` | `Amount Disbursed*`, `Court Deposit` | Compensation paid (demand drafts or bank transfers), or deposited in court when the case is disputed or the owner is unknown |
| 8 | `POSSESSION` | `LDR Issued`, `Possession Taken` | Land Delivery Receipt or Certificate: land handed to SIPCOT |
| 9 | `MUTATION` | `Patta Transferred` | Chitta/patta (e-services extract) now shows SIPCOT as owner |

**Lifecycle rules** (used by the discrepancy engine):
- A parcel cannot reach stage N without evidence of the earlier mandatory stages 1, 2, 6 and 8.
- A parcel that is "Awarded but not paid", "Paid but no possession" or "Possession but no mutation" is **stalled**. Flag it with the number of days since the last event.
- A parcel with an exemption event must not have award or payment events. If it does, that is an `EXEMPTION_CONFLICT`.

## 3. Folder label ≠ truth
- 3,212 PDFs, of which 2,361 are unique by MD5. That means 851 are byte-identical duplicates, and 186 duplicate groups span two or more folders.
- Some files are misfiled. For example, `FformBlock42SurNo406.pdf` sits in `7(2) Award`, and `DLPNC.pdf` sits in `Exemption GO Approved` and in `SLPNC`.
- **Gazettes are multi-notification files (corrected 2026-09-26).** `3(1) Published/527_Gazette_Block_1.pdf` (byte-identical to `31Published_Gazette_Block_1.pdf`) contains: p1 an unrelated Chennai railway errata; pp2–4 a s.3(1) notice for the **Allikulam scheme** (Ramasamypuram, Unit 2 Block 1, 10.2300 ha); and from p4 a *SIPCOT Solar Power Plant* notice (Tirunelveli, Manur taluk, Chittarchatram, Unit 02 Block 02, 9.75 ha). Other gazettes (No.527 block 8, No.525) also carry unrelated notices on p1. **Scheme relevance is per page segment (notification), not per file.** The project is also called "Formation of Oil Refinery Project by SIPCOT" (Gazette No.36, 24.01.2025).
- Therefore: `doc_type` = content classifier result. The folder name is only a prior. Keep `folder_label` and `classified_type` as separate columns, and flag any disagreement between them. In the 100-document eval set, **33% disagree**; the "Amount Disbursed" folders match 1 of 17 (award proceedings, DDs to the District Judge); DLPNC folder files are all SLPNC material.
- **Taxonomy rules (D-025):** `doc_type` describes the document's **form**; the acquisition **stage** it evidences is a separate field. For example, a demand draft payable to the Principal District Judge is doc_type BANK_INSTRUMENT with `payee_kind=court` and stage PAYMENT/court deposit. Committee level (district/state) is a field on price-negotiation documents. Classification output is **page-segmented** (a file can hold several documents or notifications). Extra classes: AS_PROPOSAL, CALCULATION_SHEET, CLASSIFICATION_ORDER, CORRESPONDENCE; COVERING_LETTER is dropped (those files are substantive letters: classify by content).

## 4. Formats and OCR reality
- About 12,564 unique pages in total. Form F (about 4.0k pages), 7(2) Awarded (about 1.7k), 3(2) Notice (about 1.35k), Form E (about 1.1k) and 7(3) Awarded (about 1.0k) dominate.
- **Digital text layer present:** most of `3(1) Publish/Published` (English gazettes), `Patta Transferred` (Tamil chitta e-service printouts from eservices.tn.gov.in), and `Possession Taken` (English, with a noisy OCR layer). Everything else is scanned. Always try `pdftotext` first; accept the text layer only if it contains at least 50 non-space characters per page and passes a garbage-ratio check.
- **Tesseract `tam+eng` (v5.3):**
  - Reads Tamil prose and headers well: village, unit, block, gazette number and dates.
  - **Fails on table cells** (survey numbers and extents come out garbled) and on **handwriting**. Form E owner names and plot numbers are often handwritten.
  - So table regions must be routed to a VLM, and handwriting must be routed to VLM plus human review.

## 5. Units and number parsing (critical — most bugs will be here)
- **Hectare triple notation `H.AA.SS`:** `0.95.50` = 0 ha 95 are 50 m² = **0.9550 ha**. `10.30.50` = 10.3050 ha. `904.40.0` = 904.40 ha.
- **Chitta notation `H - AA.SS`:** `0 - 53.50` = 0.5350 ha.
- **Acres:** 1 ha = 2.47105 acres exactly, **but most documents convert with 2.47** (the AS letter prints 186.455 ha as 460.54 ac; 2.47105 would give 460.74). Some truncate instead of rounding. The ha↔ac check must accept either factor: |ac − ha×2.47| ≤ 0.02 **or** |ac − ha×2.47105| ≤ 0.02 (scaled tolerance for extents over 10 ha: 0.1%).
- **Cents (சென்ட்):** 1 cent = 0.01 acre = 40.4686 m². Form E uses cents.
- **Money:** Indian grouping `₹12,60,000/-` = 1,260,000. Keep integer rupees.
- **Compensation check:** amount ≈ extent_acres × rate_per_acre (DLPNC/SLPNC rate, for example ₹5,00,000). Example: 2.52 ac × 5,00,000 = 12,60,000 ✓.
- **Tamil numerals** ௦௧௨௩௪௫௬௭௮௯ map to 0–9. OCR may also emit ௧ for "க". Normalise only inside numeric fields.
- **Dates:** `dd.mm.yyyy`, `dd/mm/yyyy`, or Tamil month names. Normalise to ISO. Sanity range: 2020-01-01 to 2026-12-31.

## 6. Survey numbers
- Canonical key: `village_id | survey_no | sub_div`. For example, `Keelathattaparai|177|4A`.
- Patterns: `173/1`, `177/4A`, `179/1A`, `18/3G`, `6,7,8,10A` (lists), `224/2`, and `172/1 மற்றும் 172/2` (Tamil "and").
- `புல எண்` = survey no. `உட்பிரிவு` = subdivision.
- The FMB layer has `KIDE` = `survey_no/sub_div`, or just `survey_no` when there is no subdivision (157 rows). The cadastral layer is at survey level only.
- Resolution order: exact KIDE → survey + subdivision with case/space normalisation → parent survey (subdivision missing in the document) → fuzzy match inside the same village and block.
- **Never match across villages.**

## 7. Village name aliases (seed; extend with evidence)
| Canonical | Variants seen or expected |
|---|---|
| Allikulam | அல்லிகுளம் |
| Keelathattaparai | Keel Thattaparai, கீழத்தட்டப்பாறை |
| Melathattaparai | Mela Thattaparai, Melathataparai, மேலத்தட்டப்பாறை |
| Umarikottai | உமரிக்கோட்டை |
| Peroorani | Perurani, பேரூரணி |
| Ramasamypuram | இராமசாமிபுரம் |
| South Silukanpatti | தெற்கு சிலுக்கன்பட்டி |

The district appears as both "Thoothukudi" and "Thoothukkudi" (the FMB layer uses the latter).

## 8. Key Tamil vocabulary
நில உரிமையாளர் = landowner; பட்டா எண் = patta no; விஸ்தீரணம் = extent; ஹெக்டேர் = hectare; ஏக்கர் = acre; புன்செய் = dry land; நன்செய் = wet land; புறம்போக்கு = poramboke (government land); வகைப்பாடு = classification; இழப்பீட்டுத் தொகை = compensation; தீர்வை = assessment; கிராமம் = village; வட்டம் = taluk; அலகு = unit; பிளாக் = block; மனைவி = wife of; மகன் = son of; க/பெ = husband's name; த/பெ = father's name.

## 9. Ground-truth totals (use as verifier anchors)
**Village totals in hectares**

| Village | GO No.100 = English AS letter in LPS.pdf p1 (patta + poramboke = total) | **Tamil proposal** (AS.pdf p2) | FMB layer (computed) | Cadastral layer (computed) |
|---|---|---|---|---|
| Allikulam | 185.96.5 + 0.49.0 = 186.45.5 | 186.45.50 | 186.77 | 188.92 |
| Keelathattaparai | 189.64.5 + 3.50.5 = 193.15.0 | **195.48.00** | 196.08 | 204.00 |
| Melathattaparai | 173.21.5 + 2.62.0 = 175.83.5 | 175.83.50 | 174.26 | 177.11 |
| Umarikottai | 106.01.0 (GO prints 106.01.01, a probable typo; the column only sums with .00) | 106.01.00 | 105.56 | 107.49 |
| Peroorani | 140.77.0 + 0.29.0 = 141.06.0 | 141.06.00 | 141.92 | 142.68 |
| Ramasamypuram | 95.23.0 | **99.89.50** | 97.11 | 97.70 |
| South Silukanpatti | 6.66.0 | 6.66.00 | 6.82 | 17.40 |
| **Total** | **904.40 ha (2,233.86 ac)** | **911.39.50 ha (2,251.14 ac)** (proposal, not sanctioned) | **908.51** | **935.30** |

- The park boundary polygon is 934.32 ha.
- **Correction (2026-09-26, golden-set labelling):** the English **Administrative Sanction letter** (Lr.No.LA/TUT/Allikulam/2020, 31.03.2021, in `LPS.pdf` page 1) states **904.40.0 ha**, the same as the GO. The **911.39.50 ha** table is a *Tamil proposal* page in `AS.pdf` (page 2, ending சமர்ப்பிக்கிறேன் = "I submit"), and that page's text contradicts its own table.
- **Known real discrepancies (demo gold):**
  - **Proposal vs sanction:** the Tamil proposal asks for 911.395 ha; the GO/AS sanction 904.40 ha. The differences sit in Keelathattaparai (+2.33 ha) and Ramasamypuram (+4.665 ha). The proposal text explains part of it: Keelathattaparai poramboke 1.54.0 ha higher and patta 1.54.0 ha lower, and a ±0.09.5 shift in Peroorani.
  - ~~GIS vs sanction~~ **withdrawn (D-023):** the FMB sum (908.82 ha geodesic) is inflated by **366 overlapping parcel pairs (7.3 ha double-counted)**; the dissolved union is **901.50 ha**, 2.9 ha *under* the sanction. Do not claim the map exceeds the sanction.
  - **FMB quality findings (P1):** 366 overlaps (110 cross village lines; largest 0.46 ha, Melathattaparai|175 × 176/1A); 28 parcels extend > 100 m² beyond their cadastral survey (1.80 ha, geodesic); self-intersecting Keelathattaparai|443/1; Excel-mangled subdivision Allikulam|16/3-4-5 stored as "38415" (use KIDE); null block for Allikulam|46; unit/block disagreements with cadastral (Allikulam 10, 46; Keelathattaparai 341, 357); 7 non-numeric Land_ids.
  - The cadastral layer holds *whole* survey polygons. The FMB layer holds only the acquired subdivisions. Example: South Silukanpatti is 17.40 ha cadastral against 6.82 ha FMB against 6.66 ha in the documents.
- **Areas (D-023):** report hectares as **geodesic** (`ST_Area(geom::geography)`) to 4 dp. UTM 44N planar areas are inflated ~+0.22% because the park is 3° off the central meridian. Village and park totals use the **dissolved union**, with the sum shown only alongside it. Use EPSG:32644 for distances, buffers and overlays only. (§9's FMB/cadastral columns are geodesic sums.)
- **Waterbodies:** no parcel intersects a tank (nearest 5.2 km outside), so PV6 must rely on JRC occurrence/NDWI.

## 10. GIS layers (all CRS84 / EPSG:4326)
- `Park_fmb_Map.geojson`: 1,242 subdivision polygons. Fields: `Land_id, dist_name, taluk_name, vil_name, unit_id, block_id, survey_no, sub_div, KIDE`. **This is the matching target.**
- `Park_Cadastral_Map.geojson`: 396 survey polygons. Fields: `dist_name, taluk_name, vil_name, unit_id, block_id, survey_no`. The set of (village, survey) pairs is identical to FMB.
- `Park_Boundary.geojson`: 1 polygon (Allikulam park).
- `Thoothukudi_Parks.geojson`: 18 SIPCOT park points in the district.
- `Basic_GIS_Layers/`:
  - Road_network: 37,590 OSM lines, `fclass`/`ref`.
  - Railway_network: 511 lines.
  - Railway_Stations: 37 points.
  - Waterbodies: 185 tank polygons, `Tank_name`.
  - SubStations: 36 polygons, `voltage`.
  - Airport: 1 polygon.
  - Seaport: 2 points.
  - Educational_Institution: 63 points.
- Rasters are **not provided**. The Task 2 guide requires participant-sourced DEM, slope and elevation (see the phase-1 skill).

## 11. Project timeline (for satellite event windows)
- GO No.100: 26.02.2021. English AS letter (in LPS.pdf): 31.03.2021.
- s.3(2) notices: from about 18.12.2021 (errata 21.08.2022).
- s.3(1) gazettes: 28.10.2022, 03.11.2022, 16.11.2022, 24.11.2022 and 28.11.2022 (Gazette No. 503/506/527 …).
- DLPNC meetings: **per block**, e.g. 01.02/24.08, 24.02/23.08, 09.08 and 17.02/17.08.2023. Errata dated both 21.08.2022 and 23.08.2022.
- Awards and Form E: 2024 to early 2025 (for example A7/10/Unit-7-Block-9/2024).
- Land Delivery Certificates: for example Melathattaparai Block 2, surveys 233/235, handed over on 21.03.2025. Allikulam Block 1: proceedings dated 27.02.2025.
- These are examples read from sample pages. The per-parcel dates come from P2 extraction.

## 11b. Golden-set findings (2026-09-26, `eval/golden/README.md`)
- **Rates are per block**, e.g. ₹5,00,000/acre in one block and ₹9,00,000/acre in another. In one block the printed amount (₹95,64,800) does not equal acres × rate (₹94,68,000). Always take the rate from the same block's DLPNC/SLPNC or award.
- **Hectare notation:** the last group is not always 00/50 (3.96.32, 0.30.60, 1.50.20). OCR variants seen: "185.96.5", "104.31 .00", "0:10.50".
- **Survey patterns:** a subdivision can itself be a list ("1 / 6,7,8,10A" is one parcel entry). Text can run together: "173/20.86.00" = survey 173/2 + extent 0.86.00.
- **Gazette No.527** (28.11.2022) *is* cited for Allikulam lands, not only the solar project. Form E and Form F for the same owner can cite different gazettes (506 on 03.11.2022 vs 527 on 28.11.2022).
- **Proceeding numbers are batch IDs:** different owners' Form E can share one number. Never use doc_no as a unique key.
- **Dates with a blank day** (".02.2025") are common: store month precision with `date_precision`.
- **Folder ≠ content:** "Amount Disbursed" holds award orders and court-deposit demand drafts; "LDR Issued" and "Possession Taken" hold the same Land Delivery Certificate form (treat as one type, LDR).
- A 7(3) award page may tabulate lands *already paid under 7(2)*. Don't read those rows as 7(3) lands.
- One award page prints a block total as both 10.31.50 and 10.30.50. The notice rows confirm 10.31.50. Self-consistency failures can be the document's own error.
- Demand drafts carry no survey, owner or village; link them only by amount and date.
- **Held-out set findings (2026-09-26, eval/heldout/README.md):** real document errors the verifier must detect: amount-in-words ≠ figures (₹25,48,000 vs ₹25,41,000; ₹85,527 vs ₹53,771); column sum ≠ printed total (₹27,53,600 vs ₹27,60,700); ha→ac pairs fitting neither factor (0.295 ha printed as 0.726 ac); duplicate serial numbers and blank rows; a third per-block rate (**₹7,00,000/acre**, Unit 7 Block 5; ₹9,00,000/acre recurs); Peroorani appears under both Unit 4 and Unit 5; some 7(3) pages carry two hectare columns. Bank account numbers appear on treasury tables: **never extract or store them** (keep IFSC/bank name only). Cross-document duplicates (chitta ↔ award, LDR ↔ chitta) are useful for matching tests.
- **Extra vocabulary:** தொகுதி = block; புஞ்சை = dry land; வீட்டுமனை = house site (Form E: plot numbers, extents in cents); (பகுதி) = part of a survey; வாரிசுதாரர்கள் = legal heirs. Village spellings: அல்லிக்குளம், கீழதட்டப்பாறை, Perurani.

## 12. Satellite facts (from the spike; see docs/spikes/sentinel2-spike.md)
- Sentinel-2 tile **43PHK** (UTM 43N, EPSG:32643) covers the park. There are 587 unique acquisitions from 2019 to 2026 (810 raw STAC items incl. reprocessed baselines; 122 unique with < 20% cloud), sparse in Jul–Nov (monsoon).
- The land is rainfed dry land (புன்செய்). NDVI peaks Dec–Jan after the NE monsoon (≈0.7) and falls in the dry season (≈0.2–0.4).
- Prosopis juliflora scrub (சீமைக் கருவேலம்) is widespread in Thoothukudi. It stays moderately green in the dry season, so separate it from crops by the dry-season floor and the shape of the peak.
- There is no field ground truth. Treat satellite outputs as signals needing field verification.

## 13. Personal data
Owner names, patta numbers and compensation amounts are personal data of real people.
- Keep them in the local DB only.
- Never send whole documents to a provider that trains on free-tier data unless the model registry marks that provider `training_on_free_tier: false`, or the user has approved it (see `free-model-router`).
- Never commit extracted owner data to git.
- Screenshots in public demo material must mask owner names.
