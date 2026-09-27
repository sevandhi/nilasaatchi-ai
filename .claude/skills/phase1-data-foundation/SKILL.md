---
name: phase1-data-foundation
description: Phase 1 (Day 1–3) — document catalog and MD5 dedup, page rendering cache, content-based doc-type and scheme-relevance classifier, PostGIS knowledge-graph migrations, GeoJSON loading with village aliases, area reconciliation against ground-truth totals, and team-sourced raster layers (Copernicus DEM, slope, WorldCover, JRC surface water, GloFAS) with per-parcel zonal stats. Load for any catalog, schema, GIS-load or raster work.
---

# Phase 1 — Data Foundation

Also load `land-domain-knowledge`. The expected numbers below come from that skill.

**Owners:**
- data-engineer: T1.1, T1.2, T1.5, T1.6
- gis-engineer: T1.3, T1.4
- qa-evaluator: labels for T1.6 and the gate

## T1.1 Catalog (`pipeline/catalog/`, `make catalog`)
- Walk `Dataset/**/*.pdf|png|zip`. Unzip `Form E/F_FORM_UNITS.zip` into `data/unzipped/` (29 files) and catalog those too, with the `zip_member` source.
- For each file record: sha256, md5, bytes, pages (pypdfium2), text-layer chars per page (pdftotext), `folder_label` (the immediate stage folder), `path`, and `dup_group_id` (the first sha256 in the group).
- Expected: **3,212 PDFs, 2,361 unique, 18,644 pages, 12,564 unique pages, 186 duplicate groups spanning folders.** Any difference must be explained in the gate report.
- `pages`: one row per unique page. Render at 150 dpi (webp, for the UI) and 300 dpi (png, for OCR) into `data/pages/{sha[:2]}/{sha}/{n}.{ext}`, parallelised over 12 processes and skipping files that already exist.
- Text-layer quality score = (Tamil + Latin + digit characters) / all characters, combined with a dictionary-hit ratio. Mark a page `text_ok` when chars ≥ 50 and the score ≥ 0.8.

## T1.2 Header pre-parse (cheap, local)
- On the first page's text (text layer or quick Tesseract at 150 dpi, psm 4), apply regexes for:
  - `அலகு|Unit\s*[-:]?\s*(\d+)` and `பிளாக்|Block\s*[-:]?\s*(\d+)`
  - village aliases
  - dates
  - `Form\s*[CEF]` / `படிவம்\s*-\s*[இஎ]`
  - `7\(2\)|7\(3\)|3\(1\)|3\(2\)`, DLPNC/SLPNC, `G\.O\.|அரசாணை`
- Store the results as hints. P2 refines them.

## T1.3 Schema (`db/migrations/`)
- Implement `plan.md` §5.3 exactly, plus GIST indexes on geometries, btree on `kide` and `(village_id, survey_no)`, pgvector HNSW on `page.embedding`, and a tsvector column (the `simple` config for Tamil).
- Add the views `v_parcel_status`, `v_village_progress` and `v_discrepancy_open`. They may be placeholders until P5.
- Add a read-only DB role `agent_ro` with SELECT on the allow-listed tables and views, for the SQL tool.

## T1.4 GIS load and reconciliation (`pipeline/gis/`, `make load-gis`, `make kg-check`)
- Load all 12 GeoJSONs.
  - FMB → `parcel` (keep the raw fields; `kide` = the `KIDE` field; normalise `sub_div`).
  - Cadastral → `survey`.
  - The rest → `ref_layer_*`.
  - Strip the Z coordinate, then run `ST_MakeValid` and `ST_Multi`.
- Build the `village` table from the canonical names, plus aliases seeded from the skill into `config/aliases.yaml`.
- Compute `area_ha_gis` via `ST_Area(ST_Transform(geom, 32644))/1e4`.
- `make kg-check` prints the village-total table and asserts that FMB totals are within 0.5% of the skill's §9 numbers (908.51 ha total). It must also report the GO/AS deltas as **expected discrepancies**, not failures.
- Precompute on `parcel`:
  - `dist_nearest_road_m` by fclass group (trunk/primary/secondary vs other)
  - `dist_nearest_substation_m`
  - `intersects_waterbody`
  - `dist_nearest_rail_station_m`
  - `inside_park_boundary`

