# 12 · How to Run and What to Show

## 1. Three ways to see it
| Way | How | What works |
|---|---|---|
| **Cloud (no install)** | Open https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/ | Every page, map, parcel, finding, evidence pack, document image (read-only) |
| **Full app on Linux/macOS** | Unzip `nilasaatchi-demo.zip` → `bash scripts/demo_setup.sh` (once) → `bash scripts/demo_run.sh` | Everything, incl. uploads and the live agent |
| **Full app on Windows** | Install Docker Desktop → unzip to `C:\nilasaatchi-demo` → double-click `SETUP-WINDOWS.cmd` (once) → `START-WINDOWS.cmd` | Everything (see `WINDOWS.md`) |

The full app opens at **http://localhost:5173** (API at http://localhost:8000/docs). Details and troubleshooting are in `DEMO.md` and `WINDOWS.md`.

**Optional extras:**
- **Agent console:** paste the free keys (`GEMINI_API_KEY`, `GROQ_API_KEY`, `COHERE_API_KEY`) into `.env`.
- **Uploading new documents:** needs the team AWS login. The lead runs `make aws-login` and approves the device code; it lasts ~2 hours.

## 2. What to show reviewers (8 minutes)
Follow **`docs/demo-script.md`**. Its short version:
1. **Overview:** the problem and live counts.
2. **Map:** stages and seasons.
3. **Parcel Melathattaparai 233:** the Paper vs Planet timeline (the hook).
4. **A scanned page** with the value boxed.
5. **Finding 1977:** a compensation mismatch with evidence.
6. **Agent console:** plan, verifier, cross-vendor critic, judge, ledger.
7. **Upload** `data/demo_uploads/working/06_AWARD_7_2_ramasamypuram.pdf` (type *Award 7(2)*), then **download the report**.
8. **Models & routing:** 0 owner-data calls to training models.

Copy-paste inputs and expected results for every page are in `docs/ui/03-demo-test-data.md`, and what the downloadable report contains is in `docs/ui/04-report-guide.md`.

**After a demo with uploads:** `uv run python scripts/demo_uploads.py cleanup` (removes the test uploads and their history).

## 3. Developer commands (all idempotent, results cached)
| Area | Command | What it does |
|---|---|---|
| Run | `make demo` / `make demo-setup` | Start database + API + UI / one-time setup on a new machine |
| Tests | `make test` / `make e2e` | 928 backend tests / UI build + lint + Playwright |
| P1 | `make catalog`, `make classify`, `make load-gis`, `make raster` | Catalogue, classify, GIS, rasters |
| P2 | `make eval-extract`, `make extract-drain RUN=1`, `make load-extractions` | Extraction accuracy, bulk extraction, load |
| P3 | `make s2-inventory` → `s2-extract` → `s2-features` → `s2-classify`; `make s2-refresh` | Satellite pipeline; incremental refresh with new scenes |
| P4 | `make api`, `make eval-agent`, `make verify-ledger` | API; 8-query agent evaluation; ledger check |
| P5 | `make match`, `make views`, `make findings`, `make evidence-pack ID=…` | Matching, lifecycle views, findings, one evidence pack |
| New data | `make ingest FILE=…` | Upload a PDF from the command line |
| Cloud | `make cloud-export` → `cloud-package` → `cloud-deploy`; `make cloud-down` | Build and deploy / remove the read-only cloud demo (confirm with the lead first) |
| Package | `make package` | Build `dist/nilasaatchi-demo.zip` for other laptops |
| AWS | `make aws-login` / `make aws-cost` | Device-code login / spend |

## 4. Where things are
| Want | File |
|---|---|
| Plan and targets | `plan.md` |
| External proposal (+ measured status appendix) | `proposal.md` |
| As-built architecture | `docs/architecture.md` |
| Every decision and why (D-001…D-069) | `docs/decisions.md` |
| Every measured number | `docs/metrics.md` |
| Demo script / UI guides | `docs/demo-script.md`, `docs/ui/` |
| AWS resources | `infra/RESOURCES.md` |
| Code | https://github.com/sevandhi/nilasaatchi-ai |
