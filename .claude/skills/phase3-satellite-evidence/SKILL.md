---
name: phase3-satellite-evidence
description: Phase 3 (Day 2–6) — the "Planet" pillar. Sentinel-2 L2A STAC inventory (2019→today), windowed COG extraction and per-parcel masked indices, smoothing and seasonal phenology features, parcel-season land-use classification (LightGBM student + WorldCover/Gemini teacher labels), satellite chips, event-window states around document dates, agri-claims crop-presence tools for arbitrary polygons, optional Sentinel-1 gap-fill. Load for any satellite, phenology, land-use-state or crop-presence work.
---

# Phase 3 — Planet: Satellite Evidence Engine

Also load `land-domain-knowledge` and `free-model-router`.

**Owners:** eo-engineer (all tasks). qa-evaluator audits the labels. gis-engineer consumes the outputs in P5.

**Baseline to reproduce first:** `docs/spikes/sentinel2-spike.md` and `spikes/s2_ndvi_spike.py`. Expected results:
- Reproduced in P0 (`make s2-spike`, legacy grid + the spike's baseline variants): all medians within 0.001–0.002, 1,142 parcels (> 5 valid px), crop-like both 530 vs 532
- **Production settings (D-015):** highest processing baseline, native UTM grid, −5 m buffer → 1,073 analysable parcels, crop-like both 444. STAC: 587 unique acquisitions, 122 with < 20% cloud
- Earth Search baseline ≥ 04.00 pixels are already offset-corrected (`earthsearch:boa_offset_applied`); Planetary Computer pixels need −1000 DN. Handled by `dn_offset`
- The single-date amplitude > 0.3 rule is fragile (it sits at the median amplitude); never use it in production
- median parcel NDVI 0.714 on 2021-12-24 and 0.684 on 2025-12-23
- (legacy reproduction only) 532 parcels crop-like in both periods
- Melathattaparai 233: NDVI 0.76 on 2025-12-23

## T3.1 STAC inventory (`planet/stac/`, `make s2-inventory`)
- `pystac-client` against `https://earth-search.aws.element84.com/v1`, collection `sentinel-2-l2a`.
- bbox = the park boundary plus 2 km. Dates run from 2019-01-01 to today. Do not filter on cloud; SCL decides.
- Collapse processing-baseline duplicates (IDs ending `_0/_1/_2`) to the highest suffix per date.
- Store the results in `s2_scene`.
- **Mirror:** Planetary Computer STAC (`planetary-computer` package for anonymous signing), used if Earth Search fails.

## T3.2 Extraction (`planet/extract/`, `make s2-extract`)
- Per scene, read B02, B03, B04, B08, B11 (20 m → 10 m bilinear) and SCL (nearest) with rasterio windowed reads over the AOI, in UTM 32643. Cache to `data/s2/{date}.tif` as a multi-band COG. Use a 12-process pool.
- **Valid mask:** SCL ∈ {4 vegetation, 5 bare, 6 water, 7 unclassified}. Exclude 3 (shadow), 8–10 (cloud/cirrus) and 11 (snow).
- **Per parcel** (FMB polygons buffered −5 m; when the buffered polygon is empty, use the original):
  - median NDVI, NDWI (G−NIR), BSI (((SWIR+R)−(NIR+B))/((SWIR+R)+(NIR+B))) and NDBI
  - valid_frac, n_px, `low_support` (n_px < 10)
- Upsert into `parcel_obs`.
- The on-demand version for uploaded polygons (`upload_polygon`) uses the same code path. It fetches only the needed scenes, and so works for any polygon inside tile 43PHK.

## T3.3 Features (`planet/features/`, `make s2-features`)
- Seasons come from `config/seasons.yaml`:
  - Kharif: Jun–Sep
  - Rabi / NE monsoon: Oct–Feb
  - Summer: Mar–May
  - Agricultural year: Jun–May
- Smooth the NDVI and BSI series with a Whittaker smoother (λ tuned on the spike parcels), using only valid observations. Mark gaps over 45 days.
- Per parcel-season and per ag-year: `ndvi_max`, `ndvi_min`, `amplitude`, `dry_mean`, `peaks_count` (> 0.45 with prominence > 0.15), `peak_doy`, `integral`, `bsi_dry_mean`, `ndwi_max`, `n_obs`, `max_gap_days`, `yoy_delta`.
- Neighbour context: the median amplitude of parcels within 300 m, which helps separate a weed flush from a crop.

## T3.4 Labels and classifier (`planet/classify/`, `make s2-classify`)
- **States:** `cropped`, `irrigated_multi`, `perennial_veg`, `bare_fallow`, `cleared_or_built`, `water`, `insufficient_data`.
- **Teacher labels:**
  - **WorldCover 2021** class fractions per parcel give weak priors: tree → perennial, cropland → cropped, built → built, water → water, bare/grass → fallow. These apply to the 2021 seasons only.
  - **Gemini visual teacher** (`satellite_teacher` task, PUBLIC tier):
    - ~300 parcel-seasons, stratified by village, weak label and amplitude quantile
    - input: 3 true-colour chips (pre-season, peak, dry), 1 NDVI chip, and the time-series PNG
    - prompt: `prompts/satellite_teacher.md`
    - output JSON `{state, confidence, visual_evidence, caveats}`
    - run over 2 days to respect the RPD limit
  - **Rules** as a third voter. The final label = majority vote, with a disagreement flag.
- **Student:** LightGBM (multiclass) on the T3.3 features.
  - Cross-validation grouped by village (leave-one-village-out).
  - Calibrate with isotonic regression.
  - Save `data/models/landuse_lgbm_v{n}.txt`, recording its metrics.
- **Runtime routing** (tool `landuse_state`):
  - p ≥ τ_accept (default 0.75) → accept
  - otherwise → Gemini visual second opinion
  - if they disagree → `needs_field_verification`
  - Each route is logged to the router log.
- **qa audit:** the qa-evaluator independently labels 50 parcel-seasons from the chips, blind to the teacher's answer. Report teacher-vs-audit and student-vs-audit agreement.

## T3.5 Chips (`planet/chips/`, tool `satellite_chip`)
- True-colour (B04/B03/B02, 2–98% stretch) and NDVI (a diverging palette), 256×256 around the parcel with its outline drawn. PNG, cached, and served at `/chips/{kide}/{date}.png`.
- Each chip records its scene ID in metadata, so every chip traces back to its source scene.

## T3.6 Event windows (`planet/events/`)
For each parcel with `acquisition_event` rows, compute the state distribution in these windows:
- `pre_notification` = [T_3(1) − 12 months, T_3(1))
- `pending` = [T_3(1), T_award)
- `post_award` = [T_award, T_possession)
- `post_possession` = [T_possession, now]

Before P2 lands, use the document-level dates (the 3(1) gazette dates and LDR dates by block) as fallbacks.

## T3.7 Agri-claims tools (the reuse pack, used by P5)
- `crop_presence(polygon|kide, season)` → `{present: bool, p, evidence: chips, obs}`
- `fallow_streak(polygon|kide, from, to)` → the longest run of `bare_fallow` seasons
- These are domain-neutral. They must not reference acquisition concepts.

## T3.8 Stretch: Sentinel-1
- Planetary Computer `sentinel-1-rtc`, VV/VH per parcel. The VH/VV ratio acts as a crop-structure proxy during monsoon gaps.
- Add it as features only when the qa audit shows that it helps.

## Exit gate
```
make s2-inventory s2-extract s2-features s2-classify && make eval-planet && make test
```
Targets are in `plan.md` §8 P3 and §10. The report covers:
- scene counts by year and month
- obs per parcel (p10/p50)
- the state distribution by season (a chart)
- student metrics and the confusion matrix
- audit agreement
- the spike numbers reproduced, with any differences explained
