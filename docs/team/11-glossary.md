# 11 · Glossary

| Term | Meaning |
|---|---|
| **SIPCOT** | State Industries Promotion Corporation of Tamil Nadu; it develops industrial parks |
| **TN Act 10 of 1999** | TN Acquisition of Land for Industrial Purposes Act, 1997 |
| **GO / AS / LPS** | Government Order / Administrative Sanction / Land Plan Schedule |
| **s.3(1), s.3(2)** | Gazette notification / notice to owners |
| **DLPNC / SLPNC** | District / State Level Price Negotiation Committee |
| **Form E / Form F** | Possession notice / per-owner compensation apportionment |
| **7(2) / 7(3) award** | Consent (negotiated) award / final award without consent |
| **LDR** | Land Delivery Receipt/Certificate: the land is handed over |
| **Patta / Chitta** | Land-ownership record / e-services extract of it |
| **Poramboke** | Government land (not privately owned) |
| **Punsey / Nansey** | Dry land / wet (irrigated) land |
| **FMB** | Field Measurement Book: subdivision-level survey sketches (our parcel map) |
| **Cadastral map** | Survey-level parcel map |
| **KIDE** | FMB key "survey/subdivision", e.g. 177/4A |
| **parcel_uid** | Our unique key "Village\|KIDE" |
| **H.AA.SS** | Hectare.are.square-metre notation |
| **Geodesic area** | True area on the Earth's ellipsoid (vs flat-map area) |
| **Dissolved union** | Merging overlapping polygons so area isn't double-counted |
| **Sentinel-2 / L2A** | ESA satellites / surface-reflectance product |
| **STAC** | Catalogue standard for satellite scenes |
| **SCL** | Scene-classification band used to mask clouds/shadows |
| **NDVI / NDWI / BSI / NDBI** | Vegetation / water / bare-soil / built-up indices |
| **Whittaker smoothing** | Fills and smooths noisy time series |
| **Kharif / Rabi / Summer** | Jun–Sep / Oct–Feb (NE monsoon) / Mar–May seasons |
| **Teacher–student** | A big model labels a sample; a small model learns and labels everything |
| **DiD** | Difference-in-differences: change vs a control group, before vs after |
| **Control group** | Never-acquired farmland 2–6 km outside the park |
| **Bootstrap CI** | Confidence interval from resampling |
| **VLM** | Vision-language model (reads images) |
| **Bedrock** | AWS service hosting AI models (we use Ministral 3, Titan) |
| **SSO / device code** | AWS login where the user approves a code in their browser |
| **Router** | Our component choosing a model per task with privacy/quota/fallback |
| **PII / PSEUDO / PUBLIC** | Privacy tiers: personal / pseudonymised / public |
| **Shadow cost** | What a call would cost at commercial prices |
| **Circuit breaker** | Temporarily skips a failing provider |
| **Golden / held-out / dev set** | First tuning set / unseen test set / tuning set for fixes |
| **Repair loop** | Measure → diagnose → fix → re-measure |
| **Self-consistency** | Document arithmetic checks (totals, ha↔ac, amount = acres × rate) |
| **Evidence pack** | Document crop + satellite chips + checks + verdict for one finding (planned P5) |
| **SSE** | Server-sent events: the live progress stream from API to UI |
| **Ledger (hash chain)** | Tamper-evident log of every agent step |
