# NilaSaatchi AI: Paper vs Planet

**What the land-acquisition papers claim, checked against what the satellite saw.**

NilaSaatchi AI reads Tamil + English land-acquisition documents (notifications, awards, Form F, land delivery receipts, chitta). It links every extracted value to a parcel on the FMB survey map and compares it with Sentinel-2 satellite history from 2019 to today. The output is a list of evidence-backed **findings**, such as extent and compensation mismatches, document-version conflicts, land still farmed after possession, and idle acquired land. A multi-model **AI agent** answers questions about the data and checks its own answers.

Built for the FarmwiseAI Campus Product Challenge (Task 1 multi-model agentic AI, applied to the Task 2 dataset: Allikulam SIPCOT, Thoothukudi).

## Live demo
**Read-only cloud demo (AWS Lambda + API Gateway, Mumbai):** https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/. Browse every page, map, parcel and finding. Uploads, the live agent and review actions run in the full local app (`make demo`). The 8-minute walkthrough is in `docs/demo-script.md`.

## What it does
- **Paper:** OCR + AI table reading with arithmetic self-checks. Every value keeps its page, box, model and confidence, and uncertain rows go to a human Review queue.
- **Planet:** per-parcel NDVI/BSI/NDWI time series from Sentinel-2 (2019→2026), seasonal land-use states, and a comparison against never-acquired farmland (difference-in-differences).
- **Knowledge graph:** 1,242 parcels, 2,362 documents, 42,380 facts, 15,342 acquisition events and 1,388 findings in PostGIS, each finding with an evidence pack.
- **Agent:** planner → tools (guarded read-only SQL, spatial, timelines, evidence) → verifier → critic from a *different* vendor → judge, with a hash-chained audit ledger.
- **New data:** upload a PDF and it is read, linked to parcels and checked, with a downloadable **verification report** (PDF/CSV). An incremental satellite refresh fetches new Sentinel-2 scenes.
- **Workspace UI:** 8 pages (map, parcel timeline, findings, agent console, documents, models & routing, review queue).

## Quick start
```bash
make demo-setup   # one time on a new machine (see DEMO.md; needs the data bundle)
make demo         # database + API (:8000) + UI (http://localhost:5173)
```
Developer targets: `make test`, `make e2e`, `make eval-agent`, `make findings`, `make s2-refresh`, `make ingest FILE=…`.

## Models (free tiers + AWS Bedrock, routed by task and privacy tier)
| Role | Primary | Fallback |
|---|---|---|
| Table reading (owner data, PII) | AWS Bedrock Ministral 3 8B (vision) | Groq Qwen VL → review queue |
| Agent planner / judge | Gemini flash-lite (non-PII only) | gpt-oss / Qwen (Groq), Cohere Command A |
| Critic | Always a different vendor from the planner | — |
| Satellite land use | LightGBM student (local), trained on teacher labels | — |

Owner data never goes to a provider that trains on free-tier inputs, as enforced by the router's privacy tiers.

## Data
The FarmwiseAI dataset (`Dataset/`, `Documents/`) and derived data (`data/`) are **not** in this repository. Owner names are masked in the UI and never committed. Satellite data comes from the public Sentinel-2 archive (Earth Search STAC).

## Documentation
- `plan.md` (architecture and phases), `proposal.md` (business case), `docs/decisions.md` (decision log), `docs/metrics.md` (measured results)
- `docs/demo-script.md` (8-min demo), `DEMO.md` (run on a new machine), `docs/ui/` (user guide, testing guide, demo test data, report guide), `docs/team/` (team handbook)

## Honest limits
- Table extraction accuracy on unseen pages: survey number 89.5%, owner 83.3%, extent 79.2%. That is below target, so uncertain rows go to review.
- Automatic document-type detection without folder names is weak; the upload form lets the user declare the type.
- Satellite findings are **signals for field verification**, not legal conclusions.
