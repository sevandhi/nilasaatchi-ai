# NilaSaatchi AI: Team Handbook

This folder explains **everything we built**: what it does, why it was built that way, how it works technically, what the results are, and what reviewers are likely to ask. Read it before any review.

> **One-line pitch:** *Every land-acquisition file tells a story on paper. NilaSaatchi asks the land itself to testify.*
> NilaSaatchi (நிலச் சாட்சி, "the land's witness") reads Tamil/English land-acquisition documents, links them to surveyed parcels, and cross-examines each parcel's paper claims against 7 years of free satellite imagery. We call this "Paper vs Planet".

## Reading order
| # | Document | What you learn | Must read |
|---|---|---|---|
| 01 | [Project overview](01-project-overview.md) | The problem, our idea, why it fits FarmwiseAI, current status | Everyone |
| 02 | [Data and domain](02-data-and-domain.md) | The dataset, the legal acquisition stages, Tamil terms, units, data defects we found | Everyone |
| 03 | [Architecture and tech stack](03-architecture-and-stack.md) | Components, repository layout, database, how the pieces connect | Everyone |
| 04 | [Model router and AWS](04-model-router-and-aws.md) | Which AI models we use and why, privacy tiers, fallback, cost control, AWS | Shivani (lead) |
| 05 | [Phase 1: data foundation](05-phase1-data-foundation.md) | Cataloguing, deduplication, document classifier, GIS loading, rasters | Preethi, Sevandhi |
| 06 | [Phase 2: document intelligence](06-phase2-document-intelligence.md) | OCR bake-off, Bedrock table reading, validation, evaluation sets, repair loops | Mohana |
| 07 | [Phase 3: satellite evidence](07-phase3-satellite-evidence.md) | Sentinel-2 time series, land-use model, control-group comparison | Sevandhi |
| 08 | [Phase 4: API and agent](08-phase4-api-and-agent.md) | FastAPI service, the agent loop, tools, verification, ledger | Preethi, Shivani |
| 09 | [Results, metrics and honesty log](09-results-and-honesty.md) | Every measured number, and every correction we made | Everyone |
| 10 | [Reviewer Q&A](10-reviewer-qa.md) | ~40 likely questions with answers | Everyone |
| 11 | [Glossary](11-glossary.md) | Terms (Tamil, legal, GIS, AI) | Reference |
| 12 | [How to run and demo](12-how-to-run.md) | Run on any laptop (Linux/macOS/Windows), the cloud link, what to show | Preethi |
| 13 | [Phases 5–7 as built](13-phases5-7-as-built.md) | Findings engine, the UI, uploads + report, satellite refresh, cloud demo, packaging, GitHub | Everyone |
| — | [As-built architecture](../architecture.md) | Diagrams of what runs today (local + AWS), components, model routing | Shivani, Preethi |

## Who owns what (from the proposal)
| Member | Workstream you must be able to explain in depth |
|---|---|
| **Shivani R**, team lead / agentic AI | Router, privacy tiers, verification design, agent, findings, quality gates, cloud demo (docs 03, 04, 08, 09, 13, architecture) |
| **Mohana G**, document intelligence | OCR, Bedrock table reading, normalisation, evaluation, repair loops, document uploads + report (docs 02, 06, 13) |
| **Sevandhi S**, GeoAI and satellite | GIS layers, FMB quality, Sentinel-2, land-use model, control-group DiD, satellite refresh (docs 02, 05, 07, 13) |
| **Preethi Shri R**, product / frontend / backend | Catalog, API, the 8-page UI, running the demo on any laptop and in the cloud (docs 03, 05, 08, 12, 13) |

## How the work was done
Following the proposal (section 13), the implementation was carried out by AI coding agents (Claude Code) under the team's direction, with each workstream reviewed against measured quality gates. Every decision is logged in `docs/decisions.md` (D-001…D-069), every number in `docs/metrics.md`, and the phase status in `docs/progress.md`. Being able to explain *why* each decision was made is what reviewers will test.

## Status at a glance (2026-09-28): all phases done
| Phase | Status |
|---|---|
| P0 Bootstrap and feasibility spikes | ✅ Done (qa review PASS) |
| P1 Data foundation | ✅ Done (classifier v2: stage 90%, type 80%, scheme 96%) |
| P2 Document intelligence | ✅ Done (Stage B: 5,796 pages → 42,380 facts; 87% of parcels linked) |
| P3 Satellite evidence | ✅ Done at core scope (control-group DiD) |
| P4 Agentic core | ✅ Router + API + agent loop (8/8 eval queries correct) |
| P5 Proof | ✅ Matcher (82%), lifecycle, 1,388 findings + evidence packs |
| P6 UI | ✅ 8-page workspace (`make demo`) |
| P7 validation, deploy and demo | ✅ Final metrics, read-only cloud demo on AWS Lambda + API Gateway, packaging (Linux/macOS/Windows), public GitHub repo |
| Beyond the plan | ✅ Upload new documents (with a downloadable verification report) and refresh satellite data |

**Links:** cloud demo https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/ · code https://github.com/sevandhi/nilasaatchi-ai · 8-minute script `docs/demo-script.md`
