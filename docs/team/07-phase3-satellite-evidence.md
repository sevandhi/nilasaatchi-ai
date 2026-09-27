# 07 · Phase 3: Satellite Evidence ("Planet")

**Goal:** for every parcel, build an objective history of what happened on the ground from 2019 to today, so it can be compared with what the documents claim.

## 1. Data: Sentinel-2
- **What:** European Copernicus satellites; 10 m pixels; a revisit every ~5 days; free.
- **Where from:** the **Earth Search STAC** catalogue on AWS open data (no account).
- **Our tile:** 43PHK.
- **Inventory:** 587 unique acquisitions (2019→2026), of which 307 show ≥ 20% of the park clear.
- **Cleaning:** clouds, shadows and cirrus are removed with the scene-classification (SCL) band.

## 2. Per-parcel indices (`make s2-extract`)
For each parcel and each usable scene (**381,294 observations** = 307 scenes × 1,242 parcels; **382,536** after the incremental refresh added the 2026-09-26 scene, see chapter 13):

| Index | Formula | Meaning |
|---|---|---|
| NDVI | (NIR − Red)/(NIR + Red) | Vegetation greenness |
| NDWI | (Green − NIR)/(Green + NIR) | Water |
| BSI | ((SWIR + Red) − (NIR + Blue)) / (…) | Bare soil (ploughing) |
| NDBI | Built-up index | Construction |

- **Parcel edges:** a −5 m inward buffer avoids edge pixels. Tiny parcels are measured unbuffered with a `mixed_pixel` flag.
- **Bug caught:** 6 scenes were wrongly "corrected" for a brightness offset they didn't have, which corrupted their values (3 of them just before the demo parcel's handover). The fix checks the raw dark-pixel level first (D-031).

## 3. Seasonal features (`make s2-features`)
- **Smoothing:** Whittaker smoothing, with λ = 1000 chosen by hold-out cross-validation.
- **Seasons:** Kharif (Jun–Sep), Rabi / north-east monsoon (Oct–Feb), Summer (Mar–May).
- **Per parcel-season:** peak, minimum, amplitude, dry-season level, number of peaks, peak date, integral, bare-soil level, water maximum, gap flags.
- **Result:** **40,986** parcel-season rows.

## 4. Land-use model: teacher → student
- **Teacher labels:** Gemini (vision) labelled 300 parcel-seasons from image chips plus the curve plot. They were combined with ESA WorldCover as a prior.
- **Student:** a LightGBM model trained on the seasonal features. Validation is leave-one-village-out, so there is no spatial leakage.
  - macro-F1 0.634, accuracy 0.936 vs the teacher labels;
  - cropped F1 0.97, bare/fallow F1 0.96;
  - rare classes (irrigated) are weak, with only 5 examples.
- **States:** cropped, irrigated_multi, perennial_veg, bare_fallow, cleared_or_built, water, insufficient_data.
- **Pattern:** the "small model first, big model when unsure" pattern shows up here too; uncertain predictions route to a vision second opinion.

## 5. The key scientific finding, and our redesign (D-035)
The model showed that **almost every parcel turns green after the NE monsoon**, weeds included: 6,130 "cropped" vs 27 bare in rabi seasons since 2021. So "green after possession" **does not prove farming**, and our first headline ("444 parcels still cultivated after possession") was **not proven as stated**.

**The redesign is a control-group, difference-in-differences (DiD) test:**
- **Controls:** 119,116 observations of *never-acquired farmland* in a 2–6 km ring outside the park, under the same rain.
- **For each parcel and each event** (3(1) notice, award, possession):
  DiD = (parcel − controls after the event) − (parcel − controls before it), with bootstrap 95% confidence intervals → table `parcel_did` (22,356 rows).
- **Ploughing signature:** a bare-soil rise in the 2–6 weeks before green-up.

## 6. Results (post-possession, rabi seasons)
| Result | Count |
|---|---|
| Parcels that still behave like active farmland (greenness within the control range, p ≥ 0.7) | **1,143 / 1,242** |
| Parcels with a **significant drop vs controls** (possession took effect, or abandonment) | **143** |
| Parcels significantly greener than controls | 513 |
| Ploughing clearly detected | 16 of 1,144 parcel-seasons (10 m pixels are too coarse for most ploughing) |

**Demo parcel, Melathattaparai 233** (Land Delivery Certificate dated 21.03.2025):
- After handover its greenness stays within the farmland range (DiD +0.35, CI −0.13 to 0.90, i.e. no significant change vs controls).
- Its **ploughing-like signal is significantly stronger than the controls'** (+2.15, CI 0.85 to 3.70).
- **Caveat:** only one post-possession season, so this is a *lead for field verification*, not proof.

## 7. How to explain it simply
> "We don't ask 'is the field green?'. After the monsoon everything is green. We ask 'after the government took possession, does this parcel keep behaving like the farms next door that were never acquired?'. If it suddenly behaves differently from its neighbours, possession probably changed something. If it keeps pace with active farms and shows ploughing, someone may still be farming it. Either way we show the evidence and ask for a field check."

## 8. Files
- Code: `planet/`.
- Report: `eval/planet/did_report.json`.
- Chips: `data/s2/chips/`.
- Model: `data/models/landuse_lgbm_v1.*`.
- Spike: `docs/spikes/sentinel2-spike.md`.
