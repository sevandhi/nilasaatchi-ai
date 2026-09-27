# NilaSaatchi AI: Testing and Verification Guide

How to test the application and **verify each screen shows correct data**. Each check lists what to do, what to expect, and how to cross-check it independently (the database, a file, or a command).

## 0. Setup
```bash
make db-up && make api    # API on :8000
make web                  # UI on :5173
make test                 # ~870 automated backend tests (offline)
make e2e                  # UI build + lint + Playwright browser tests
```
**Independent cross-check tool:** the database.

`docker compose exec -T db psql -U nila -d nilasaatchi -c "<SQL>"`

## 1. Automated tests (run first)
| Command | Expected |
|---|---|
| `make test` | ~876 passed, 0 failed (a few skipped/deselected are live-API tests) |
| `make e2e` | Build + lint clean; Playwright ~8 tests pass. The agent-console live test may skip when free AI quota is exhausted; that is a quota limit, not a bug |
| `make verify-ledger` | `ledger OK … chain intact` |
| `make doctor` | Every model status OK or SKIPPED (local/optional); vision models show the correct colour |
| `make aws-cost` | Spend well under the budget |

## 2. Page-by-page checks
### Overview
| Check | Expected | Cross-check |
|---|---|---|
| KPI tiles load | Documents 2,362, parcels 1,242, findings 1,388, facts 42,380 | `select count(*) from document;` / `parcel` / `finding` / `parcel_fact` |
| Database tiles show their SQL | The SQL text appears under the number | Run that SQL; the numbers match |
| Pipeline boxes are clickable | Each opens the right page | — |

### Map workspace
| Check | Expected | Cross-check |
|---|---|---|
| Parcels render | 1,242 polygons around Allikulam (~8.78 N, 78.02 E) | `curl -s localhost:8000/layers/parcel.geojson \| python3 -c "import json,sys;print(len(json.load(sys.stdin)['features']))"` |
| Each layer toggles on/off | Roads, water, substations, rail, schools, parks, FMB overlaps, outside-survey, controls appear/disappear | — |
| Colour-by "stage" | The legend lists stages; colours change | `select current_stage, count(*) from v_parcel_lifecycle group by 1;` |
| Season slider | Colours change between e.g. 2021-rabi (mostly cropped) and 2021-summer (mostly bare) | `select state,count(*) from parcel_season where ag_year=2021 and season='rabi' group by 1;` |
| Click a parcel | The Parcel page opens with that parcel_uid | — |

### Parcel page
Use **`/parcel/Melathattaparai%7C233`** and **`/parcel/Umarikottai%7C168`**.
| Check | Expected | Cross-check |
|---|---|---|
| Header area | Umarikottai\|168: geodesic area ≈ 1.67 ha | `select area_ha_gis from parcel where parcel_uid='Umarikottai\|168';` |
| Timeline line spans 2019→2026 | Continuous seasonal peaks each rabi; gaps only where no clear image | `curl -s "localhost:8000/parcels/Umarikottai%7C168/timeline"` → 564 series points, last ≈ 2026-09 |
| Event markers | Coloured markers with date tooltips; badge counts when several events share a date | `select e.stage, e.event_date, l.link_kind from acquisition_event_link l join acquisition_event e on e.id=l.event_id where l.parcel_uid='Umarikottai\|168';` (or the view `v_parcel_events`) |
| Documents panel | A page image with a box around the value; confidence shown; owner masked | Open `/evidence/<extraction_id>` in the API |
| Satellite DiD | Melathattaparai\|233 plough DiD ≈ +2.15 (CI 0.85–3.70) | `select metric,did,ci_lo,ci_hi from parcel_did where parcel_uid='Melathattaparai\|233' and event='t_possession' and season_scope='rabi';` |
| Findings list | Matches the DB | `select category,severity from finding where parcel_uid='Melathattaparai\|233';` |

