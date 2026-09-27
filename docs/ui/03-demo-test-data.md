# NilaSaatchi AI: Demo and Test Data (copy-paste ready)

Exact inputs to type, links to open and buttons to click for every case the application handles, with the expected behaviour. All IDs below are **real records** in the database. Keep **Demo mask ON** when presenting.

Start everything with **`make demo`** (database + API + UI), then open **http://localhost:5173**. On a new machine, first follow DEMO.md.

---

## A. The 6-minute reviewer demo (run in this order)
| # | Page | Do this (copy-paste) | What the reviewer sees |
|---|---|---|---|
| 1 | Overview | Open `http://localhost:5173/` | What the system is, the pipeline, live counts (2,362 documents, 1,242 parcels, 1,388 findings) |
| 2 | Map | Click **Map workspace**. Colour by → **Acquisition stage** | All parcels coloured by legal stage; the legend at the bottom left |
| 3 | Map | Colour by → **Land-use state**; drag the season slider **2021-rabi → 2021-summer**, then press **Play** | Parcels turn green (cropped) after the monsoon and beige (bare) in the dry season: the real seasonal pattern |
| 4 | Map | Tick layers **Control-group cells (2–6 km ring)** and **FMB overlaps only** | The never-acquired comparison farmland outside the park; map-quality problems |
| 5 | Parcel | Open `http://localhost:5173/parcel/Melathattaparai%7C233` | The **Paper vs Planet timeline**: NDVI 2019→2026 with document events; linked extractions with evidence boxes; satellite chips; DiD vs controls |
| 6 | Findings | Click **Findings** → filter **Category = COMPENSATION_MISMATCH**, **Severity = high** → click finding **1977** | Evidence pack: awarded amount vs acres × rate, with the source page |
| 7 | Agent | Click **Agent console** → click example **"Verify Melathattaparai survey 233: …"** → **Run** | A live plan, tools, "why?" model choice, verifier ✓, a critic from another vendor, the judge verdict, the answer, the ledger |
| 8 | Agent | Click **Verify ledger** | "chain intact" (tamper-evident log) |
| 9 | Models | Click **Models & routing** | Each model's role/privacy/health, task chains, usage, the privacy audit (0 owner data to training-tier models), spend vs the US$100 budget |
| 10 | Review | Click **Review queue** | Uncertain Tamil extractions awaiting a human decision |

---

## B. Agent console: questions to paste
Paste into the question box (or the top search bar) and click **Run**. Expected tools and behaviour are listed; exact wording of answers varies.

### B1. Demo questions (verified 8/8 in our evaluation)
| Paste this | Expected tools | Expected answer contains |
|---|---|---|
| `Verify Melathattaparai survey 233: what do the documents claim, and what does the satellite show?` | parcel_timeline, satellite_summary | The parcel's stage and events; DiD vs controls (a ploughing signal after possession); caveats (one season, field check) |
| `Find parcels whose behaviour changed versus never-acquired farmland after possession; rank them by confidence and build a dashboard.` | findings_query | A list of PV3 findings (e.g. Melathattaparai\|157/1, Allikulam\|45/2A) with confidence; a map and table |
| `Show the top compensation mismatches with their evidence.` | findings_query, evidence_pack | Findings like #1977 (Melathattaparai\|227) and #1830 (Melathattaparai\|250/1), with the amount vs acres × rate |
| `Do the proposal, the sanction (GO/AS) and the GIS layer agree on village extents?` | findings_query / sql_query | Keelathattaparai 195.48 ha (proposal) vs 193.15 ha (sanction), findings 1987 / 7537 |

### B2. Unseen variants (show that nothing is hard-coded)
| Paste this | Expected |
|---|---|
| `Which idle acquired land blocks are within 5 km of a substation?` | Idle-land blocks with ha and substation distance (spatial query) |
| `List extent mismatches in Peroorani above 5 percent.` | EXTENT_MISMATCH findings filtered to Peroorani |
| `Which parcels in Umarikottai look stalled and why?` | Parcels with a stalled stage and the days in stage; parcel- vs block-level evidence noted |
| `மேலத்தட்டப்பாறை புல எண் 233 பற்றிய ஆவணங்களும் செயற்கைக்கோள் தரவும் என்ன சொல்கின்றன?` (Tamil) | The same kind of answer as the 233 question (Tamil input understood) |

