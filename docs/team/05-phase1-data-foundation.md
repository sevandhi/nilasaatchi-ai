# 05 · Phase 1: Data Foundation

**Goal:** turn the raw folders and map files into a clean, queryable database: every document catalogued and classified, every parcel loaded with its geometry and context.

## 1. Catalog and deduplication (`make catalog`)
- **What:** walks `Dataset/`, computes SHA-256 per file, groups byte-identical copies, counts pages, detects whether each page has a real text layer, and renders a 150-dpi WebP preview of every unique page.
- **Why:** 26% of files are duplicates, and many sit in several stage folders. One document can support several stages, so we keep all paths per unique file.
- **Result:** 2,362 documents / 12,859 pages; previews in `data/pages/`.
- **Design choice:** 300-dpi images are rendered on demand only (rendering everything up front would take ~25 GB) (D-021).

## 2. Document classifier (`make classify`, `make eval-classify`)
- **The problem:** folder names are unreliable (33% wrong in our labelled sample).
- **v1 (keyword rules) failed: 45% stage accuracy.** Award documents cite the "3(1) gazette" in their opening paragraph, so rules labelled them as gazettes (676 "gazettes", only 26 "awards").
- **v2 (LLM classifier, D-030): 90% stage accuracy.** It gets the first-pages text (text layer or Tesseract) plus the folder/filename as a weak hint, and sends it to Bedrock **Ministral 3B**. When the text is poor, the page-1 image goes to **Ministral 8B vision**. The output is JSON: form type, legal stage, payee kind, committee level, and **page segments** (one file can contain several documents).
- **Taxonomy rule (D-025):** `doc_type` = the document's **form** (e.g. a bank draft); `stage` = what it proves (e.g. a court deposit).
- **Evaluation set:** 100 documents labelled by hand from page images, stratified across all folders.
- **Safety fix after an incident:** when AWS expired mid-run, failed calls had overwritten types with "OTHER". Now failed or fallback results never overwrite a type, and the run stops cleanly on expired credentials.
- **Speed fix:** first-page OCR is now cached (`data/quick_ocr_cache/`), so restarts never repeat work (D-041).

## 3. GIS loading and reconciliation (`make load-gis`, `make kg-check`)
- Loads all 12 GeoJSON layers into PostGIS:
  - strips Z coordinates;
  - repairs invalid polygons;
  - normalises village spellings (`config/aliases.yaml`).
- **Parcel key** `parcel_uid = Village|KIDE` (KIDE repeats across villages).
- **Precomputed per parcel:** geodesic area; distance to the nearest major road, any road, substation and rail station; waterbody overlap; inside-park flag.
- **Area method (D-023):**
  - Areas are *geodesic* (true ellipsoid). Planar UTM areas are inflated ~0.22%, because the park is 3° from the UTM zone's centre.
  - Totals use the *dissolved union*, because FMB parcels overlap.
- **Reconciliation** against official totals:
  - FMB geodesic sum 908.82 ha;
  - union 901.50 ha;
  - sanction 904.40 ha;
  - Tamil proposal 911.395 ha.
- **FMB quality views:** 366 overlaps, 28 parcels outside their survey.

## 4. Rasters (`make raster`)
We added free public data (not provided by FarmwiseAI) and computed per-parcel statistics:
- elevation and slope (Copernicus DEM);
- land-cover mix (WorldCover);
- surface-water occurrence (JRC);
- flood depth (GloFAS).

Stored in `parcel.raster_stats` for all 1,242 parcels.

## 5. Satellite inventory (`make s2-inventory`)
- 587 unique Sentinel-2 acquisitions (2019→2026); 810 raw items include reprocessed duplicates.
- Each scene is scored by how much of the park is clearly visible: ≥ 60% in 194 scenes.

## 6. Phase 1 gate
- **Exit criteria:**
  - counts match ✅
  - FMB totals within 0.5% ✅
  - rasters for all parcels ✅
  - classifier ≥ 90% stage accuracy ✅ (v2)
- **Done:** the classifier's full run over all 2,362 documents (stage 0.90 / type 0.80 / scheme 0.96 on 100 labels).
