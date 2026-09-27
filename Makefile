# NilaSaatchi AI — every pipeline step is an idempotent make target.
SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(PATH)
UV := uv run
.DEFAULT_GOAL := help

help:            ## list targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/'

setup:           ## create venv (Python 3.12) and install all dependency groups
	uv sync

db-up:           ## start PostGIS+pgvector (host port 5439)
	docker compose up -d --build db
	@until docker compose exec -T db pg_isready -U nila -d nilasaatchi >/dev/null 2>&1; do sleep 1; done; echo "db ready"

db-down:         ## stop database (data kept)
	docker compose stop db

db-reset:        ## DESTRUCTIVE: drop the database volume (ask the user first)
	docker compose down -v

migrate:         ## apply SQL migrations in db/migrations (ordered)
	$(UV) python scripts/migrate.py

doctor:          ## probe tools, DB and every model in config/models.yaml
	$(UV) python scripts/doctor.py

test:            ## offline unit tests (no quota)
	$(UV) pytest -q -m "not live"

lint:            ## ruff
	$(UV) ruff check app pipeline planet scripts tests

ocr-bakeoff:     ## run the OCR bake-off on the eval/golden/mini pages
	$(UV) python -m spikes.ocr_bakeoff.run

catalog:         ## P1: catalog + dedup + page render cache (LIMIT=N for smoke runs)
	$(UV) python -m pipeline.catalog $(if $(LIMIT),--limit $(LIMIT))

classify:        ## P1: header pre-parse + doc-type/scheme classifier v1
	$(UV) python -m pipeline.classify $(if $(LIMIT),--limit $(LIMIT))

eval-classify:   ## P1: classifier accuracy vs eval/classify/labels.csv
	$(UV) python -m pipeline.classify.evaluate

load-gis:        ## P1: load all GeoJSON layers into PostGIS + precomputed distances
	$(UV) python -m pipeline.gis.load

kg-check:        ## P1: village-total reconciliation vs ground truth
	$(UV) python -m pipeline.gis.kg_check

raster:          ## P1: DEM/slope/WorldCover/JRC/GloFAS download, clip, zonal stats
	$(UV) python -m pipeline.raster $(if $(LIMIT),--limit $(LIMIT))

eval-extract:    ## P2: extraction accuracy on golden (and held-out) pages
	PYTHONPATH=. $(UV) python -m eval.extraction.run

load-extractions: ## P2: load per-page extraction JSON into the KG (idempotent)
	$(UV) python -m pipeline.load.extractions --dir data/extract/pages --doc-events

TYPES ?= AWARD_7_2,AWARD_7_3,FORM_F,LDR,CHITTA,GO,AS,LPS,DISBURSEMENT
BUDGET ?= 7
MAXPAGES ?= FORM_F=2
WORKERS ?= 10
extract-drain:   ## P2: Stage-B batch — DRY RUN by default; RUN=1 after user go-ahead; TYPES=… BUDGET=…
	$(UV) python -m pipeline.extract.batch --types $(TYPES) --workers $(WORKERS) --budget-usd $(BUDGET) --max-pages $(MAXPAGES) $(if $(RUN),--run)

match:           ## P5: link facts/events to parcel_uid (incremental; --all to redo)
	$(UV) python -m pipeline.match

match-eval:      ## P5: sample check of matches vs FMB
	$(UV) python -m pipeline.match.eval

views:           ## (re)apply SQL views
	$(UV) python -m pipeline.gis.views

findings:        ## P5: Paper-vs-Planet + document findings (run after match, views)
	$(UV) python -m pipeline.findings

evidence-pack:   ## P5: evidence pack JSON for one finding (ID=…)
	$(UV) python -m pipeline.findings.evidence_pack $(ID)

web:             ## P6: React dev server on :5173 (needs `make api`)
	cd web && npm run dev

web-build:       ## P6: production build of the UI
	cd web && npm run build

e2e:             ## P6: build + lint + Playwright smoke tests (needs `make api`)
	cd web && npm run build && npm run lint && npx playwright test

s2-inventory:    ## P1/P3: Sentinel-2 STAC inventory into s2_scene
	$(UV) python -m planet.stac.inventory

s2-extract:      ## P3: per-parcel indices for usable scenes (resumable)
	$(UV) python -m planet.extract $(if $(LIMIT),--limit $(LIMIT))

s2-features:     ## P3: smoothing + seasonal features into parcel_season
	$(UV) python -m planet.features $(if $(LIMIT),--limit $(LIMIT))

s2-classify:     ## P3: teacher labels -> LightGBM student -> event windows (free tiers; no Bedrock)
	$(UV) python -m planet.classify.teacher all $(if $(LIMIT),--limit $(LIMIT))
	$(UV) python -m planet.classify.student $(if $(LIMIT),--limit $(LIMIT))
	$(UV) python -m planet.events $(if $(LIMIT),--limit $(LIMIT))

s2-audit-pack:   ## P3: blind 50-item satellite audit pack for qa
	$(UV) python -m planet.classify.audit pack

