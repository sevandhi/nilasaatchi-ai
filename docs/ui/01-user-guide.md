# NilaSaatchi AI: User Guide (for a first-time user)

This guide walks you through the web application page by page: what each screen is for, what every component shows, and how the pages connect. No prior knowledge is needed.

## Starting the application
```bash
make db-up      # database (PostGIS) on port 5439
make api        # backend API on http://localhost:8000
make web        # web app on http://localhost:5173  ← open this in the browser
```
The agent console uses AI models; the free tiers need the keys in `.env`.

## The screen layout (same on every page)
| Area | What it is |
|---|---|
| **Left menu** | 8 pages: 1 Overview · 2 Map workspace · 3 Parcel · 4 Findings · 5 Agent console · 6 Documents · 7 Models & routing · 8 Review queue |
| **Top bar: search box** | Ask the AI agent a question in English or Tamil from any page; it opens the Agent console |
| **Top bar: Chaos** | Simulates an AI provider failure (e.g. Gemini down) so you can watch the system fall back to another model |
| **Top bar: Demo mask** | ON by default. It hides landowner names ("Owner ••17"). Keep it ON when presenting |
| **Top bar: EN / தமிழ்** | Switches the menu and labels between English and Tamil (document data stays in its original language) |
| **Footer** | API/database health, AWS spend vs the US$100 event budget (Cost Explorer, which lags ~1 day, plus a live router estimate), last refresh time |

**Everywhere:** each number shows where it comes from (hover or read "source"). Uncertain results show confidence and caveats. Loading, empty and error states are shown instead of blank panels.

---

## Page 1: Overview ("what this system is")
**Purpose:** a 1-minute understanding of the whole system.
| Component | What it shows |
|---|---|
| Explanation paragraph | Paper vs Planet in plain words: read land-acquisition documents, link them to parcels, compare with satellite evidence |
| Pipeline diagram | Sources → Paper (documents) → Planet (satellite) → Knowledge graph → Agent → Findings. **Click a box to jump to its page** |
| KPI tiles | Live counts from the database: documents (2,362), pages (12,859), extracted rows (26,269), parcel facts (42,380), acquisition events (15,342), parcels (1,242), satellite observations (381,294), parcel-seasons (40,986), findings (1,388, by category), review queue (1,302), AWS spend vs the US$100 event budget. Database counts show the SQL behind them |

**Connects to:** every other page, via the diagram.

## Page 2: Map workspace
**Purpose:** see all 1,242 parcels of the Allikulam SIPCOT park and their context on a full-screen map. The controls (colour-by, season slider) float at the top left, the layer list at the top right and the legend at the bottom left.
| Component | What it does |
|---|---|
| Map | FMB parcels (subdivision polygons) and the park boundary on a free OpenStreetMap basemap |
| Layer toggles | Major/other roads, waterbodies, substations, rail, schools, SIPCOT parks, FMB overlaps (parcels drawn on top of each other), parcels outside their survey, control-group cells (never-acquired farmland 2–6 km outside the park, used for the satellite comparison) |
| Colour-by | **Acquisition stage (today)**, **acquisition stage by season** (follows the slider), **number of findings**, **land use in a season** (follows the slider), **change vs never-acquired farmland after possession**, **document link level** (parcel / survey / block-level / none) |
| Season slider | Pick a season from 2018-rabi (imagery starts Jan 2019, so partial) to 2026 (or press play). It recolours the two seasonal options: land use (cropped, bare/fallow, trees/scrub…) and the legal stage reached by then. Colours stay fixed per value; grey = not yet / no data |
| Legend | Always visible; explains the colours |
| Hover card | Parcel ID, village, area, stage, number of findings |
| **Click a parcel** | Opens that parcel on the **Parcel** page |

## Page 3: Parcel (the key page)
**Purpose:** everything we know about one parcel: what the papers say and what the satellite saw.
| Component | What it shows |
|---|---|
| Header | Parcel ID (Village\|survey/subdivision), village/unit/block, area (map vs document), current acquisition stage and days in it, evidence level (parcel or block), open findings |
| **Paper vs Planet timeline** | The green line is vegetation greenness (NDVI) from satellite, 2019→2026; dots are individual satellite observations; background bands show the season's land use (green = cropped, beige = bare/fallow); coloured markers at the top are **document events** (award, payment, possession, mutation…), with the date on hover and a number badge when several share a date. Tick "show BSI" to see the bare-soil index (ploughing) |
| Documents (linked extractions) | Every table row extracted for this parcel: the scanned page with a **box drawn around the value**, extracted fields, confidence, whether two AI reads agreed, arithmetic checks, review status |
| Satellite | Image chips (true colour/NDVI) at key dates; land-use state per season with confidence; the **comparison with the control group** before vs after each event, with a plain explanation and caveats |
| Findings | This parcel's findings; "Open evidence pack" shows the full proof |

**Tip:** open the parcel from the map, or type a URL like `/parcel/Melathattaparai%7C233`.

