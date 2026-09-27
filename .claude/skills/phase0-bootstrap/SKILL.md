---
name: phase0-bootstrap
description: Phase 0 (Day 0–1) — scaffold the repo, Python 3.12 via uv, docker-compose PostGIS+pgvector, Makefile, env/secrets hygiene, `make doctor` provider/quota probe, and the OCR bake-off spike that fixes routing thresholds. Load when starting the project or rebuilding the environment.
---

# Phase 0 — Bootstrap & Feasibility Spikes

**Goal:** a reproducible stack, verified free model access, and *measured* OCR routing thresholds.
**Owners:** data-engineer (T0.1–T0.3, T0.5), eo-engineer (T0.5b), agent-architect (T0.4, T0.6), qa-evaluator (T0.5 labels and review).

## T0.1 Scaffold
- Create the layout from `plan.md` §7.
- Add `.gitignore`: `.env`, `data/`, `*.sqlite`, `web/node_modules`, `__pycache__`, `docs/screens/raw/`.
- Run `git init` if absent. **Never commit** `Dataset/` or `Documents/`; add them to `.gitignore`.
- Environment:
  - `uv` (install with `curl -LsSf https://astral.sh/uv/install.sh | sh` if it is missing)
  - `uv python install 3.12`, then `uv init --python 3.12`
  - dependencies grouped as `core`, `ocr`, `geo`, `agent`, `dev`
- Pin versions in `pyproject.toml` / `uv.lock`: langgraph ≥1.2, litellm ≥1.10, paddleocr 3.7.x, paddlepaddle (CPU), rasterio, geopandas, shapely ≥2, fastapi, sse-starlette, pgvector, psycopg[binary], sqlglot, rapidfuzz, indic-transliteration, FlagEmbedding, pytest, hypothesis, ruff, mypy.
- **Pitfall:** the system Python is 3.14 and paddlepaddle has no 3.14 wheels. Always use `uv run`.

## T0.2 Services (`docker-compose.yml`)
- `db`: `postgis/postgis:16-3.4` plus `CREATE EXTENSION vector`. If the image lacks pgvector, use a Dockerfile that installs `postgresql-16-pgvector`. Volume `pgdata`, port 5432, healthcheck.
- `ocr-tools` (optional): an Ubuntu 24.04 image with `tesseract-ocr tesseract-ocr-tam tesseract-ocr-eng poppler-utils gdal-bin`, used when apt is unavailable on the host.
- `ollama` (optional profile `local-llm`).
- Later phases add the `api` and `web` services.

## T0.3 Makefile targets (stubs now; phases fill them in)
`setup doctor db-up db-reset migrate catalog classify ocr extract resolve raster load kg-check test eval eval-extract eval-match eval-agent eval-live e2e verify-ledger demo gen-schemas lint`

## T0.4 Secrets and `make doctor`
- Create `.env.example` with `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `COHERE_API_KEY`, and optionally `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `OPENTOPO_API_KEY`, `AWS_PROFILE`.
- **Keys already exist:** the user created GEMINI, MISTRAL, GROQ and COHERE on 2026-09-26. Never print them. Ask the user only to confirm the Mistral training opt-out.
- `make doctor`:
  - checks tool versions (python, uv, docker, node, tesseract/paddle import, gdal)
  - checks the DB connection
  - for each registry model: one minimal JSON-schema call, recording its latency, the model ID it resolves to, and the rate-limit headers (`x-ratelimit-*`) where present
  - verifies the Mistral opt-out if the API exposes it; otherwise records `trains_on_free_tier: true` until the user confirms
  - writes `data/doctor.json` and a human-readable table
- A provider with no key is `SKIPPED`, not `FAIL`. The chains must still have at least one eligible model per task.

## T0.5 OCR bake-off (the feasibility spike)
- **Mini golden set:** 20 pages across 7(2) award, 7(3) award, Form F, Form E, 3(2) notice, disbursement, and GO/AS. The qa-evaluator labels survey, subdivision, extent, owner and amount **from the images** in `eval/golden/mini.jsonl`.
- **Candidates:**
  - (a) Tesseract `tam+eng` on the full page, psm 6
  - (b) PaddleOCR `ta` on the full page
  - (c) grid-detected cell OCR (digit whitelist for numeric columns)
  - (d) ~~Mistral OCR~~ dropped (D-018: needs a billing method)
  - (e) Groq Qwen3.8 vision on the table crop
- **Measure** per field: exact match, CER, seconds per page, and quota units per page.
- **Output:** `docs/spikes/ocr-bakeoff.md`, which chooses the thresholds τ_text, τ_cell and τ_escalate, and gives expected P2 coverage within the §11.2 budget. Log it as `D-010`.
- The known baseline is Tesseract failing on table cells. Your job is to quantify that and choose the route.

## T0.5b Satellite spike reproduction (eo-engineer)
- Port `spikes/s2_ndvi_spike.py` to `planet/` using rasterio and pystac-client, and re-run it.
- It must reproduce the numbers in `docs/spikes/sentinel2-spike.md` within ±0.01 NDVI.
- If Earth Search is unreachable, test the Planetary Computer mirror and log the result.

## T0.6 Router skeleton
- Build `app/router/` (registry loader, eligibility filters, scorer, quota DB, circuit breaker, providers via LiteLLM, fixture recorder/replayer) with unit tests on a fake clock and a fake provider.
- The gateway can remain a stub, as long as the stub raises on any PII payload sent to a training-tier model.

## Exit gate (run it yourself)
```
make setup && make db-up && make migrate && make doctor && make test
make s2-spike && test -f docs/spikes/ocr-bakeoff.md && grep -q "D-010" docs/decisions.md
```
Send the gate report per `phase-gate`: the doctor table, bake-off numbers, the chosen routing, and quotas observed against the plan.