eval-planet:     ## P3: score teachers/student against the qa audit
	$(UV) python -m planet.classify.audit score

aws-cost:        ## AWS spend vs the self-imposed cap (D-028); exit 2 at >=80%
	$(UV) python scripts/aws_cost.py

aws-login:       ## refresh the AWS SSO session (device code; user approves in browser)
	aws sso login --sso-session fai-tce --use-device-code --no-browser

BID ?=
bedrock-batch-upload:    ## D-042: upload data/bedrock_batch/$(BID)/{requests.jsonl,img/} to S3 (needs BID=..., SSO)
	$(UV) python -m infra.bedrock_batch.upload $(BID)

bedrock-batch-status:    ## D-042: parts done/total + error tally for batch $(BID) (needs SSO)
	$(UV) python -m infra.bedrock_batch.status $(BID)

bedrock-batch-download:  ## D-042: pull responses/*.jsonl for batch $(BID) into data/bedrock_batch/$(BID)/responses (needs SSO)
	$(UV) python -m infra.bedrock_batch.download $(BID)

aws-down:        ## D-042: tear down the bedrock-batch Lambda + S3 trigger + batch objects (asks for confirmation)
	$(UV) python -m infra.bedrock_batch.teardown

api:             ## P4: run the FastAPI app (uvicorn, reload) on :8000
	$(UV) uvicorn app.api.main:app --reload --port 8000

gen-schemas:     ## P4: OpenAPI -> web/src/api/schemas.js (zod schemas; D-011)
	$(UV) python scripts/gen_schemas.py

eval-agent:      ## P4: 8 agent_eval queries end-to-end, live free models, real KG (CHAOS=gemini:down ONLY=a1,a2)
	PYTHONPATH=. $(UV) python -m eval.agent.run $(if $(ONLY),--only $(ONLY)) $(if $(CHAOS),--chaos $(CHAOS))

verify-ledger:   ## P4: verify the SHA-256 hash-chained agent ledger (RUN=<run_id> for one run); exit 1 on tampering
	$(UV) python -m app.ledger verify $(if $(RUN),--run $(RUN))

# --- stubs, filled in by later phases ---
ocr extract resolve load eval eval-match eval-live demo:
	@echo "$@: not implemented yet (see plan.md phases)"; exit 1


.PHONY: web web-build e2e eval-agent verify-ledger findings evidence-pack match match-eval views load-extractions extract-drain aws-cost aws-login bedrock-batch-upload bedrock-batch-status bedrock-batch-download aws-down help setup db-up db-down db-reset migrate doctor test lint ocr-bakeoff catalog classify eval-classify load-gis kg-check raster s2-inventory api gen-schemas

s2-refresh:      ## incremental S2 refresh: new scenes after the latest s2_scene -> extract -> features -> classify (MAX_SCENES=N, DRY=1)
	$(UV) python -m planet.refresh --json $(if $(MAX_SCENES),--max-scenes $(MAX_SCENES)) $(if $(DRY),--dry-run)

.PHONY: s2-refresh

ingest:          ## new-document ingest: run one PDF headlessly through store->catalog->classify->extract->load->match->findings (FILE=path.pdf VILLAGE=hint)
	$(UV) python -m app.ingest.cli --file "$(FILE)" $(if $(VILLAGE),--village "$(VILLAGE)")

ingest-holdout-test: ## proves ingest works on "new" input: 2 already-processed docs re-ingested under a new hash, compared, then cleaned up (NO_CACHE=1 for a fresh, quota-costing read)
	$(UV) python scripts/ingest_holdout_test.py $(if $(NO_CACHE),--no-cache)

.PHONY: ingest ingest-holdout-test

demo-setup:      ## one-time setup on a new machine (deps, images, DB restore from bundle/, data)
	bash scripts/demo_setup.sh

demo:            ## start everything: database + API (:8000) + web UI (:5173); Ctrl-C to stop
	bash scripts/demo_run.sh

package:         ## build dist/nilasaatchi-demo.tar (code + DB dump + runtime data) for another machine
	bash scripts/package_demo.sh

cloud-export:    ## D-067: snapshot the read-only API (needs the local API on :8000) -> data/cloud/snapshot
	$(UV) python scripts/cloud_export.py

cloud-package:   ## D-067: build dist/cloud/lambda.zip (cloud app + read-only web build)
	bash scripts/cloud_package.sh

cloud-deploy:    ## D-067: sync data to S3 + create/update Lambda + HTTP API (AWS SSO; confirm with the user first)
	bash infra/cloud_demo/deploy.sh

cloud-down:      ## D-067: tear down the cloud demo (asks for confirmation)
	bash infra/cloud_demo/teardown.sh

.PHONY: cloud-export cloud-package cloud-deploy cloud-down demo demo-setup package

eval-routes:     ## P7 T6.2: 8 agent queries under routed / all-open / all-proprietary (live free tiers)
	bash scripts/eval_routes.sh
.PHONY: eval-routes