## Page 4: Findings
**Purpose:** the list of issues the system detected, with evidence.
| Component | What it does |
|---|---|
| Summary chart | Findings by category × severity (high/medium/low) |
| Filters | Dropdowns for category (plain names), severity, village, block and evidence level, each listing the values in the data with counts; **Clear filters** resets them |
| Table | 1,388 findings; click a row to open its evidence pack |
| Idle land bank | Possessed land showing no clearing or construction, by block (hectares, distance to road/substation) |
| **Evidence pack** | Opens at the **top of the page** when you click a finding. It starts with **"In simple words"**: an AI-written explanation for non-experts (what was found, why it matters, how sure we are, what to check next; a standard text if no AI model answers). Below: *paper side* (document page and extracted values), *planet side* (satellite numbers and chips), the checks, verdict, confidence and caveats. Export as JSON |

**Finding categories:**
- post-possession activity (vs controls);
- idle land bank;
- land-class conflict;
- extent mismatch (document vs map);
- compensation mismatch (amount ≠ acres × rate);
- document drift (proposal vs sanction);
- FMB map quality;
- extraction error.

## Page 5: Agent console (see the AI working)
**Purpose:** ask a question and watch the multi-model agent plan, work and check itself live.
| Component | What it shows |
|---|---|
| Question box + example chips | Type a question or click an example (English or Tamil) → **Run** |
| **Live plan & verification** | The plan steps as they are created; each step shows the tool or model, time, tokens and cost. **"why?"** shows the router's decision (which models were considered, filtered out and why, scores, the privacy tier) |
| Verifier checks | Deterministic checks (e.g. SQL re-run, confidence-interval check), each ✓ or ✗ |
| Critic challenges | A different AI vendor tries to prove a claim wrong, with its hypothesis, the test it ran and the outcome |
| Judge | The final verdict per claim (accept / downgrade / reroute / review) |
| Answer | Narrative + map/table/KPIs built from verified results |
| SQL / GIS operations | The exact SQL the agent ran (read-only, guarded) |
| Ledger | A tamper-evident hash chain of every step; **Verify ledger** proves nothing was altered |
| Run history | Earlier runs; click one to replay it |
| Chaos toggle (top bar) | Simulates a provider outage and shows the fallback |

## Page 6: Documents
**Purpose:** browse the 2,362 land-acquisition documents and how the AI classified them.
| Component | What it shows |
|---|---|
| Catalog table | Type, legal stage, scheme relevance, village/unit/block, date, pages; a badge when the folder name disagrees with the content |
| Filters / search | Dropdowns for village, document type and legal stage (live values with counts; new uploads appear automatically) and a search box (file name, document number or page text) |
| Page viewer | The scanned page, with boxes around extracted values |

## Page 7: Models & routing
**Purpose:** which AI models the system uses, why, and at what cost. **All data is live** from the API (router configuration, the latest health check, usage logs).
| Component | What it shows |
|---|---|
| AWS spend vs US$100 event budget | Cost Explorer (lags ~1 day) and the live router estimate |
| Model registry | Each model's role, vendor, open vs proprietary, whether it trains on free-tier data (privacy), limits, latest health check |
| Task → chain table | For each task (plan, critic, judge, SQL, table reading, satellite teacher…), the ordered list of models tried |
| Usage | Calls per model, success and fallback rates, p50/p95 latency, cost (shadow = list price, actual) |
| Privacy audit | Confirms that no owner data went to models that train on inputs |

## Page 8: Review queue
**Purpose:** a person checks pages the AI was unsure about (handwriting, blurred scans, numbers that don't add up, text read as a table).
| Component | What it does |
|---|---|
| Queue item | The page image, why a human is needed (in plain words), the AI's confidence, and how many rows were read from the page |
| **Reason** dropdown | Show one kind of problem at a time (live values with counts) |
| **Approve page** | Finishes the page: rows you corrected stay *corrected*, all others become *approved* |
| **Correct values** → **Save corrections** | A table of the rows read from the page; change only the wrong values (survey, extents, amounts, patta, land class). Saving **only stores** your corrections (AI's original kept for audit); the page stays open until you click **Approve page**. Owner names stay masked and can't be edited |
| **Reject page** | The page is unreadable or not usable: its rows are excluded |
| **Confirm: nothing to extract** | For pages where no rows were read |

## How the pages connect
```
Overview ──(diagram)──► any page
Map ──click parcel──► Parcel ──► evidence pack / document page / satellite chip
Findings ──click row──► evidence pack ──► Parcel
Agent console ──answer map/table──► Parcel / Findings
Documents ──page viewer──► extraction boxes (also shown on the Parcel page)
Review queue ──decisions──► data used by Parcel/Findings
```

## Adding new data
- **Documents page → Upload a document:** drop in a land-acquisition PDF and click *Upload & process*. A progress list shows each step: saving, cataloguing, classifying the document type, reading tables, loading the data, linking to parcels, checking findings. At the end you see the document type, pages, rows extracted, the linked parcels (clickable), new findings and the number of items sent to the Review queue. A PDF already in the system is recognised and not processed twice. *Recent uploads* lists earlier uploads.
- **Overview → Satellite data freshness:** shows the latest satellite image date and the number of scenes. *Check for new satellite images* fetches Sentinel-2 images newer than the latest one and updates the parcels' seasons, the comparison with the control group and the findings (a few minutes when there is a new image; seconds when there is none).
