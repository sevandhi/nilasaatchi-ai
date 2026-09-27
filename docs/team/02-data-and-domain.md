# 02 · Data and Domain

## 1. What FarmwiseAI gave us
| Folder | Contents |
|---|---|
| `Dataset/Land_documents/` | GO, AS, LPS and 23 acquisition-stage folders (`3(1) Publish`, `7(2) Award`, `Form F` …) |
| `Dataset/Geospatial_Layer/` | FMB map (1,242 subdivision polygons), cadastral map (396 survey polygons), park boundary, 18 SIPCOT park points, and basic layers (37,590 road lines, 511 rail lines, 37 stations, 185 waterbodies, 36 substations, airport, seaport, 63 schools) |
| `Documents/` | The challenge brief, the task/dataset guide, the AWS access guide |

**Study area:** the Allikulam SIPCOT industrial park ("Allikulam Oil Refinery Industry Formation"), Thoothukudi taluk. It spans **7 villages**: Allikulam, Keelathattaparai, Melathattaparai, Umarikottai, Peroorani, Ramasamypuram and South Silukanpatti. The park is organised in **Units** (e.g. Unit 4, Unit 7) and **Blocks** 1–10.

## 2. The numbers we measured (Phase 1)
- **3,212 PDFs → 2,361 unique** by content hash: 851 exact duplicates, and 186 duplicate groups span two or more folders. The catalog stores **2,362** documents, including files from one zip archive.
- **18,644 pages → 12,564 unique**; the catalog has 12,859 page rows including the zip files.
- **Mostly scanned Tamil.** Only the 3(1) gazettes, the chitta (patta) printouts and the possession certificates have a digital text layer.

## 3. The legal lifecycle (learn this, reviewers ask)
| # | Stage | Folders | Meaning |
|---|---|---|---|
| 0 | GO / AS / LPS | Government Order, Administrative Sanction, Land Plan Schedule | The government sanctions the project |
| 1 | s.3(1) | `3(1) Publish/Published` | Gazette notification: land to be acquired (Form C) |
| 2 | s.3(2) | `3(2) Notice/Errata` | Notice to owners; errata correct earlier notices |
| 3 | Exemption | `Exemption *` | Land proposed or approved for exemption |
| 4 | Price negotiation | DLPNC, SLPNC, Land Value Fixation, Funds | District- and State-level committees fix the rate (e.g. ₹5/7/9 lakh per acre, varying by block) |
| 5 | Possession notice | `Form E` | Surrender the land within 30 days |
| 6 | Award | `7(2)`, `7(3)`, `Form F` | 7(2) = consent/negotiated award; 7(3) = final award without consent; Form F = per-owner compensation |
| 7 | Payment | `Amount Disbursed*`, `Court Deposit` | Paid to the owner, or deposited in court (disputes or unknown heirs) |
| 8 | Possession | `LDR Issued`, `Possession Taken` | Land Delivery Certificate: the land is handed to SIPCOT |
| 9 | Mutation | `Patta Transferred` | The land record (chitta) now shows SIPCOT |

## 4. Units and number formats (a common source of bugs)
- **Hectare triple `H.AA.SS`:** `0.95.50` = 0.9550 ha. **Chitta format** `0 - 53.50` = 0.5350 ha.
- **Acres:** documents mostly convert with **2.47**, not the exact 2.47105, so our checks accept both.
- **Cents:** 1 cent = 0.01 acre (used on Form E).
- **Money:** Indian grouping, e.g. `₹12,60,000/-` = 1,260,000.
- **Built-in check:** award amount = acres × per-block rate (e.g. 2.52 ac × ₹5,00,000 = ₹12,60,000).
- **Survey numbers:** `177/4A` = survey 177, subdivision 4A. The canonical parcel key is **`parcel_uid = "<Village>|<KIDE>"`**, because the FMB KIDE (e.g. "168") repeats across villages (decision D-020).

## 5. Tamil vocabulary you should know
நில உரிமையாளர் = landowner; பட்டா எண் = patta no.; புல எண் = survey no.; உட்பிரிவு = subdivision; விஸ்தீரணம் = extent; ஹெக்டேர்/ஏக்கர் = hectare/acre; புன்செய்/புஞ்சை = dry land; நன்செய் = wet land; புறம்போக்கு = poramboke (government land); இழப்பீட்டுத் தொகை = compensation; அலகு = unit; பிளாக்/தொகுதி = block; மொத்தம் = total; த/பெ = father's name; க/பெ = husband's name; மகன் = son; மனைவி = wife; வாரிசுதாரர்கள் = legal heirs.

## 6. Real findings in the data (good review material)
| Finding | Detail | Decision |
|---|---|---|
| **Proposal vs sanction drift** | The Tamil *proposal* (AS.pdf page 2) asks for **911.395 ha**; the GO and the English AS letter sanction **904.40 ha** (differences in Keelathattaparai +2.33 ha and Ramasamypuram +4.665 ha) | D-014 |
| **FMB map quality** | **366 overlapping parcel pairs** (7.3 ha counted twice); **28** parcels spill outside their cadastral survey; one self-intersecting polygon; an Excel-corrupted subdivision ("38415" for 3-4-5) | D-023 |
| **Area totals** | Geodesic sum 908.82 ha vs overlap-free union **901.50 ha**. We therefore *withdrew* the claim "the map exceeds the sanction" | D-023 |
| **Folders ≠ content** | 33% of the 100 labelled documents are in the "wrong" folder; the "Amount Disbursed" folders match only 1 of 17 | D-025 |
| **Mixed gazettes** | One gazette file holds an unrelated railway errata, an Allikulam notice *and* a Solar-plant (Tirunelveli) notice, so relevance is judged per notice, not per file | D-025 |
| **Document errors** | Amount in words ≠ figures (₹25,48,000 vs ₹25,41,000); column sums ≠ printed totals; ha→ac pairs that fit neither factor | held-out README |
| **Per-block rates** | ₹5, 7 and 9 lakh per acre in different blocks | — |

## 7. Data we added ourselves (clearly separated from the provided data)
- **Copernicus DEM (30 m)** → elevation and slope.
- **ESA WorldCover 2021** → land-cover fractions per parcel.
- **JRC Global Surface Water occurrence.**
- **GloFAS** river-flood hazard.
- **Sentinel-2 L2A** imagery 2019→2026 (587 unique acquisitions) from the open Earth Search catalogue, with no account needed.

## 8. Privacy
Owner names, patta numbers and amounts are personal data.
- They stay in the local database.
- They are masked in the API by default (bank account numbers are never stored).
- They never go to AI providers that train on free-tier inputs (see doc 04).
- The evaluation label files containing names are git-ignored.
