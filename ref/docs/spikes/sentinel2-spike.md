# Spike: Sentinel-2 "Paper vs Planet" feasibility (2026-09-26)

**Question.** Can free satellite data verify land-acquisition claims per parcel, on CPU, with no accounts?

**Setup**
- Source: Element84 Earth Search STAC (`sentinel-2-l2a`, AWS open data, anonymous). The AOI bbox is 77.99–78.04 E, 8.76–8.81 N, on MGRS tile 43PHK.
- Tooling: `ghcr.io/osgeo/gdal:ubuntu-small-latest` running `spikes/s2_ndvi_spike.py`, which reads over `/vsicurl` windowed COG reads and warps to 0.0001°.
- Method:
  - rasterise the FMB parcels (1,242 polygons, 1,142 with ≥ 5 clear pixels)
  - use SCL classes 4–7 as the valid mask
  - take the median NDVI per parcel

**Scene availability:** 170 scenes with < 20% cloud between 2019 and 2026 (2019: 22, 2020: 29, 2021: 37, 2022: 17, 2023: 23, 2024: 13, 2025: 17, 2026: 12). They are thin in Jul–Nov because of the monsoon, so Sentinel-1 SAR is a stretch goal to fill the gap.

| Date | Median parcel NDVI | Share NDVI > 0.35 | Share NDVI < 0.15 |
|---|---|---|---|
| 2019-01-09 | 0.383 | 77.2% | 0.0% |
| 2021-05-28 (dry) | 0.399 | 67.0% | 1.7% |
| 2021-12-24 (post-monsoon) | 0.714 | 98.5% | 0.1% |
| 2025-07-11 (dry) | 0.205 | 3.1% | 18.5% |
| 2025-12-23 (post-monsoon) | 0.684 | 99.6% | 0.0% |
| 2026-01-17 | 0.680 | 98.5% | 0.0% |
| 2026-05-27 (dry) | 0.239 | 8.8% | 7.7% |

**Seasonal amplitude** (post-monsoon − dry): the median was 0.297 before acquisition (2021) and 0.412 after possession (2025–26). Measured on 1,142 parcels:
- Crop-like (amplitude > 0.3): 557 before and 968 after.
- Crop-like in both periods: **532**. By village: Allikulam 246, Keelathattaparai 135, Melathattaparai 84, Umarikottai 26, Peroorani 21, Ramasamypuram 17, South Silukanpatti 3.

**Worked example.**
- Land Delivery Certificate, Melathattaparai Block 2: surveys 233 and 235, handed over **21.03.2025**.
- NDVI for 233: 0.51 in dry 2021 → 0.78 post-monsoon 2021 → 0.24 in dry 2025 → **0.76 on 2025-12-23** → 0.36 in dry 2026. Survey 235 follows the same pattern.
- A seasonal vegetation cycle persists after possession. That is a lead for field verification: re-cultivation, weed flush, or possession not effected on the ground.

**Caveats (must appear in the product)**
- These are single-date comparisons. The P3 engine uses full smoothed time series.
- Rainfall differs between years, and a post-monsoon weed flush can mimic crops.
- Mixed pixels affect small parcels.
- The output is a *signal needing field verification*, never an accusation.

**Verdict.** Feasible. The whole-park, per-parcel computation takes minutes on CPU with no account.

---
## Correction after P0 reproduction (2026-09-26, `make s2-spike`)
- **Scene count:** the "170 scenes" double-counted reprocessed baselines. Earth Search returns 810 raw items → **587 unique acquisitions**, **122 with < 20% cloud** (129 distinct dates where any variant is < 20%).
- **Mixed baselines:** the original spike used baseline 03.00 (`_0`) for 2021-05-28 but 05.00 for other dates. Baselines shift the AOI median NDVI materially (0.597 vs 0.711 on 2021-12-24).
- **Reproduction:** on the legacy grid with the spike's variants, every median is within 0.002 and crop-like-both is 530 vs 532, so the code is right.
- **Production settings (D-015):** highest baseline, native UTM grid (EPSG:32643), −5 m inward buffer → **1,073 analysable parcels, 444 crop-like in both periods**. This is the figure used in the proposal.
- **Robust result:** Melathattaparai 233 reproduces in all four configurations: 0.51 / 0.79 / 0.24 / 0.76 / 0.36.
- **Fragility:** the amplitude > 0.3 threshold sits at the median amplitude (0.297), so counts move ±5% with the grid and about −17% with the buffer. P3 replaces it with smoothed phenology plus a classifier.