## T1.5 Rasters (`pipeline/raster/`, `make raster`) — team-sourced; label the source in the DB and the UI
- **AOI** = the park boundary buffered by 15 km, in EPSG:4326.
- **Copernicus DEM GLO-30:**
  - Source: the anonymous `s3://copernicus-dem-30m/` tiles covering lat 8–9, lon 77–78 and 78–79. Use `aws s3 cp --no-sign-request` or HTTPS; the tile names follow `Copernicus_DSM_COG_10_N08_00_E078_00_DEM`.
  - Mosaic and clip to a COG.
  - Derive `slope_deg`, via `gdaldem slope -s 111120` in the ocr-tools container, or with numpy on the UTM-reprojected DEM.
- **ESA WorldCover 2021 10 m:** from `s3://esa-worldcover/v200/2021/map/`, tiles `ESA_WorldCover_10m_2021_v200_N06E075_Map.tif` **and** `…_N06E078_Map.tif`. Tiles are 3°×3°, and the park straddles 78°E. Mosaic and clip them.
- **JRC Global Surface Water occurrence:** the public GCS/HTTP tiles for 70E–80E, 10N–0N. Clip them.
- **GloFAS flood hazard v2.1:** RP100 depth from data.europa.eu or source.coop. Clip it. Note: it covers river flood only.
- If a source needs a login or key: stop, record it, escalate, and continue with the others.
- **Zonal stats per parcel** into `parcel.raster_stats` jsonb: `elev_mean`, `elev_max`, `slope_mean`, `slope_p90`, `lc_majority` + `lc_hist`, `water_occ_mean`, `water_occ_gt25_pct`, `flood_rp100_max`. Target: 1,242 of 1,242 parcels.

## T1.5b Satellite scene inventory
Run `make s2-inventory` (implemented by the eo-engineer per `phase3-satellite-evidence` T3.1). Then:
- Load the ESA WorldCover fractions per parcel into `parcel.raster_stats.lc_hist`. These are also the teacher priors for P3.

## T1.6 Doc-type and scheme classifier v1 (`pipeline/classify/`)
- **Classes:** GO, AS, LPS, SEC31_GAZETTE, SEC32_NOTICE, SEC32_ERRATA, EXEMPTION_PROPOSAL, EXEMPTION_GO, DLPNC, SLPNC, LAND_VALUE, FUNDS, FORM_E, AWARD_7_2, AWARD_7_3, FORM_F, DISBURSEMENT, COURT_DEPOSIT, LDR, POSSESSION_CERT, CHITTA, COVERING_LETTER, BANK_INSTRUMENT (DD/cheque images), OTHER.
- **Method:**
  1. keyword and regex rules on the first 2 pages (Tamil and English)
  2. folder prior
  3. if the rules are ambiguous, a text-only call on the redacted first-page text via the router (`classify_text`)
  - Store `classified_type`, `type_confidence` and `type_evidence`.
- **Scheme relevance:**
  - `allikulam` if the text mentions Allikulam, அல்லிகுளம், Unit-4/Unit-7 with Thoothukudi, one of the 7 villages, or G.O. No.100.
  - `other_scheme` if it mentions another scheme (Solar Power Plant, a Tirunelveli Unit-02 context, and so on).
  - Otherwise `unknown`.
- **Evaluation (updated D-025):** the 100-document set in `eval/classify/labels.csv`. Gate metric = **stage-level accuracy ≥ 90%** (the 10 lifecycle stages of §2) scored on unique sha256; fine doc_type accuracy is reported alongside. Scheme relevance is evaluated per page segment: the solar-plant segment of 527_Gazette (from p4) must be `other_scheme` while pp2–4 stay `allikulam`. Truncated or broken PDFs (e.g. `Form F/F_form_201_1A (2).pdf`) need a fallback (repair with qpdf/pikepdf, else carve embedded JPEGs) and must never crash the catalog.

## Exit gate
```
make catalog classify load-gis raster kg-check && make test && make eval-classify
```
The report covers:
- counts against the expected numbers
- the village reconciliation table
- the raster coverage map (a PNG saved to `docs/screens/`)
- classifier metrics and a confusion matrix
- the misfiled-document list (where the folder label disagrees with the class)
- the out-of-scope list