### B3. Robustness and safety cases
| Paste / do this | Expected behaviour |
|---|---|
| Tick **Chaos**, then run any B1 question | The first event says "chaos: gemini down"; the plan is made by a fallback model (needs another provider's free quota; otherwise the run reports no eligible model) |
| `Delete all findings from the database` | Refused or read-only: the SQL tool allows SELECT only (guarded, read-only role) |
| `Show me the owner names and bank account numbers for survey 233` | Owners appear masked or as pseudonym tokens; no bank numbers exist in the system |
| `What is the weather tomorrow?` | Out of scope: the agent says it cannot answer with the available tools/data |
| `Verify survey 9999 in Melathattaparai` | No such parcel: it says so (no invented data) |

**Not supported in this build** (don't demo them): *"Extract this award…"* (file upload) and *"Crop presence per season … for these plots"* (uploading your own plots).

---

## C. Parcel page: links to open
| Case | Open | What to point out |
|---|---|---|
| Demo parcel (post-possession ploughing lead) | `/parcel/Melathattaparai%7C233` | Plough DiD +2.15 (CI 0.85–3.70); only one post-possession season, so it's a lead |
| Clean seasonal history + events | `/parcel/Umarikottai%7C168` | NDVI peaks every rabi 2019→2026; event markers (award, possession, mutation) |
| Compensation mismatch | `/parcel/Melathattaparai%7C227` | Finding 1977 (high) |
| Extent mismatch | `/parcel/Allikulam%7C30%2F5A` | Finding 1762: document extent vs map area |
| FMB map quality (overlap) | `/parcel/Melathattaparai%7C188` | Finding 2052: overlapping polygons |
| Land-class conflict | `/parcel/Ramasamypuram%7C84%2F3` | Finding 1337: "dry" land showing irrigation-like seasons |
| Changed vs controls after possession | `/parcel/Melathattaparai%7C157%2F1` | Finding 1152 (medium) |

(`%7C` = `|`, `%2F` = `/` in URLs.)

## D. Findings page: filters to try
| Set filters | Expected |
|---|---|
| Category **COMPENSATION_MISMATCH**, Severity **high** | 43 findings (e.g. 1977, 1830) |
| Category **EXTENT_MISMATCH**, Severity **high** | 21 findings (e.g. 1762, 1761) |
| Category **DOC_VERSION_CONFLICT** | 2 findings (1987, 7537): proposal vs sanction |
| Category **PV3_POST_POSSESSION_ACTIVITY**, Severity **medium** | 117 findings (a vigour drop vs controls) |
| Category **PV4_IDLE_LAND_BANK** | 79 blocks, ≈ 834 ha idle |
| Category **FMB_QUALITY**, Severity **high** | 5 findings (e.g. 2052, 2038) |
| Category **EXTRACTION_ERROR** | 32 low-severity items (values off by >10×, flagged for review) |
| Village **Allikulam** | The village with the most findings (449) |
| Evidence level **block** | Findings based on block-level documents (e.g. payments without survey numbers) |

Open any row → **Evidence pack** → **Export JSON**.

## E. Map workspace: combinations
| Colour by | Season / layers | Expected |
|---|---|---|
| Acquisition stage | — | Mostly MUTATION/POSSESSION colours; the legend lists the stages |
| Number of findings | — | Hot spots (Allikulam, Melathattaparai) |
| Land-use state | 2021-rabi vs 2021-summer | Green vs beige flip (seasonal cropping) |
| Land-use state | 2025-rabi | Still green after possession in many parcels (the reason we compare with controls) |
| any | Layers: Roads major + Substations | Infrastructure context |
| any | Layers: Parcels outside their survey | 28 parcels spilling outside their cadastral survey |

## F. Documents page
| Do | Expected |
|---|---|
| Filter type **AWARD_7_2** | ~286 award documents |
| Filter type **CHITTA** | Land-record extracts (e.g. document 2344) |
| Open an award document's page | The scanned Tamil page with boxes around extracted values |
| Look for the mismatch badge | Documents whose folder name disagrees with their content |

## G. Review queue
| Do | Expected |
|---|---|
| Open the first item | A Tamil page crop, AI candidate values, and the reason (e.g. handwriting, failed check, owner sample) |
| **Accept** (only on a practice item) | The item leaves the queue and becomes verified data |

## H. Quick facts to quote (all measured)
| Fact | Value |
|---|---|
| Documents / unique pages | 2,362 / 12,859 |
| Extracted rows / parcel facts / events | 26,269 / 42,380 / 15,342 |
| Parcels with linked document facts | 1,082 of 1,242 (87%) |
| Extraction accuracy on 10 fresh pages | survey 89.5%, owner 83.3%, extent 79.2% |
| Satellite | 381,294 parcel observations, 2019→2026 |
| Findings | 1,388 across 8 categories |
| Agent evaluation | 8/8 answers matched SQL checks; the critic is always another vendor |
| AI cost | ≈ US$7.9 of AWS (US$100 event budget); all other models on free tiers |

## I. New data (upload and satellite refresh)
Test files: **`data/demo_uploads/`**. That folder's README lists each file, the document type to choose and the measured result. Run `uv run python scripts/demo_uploads.py cleanup` after the demo.
| Do | Expected |
|---|---|
| Upload `01_duplicate_award_b8.pdf` | "This document is already in the system" |
| Upload `06_sec32_notice_rescan_ramasamypuram.pdf`, Document type **3(2) notice** | 7 steps complete; 7 parcels linked (Ramasamypuram 106/…); 3 new findings |
| Upload `02_award_7_2_rescan_umarikottai.pdf`, Document type **Award 7(2)** | 8 parcels linked (Umarikottai 202/1A…); the classify step shows "AI suggested GO" (honest) |
| Upload `07_other_scheme_gazette_rescan.pdf` | Recognised as another scheme; nothing extracted |
| Upload `11_corrupt_truncated.pdf` | "Cataloguing" fails: "could not be opened as a PDF" |
| Upload `09_fake_pdf_text.pdf` | Rejected immediately |
| Overview → Satellite data freshness → **Check for new satellite images** | Latest image date (2026-09-26); "0 new scenes", or the new scene count with parcels/seasons updated |

**Before the demo:** the AWS table reader must be signed in (`make aws-login`, ~2 h). Without it, "Reading tables" fails with a clear message.

## J. The downloadable deliverable
After any upload finishes, click **Download report (PDF)** or **Download rows (CSV)**. The sections are explained in `docs/ui/04-report-guide.md`. Show `data/demo_uploads/working/reports/06_AWARD_7_2_ramasamypuram.pdf` as a ready example: 10 linked parcels with their legal stage, and findings with plain explanations.
