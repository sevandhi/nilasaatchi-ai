# 12 · How to Run and What to Show

## Setup (once)
```bash
make setup        # Python 3.12 env via uv
make db-up        # PostGIS + pgvector on localhost:5439
make migrate      # apply schema
make doctor       # probe every AI model (needs .env keys; AWS needs `make aws-login`)
make test         # ~750 offline tests
```

## Pipeline commands (all idempotent, results cached)
| Phase | Command | What it does |
|---|---|---|
| P1 | `make catalog` | Catalogue + dedup + page previews |
| P1 | `make classify` / `make eval-classify` | Classify documents / score vs 100 labels |
| P1 | `make load-gis` / `make kg-check` | Load GIS layers / reconcile area totals |
| P1 | `make raster` | DEM, slope, WorldCover, water, flood stats per parcel |
| P2 | `make eval-extract` (`--set golden|heldout|dev|dev2`) | Extraction accuracy on labelled pages |
| P2 | `make extract-drain` (add `RUN=1` to execute) | Stage B bulk extraction (dry run by default; budget-capped) |
| P2 | `make load-extractions` | Load extracted facts into the database |
| P3 | `make s2-inventory` / `s2-extract` / `s2-features` / `s2-classify` | Satellite inventory → indices → features → land-use states |
| P3 | `uv run python -m planet.controls did` / `report` | Control-group DiD and report |
| P4 | `make api` | Start the API on http://localhost:8000 (docs at /docs) |
| AWS | `make aws-login` / `make aws-cost` | Device-code login / spend vs cap |

## What to show reviewers (5-minute walkthrough)
1. **The problem:** open a scanned Tamil award page (`data/pages/…webp`) next to the parcel map layer (`/layers/parcel.geojson`).
2. **Paper:** show one page's extraction JSON (`data/extract/golden/g05.json`) with survey, extent, owner, amount, evidence box and checks.
3. **Honesty:** show the metrics table (`docs/metrics.md`): the held-out 96/91% and the correction log (doc 09).
4. **Planet:** show Melathattaparai 233's chips (`data/s2/chips/`) and its DiD row. Explain the control group.
5. **Router:** `make doctor` output (every model, latency, vision proof), and the AWS spend (`make aws-cost`).
6. **Scale:** `docs/progress.md` status board and `docs/decisions.md` (45 logged decisions).

## Where things are
| Want | File |
|---|---|
| Plan and targets | `plan.md` |
| External proposal | `proposal.md` |
| Every decision and why | `docs/decisions.md` |
| Every measured number | `docs/metrics.md` |
| Current status | `docs/progress.md` |
| Spike reports | `docs/spikes/` |
| Domain reference | `.claude/skills/land-domain-knowledge/SKILL.md` |