### Findings
| Check | Expected | Cross-check |
|---|---|---|
| Summary chart totals | Sum = 1,388 | `select category,severity,count(*) from finding group by 1,2;` |
| Filter category = COMPENSATION_MISMATCH, severity = high | 43 rows | same SQL with a filter |
| Open finding **1977** | Compensation evidence: amount vs acres × rate, source page | `make evidence-pack ID=1977` (same numbers) |
| Idle land bank | 79 blocks, ≈ 834 ha total | `select count(*), round(sum(idle_ha)::numeric,1) from v_idle_land_bank;` |
| Evidence pack JSON export | A downloaded JSON equals the API output | `curl -s localhost:8000/findings/1977/evidence-pack` |

### Agent console
| Check | Expected |
|---|---|
| Click example "Verify Melathattaparai survey 233…" → Run | The plan appears within seconds; steps like `parcel_timeline`, `satellite_summary` |
| "why?" on a step | Candidates, scores, filtered reasons, privacy tier |
| Verifier section | Checks with ✓/✗ (e.g. `rerun_sql`, `did_ci_contains`) |
| Critic section | The critic's vendor ≠ the planner's vendor |
| Judge section | A verdict per claim |
| Ledger → Verify | "chain intact" |
| Numbers in the answer | Equal the DB values (DiD, stage, counts) |
| Tick **Chaos** → run again | The first event shows "chaos: gemini down"; the planner is a non-Gemini model (fallback). Needs another provider's free quota |

**Cross-check the agent:** `make eval-agent ONLY=a1` runs the same query headless; `data/eval/agent_run_final.json` shows 8/8 answers matching SQL references.

### Documents
| Check | Expected | Cross-check |
|---|---|---|
| Catalog count | 2,362 | `select count(*) from document;` |
| Type filter AWARD_7_2 | ~286 docs | `select count(*) from document where classified_type='AWARD_7_2';` |
| Mismatch badge | Shown where folder ≠ class | `folder_label` vs `classified_type` |

### Models & routing
| Check | Expected | Cross-check |
|---|---|---|
| Registry is live | Matches `config/models.yaml` + `data/doctor.json` | Edit nothing; compare the files |
| Usage numbers | Calls/latency per model | `data/router_log.sqlite` |
| Privacy audit | 0 PII calls to training-tier models | The API `/router/usage` shows 0 offenders |
| AWS spend | Cost Explorer + router estimate vs the US$100 budget | `make aws-cost` |

### Review queue
| Check | Expected | Cross-check |
|---|---|---|
| Items load with crops | Tamil crops, owners masked | `select count(*) from review_queue where status='queued';` |
| Accept one item (in a test DB or on a known item) | Status changes | Same SQL; `decided_at` set |

## 3. Privacy and safety checks (important to show)
- **Demo mask ON** → no real owner names anywhere. Toggle it OFF → still masked unless the server sets `ALLOW_DEMO_UNMASK=true`.
- The agent's read-only role cannot read owner names: `begin read only; set local role agent_ro; select * from owner limit 1;` → **permission denied**.
- No bank account numbers anywhere (the API strips them).

## 4. Known, expected behaviours (not bugs)
- AWS Cost Explorer lags ~1 day; the router estimate is live.
- Free AI quotas (Gemini/Groq) can run out for the day; the agent then falls back (e.g. Cohere), or a live test skips.
- Extraction accuracy is below target on fresh pages (survey 89.5%, owner 83.3%, extent 79.2%). Uncertain rows are in the Review queue.
- "Block-level" evidence (payments without survey numbers) is marked and never presented as a parcel-level finding.

## 5. New-data path
| Check | Expected | Cross-check |
|---|---|---|
| `uv run pytest tests/test_ingest_*.py tests/test_planet_refresh.py -q` | 47 passed | — |
| `npx playwright test e2e/ingest.spec.js` (in web/) | 5 passed | — |
| Upload a PDF that is already in the system | "This document is already in the system" | `curl -s localhost:8000/ingest/jobs?limit=1` → status `duplicate` |
| Overview → Check for new satellite images | Stages inventory → findings; "0 new scenes" if none | `select count(*), max(datetime) from s2_scene;` |
| Upload a new PDF | All stages done; linked parcels listed | **Needs the cloud table reader signed in** (AWS session, 2 h); otherwise the "Reading tables" step fails with a clear message |
