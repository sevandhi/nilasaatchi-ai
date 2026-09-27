# 03 · Architecture and Tech Stack

## 1. The big picture
```mermaid
flowchart LR
  subgraph Sources
    D[Scanned + digital LA PDFs]
    G[FMB / cadastral / GIS layers]
    S[Sentinel-2 2019→ open data]
    R[DEM · WorldCover · JRC · GloFAS]
  end
  subgraph Paper[Paper pipeline]
    C[Catalog + dedup] --> K[Doc classifier v2] --> X[Table extraction via Bedrock + validation] --> L[Loader]
  end
  subgraph Planet[Planet pipeline]
    I[STAC inventory] --> O[Per-parcel indices] --> F[Seasonal features] --> M[Land-use model] --> DID[Control-group DiD]
  end
  KG[(PostGIS knowledge graph)]
  RT[Model router: privacy · quota · fallback · spend guard]
  API[FastAPI + SSE]
  AG[Agent: planner → tools → verifier → judge → presenter]
  D --> Paper --> KG
  G --> KG
  S --> Planet --> KG
  R --> KG
  Paper --- RT
  Planet --- RT
  AG --- RT
  API --> AG --> KG
```

## 2. Tech stack and the reason for each choice
| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 via `uv` | The system Python 3.14 has no PaddleOCR wheels; `uv` gives reproducible environments |
| Database | PostgreSQL 16 + **PostGIS** + **pgvector** (Docker, port 5439) | Spatial SQL (areas, buffers, intersections) and vector search in one store. LLMs know PostGIS SQL well. Ports 5432/5433 are used by other services on this machine |
| Geo | GeoPandas, Shapely 2, pyproj, rasterio, pystac-client | Standard open-source GIS; windowed reads of cloud-optimised GeoTIFFs |
| OCR | Tesseract 5 `tam+eng` (in a Docker image) | Free and local; good on Tamil prose/headers (not tables) |
| Table reading | **AWS Bedrock Ministral 3 8B (vision)** | Won the bake-off; private (AWS doesn't train on customer data); cheap (~$0.0005/page) |
| ML | LightGBM (land-use "student"), numpy/scipy (Whittaker smoothing) | Fast, CPU-only, explainable |
| Agent | LangGraph + Pydantic | Explicit state graph, typed state, checkpointing |
| Model access | Own router on top of LiteLLM / boto3 / Cohere v2 | Privacy tiers, quotas, fallback, spend guard, logging |
| API | FastAPI + server-sent events (SSE) | Streams live agent progress to the UI |
| Frontend (planned) | React + JavaScript (JSX), MapLibre, ECharts | Team choice (D-011); free basemap |
| Cloud | AWS ap-south-1 (Team 49): Bedrock, S3, Lambda, DynamoDB | FarmwiseAI's approved services; no EC2/RDS allowed |

## 3. Repository map (what lives where)
```
plan.md · proposal.md · CLAUDE.md          plan, external proposal, agent operating guide
config/models.yaml                         model registry + task chains (router config)
db/migrations/0001…0012                    database schema (see §4)
pipeline/catalog|classify|gis|raster       Phase 1
pipeline/extract|normalize|load            Phase 2
planet/stac|extract|features|classify|controls|events|chips   Phase 3
app/router/                                model router (Phase 0/4)
app/api/  app/workspace/  app/export/      FastAPI service (Phase 4)
app/agent/  app/tools/  app/ledger/        agent graph + tools (Phase 4, partial)
infra/bedrock_batch/                       Lambda batch worker (built, waiting on AWS permission)
spikes/                                    feasibility experiments (OCR bake-off, S2 spike)
eval/                                      golden, held-out, dev, dev2, classify labels, planet audit
docs/                                      decisions, metrics, progress, spikes, reviews, team/
tests/                                     ~750 automated tests
data/                                      (git-ignored) caches, page images, rasters, results
```

## 4. Database (the knowledge graph)
| Table | Holds |
|---|---|
| `village`, `parcel` (PK `parcel_uid`), `survey`, `ref_layer_*` | GIS base, with precomputed distances to roads/substations/rail and areas (geodesic) |
| `fmb_qa_overlap`, `fmb_qa_outside_survey` | FMB quality issues |
| `document`, `page`, `document_segment` | Catalog + classification (form, stage, page sections) |
| `extraction`, `parcel_fact`, `acquisition_event`, `owner`, `review_queue` | Extracted facts with evidence; owner names hidden from the agent via `v_owner_pseudo` |
| `s2_scene`, `parcel_obs`, `parcel_season`, `planet_teacher_label`, `parcel_event_window`, `parcel_did` | Satellite pipeline outputs |
| `workspace`, `workspace_version`, `run`, `run_event`, `agent_ledger` | API/agent state and the tamper-evident ledger |

The agent queries through a read-only role **`agent_ro`**, which cannot see owner names or write anything.

## 5. Cross-cutting engineering rules
- **Idempotent `make` targets**: every step can be re-run; results are cached by content hash.
- **Decision log** (`docs/decisions.md`), **metrics log** (`docs/metrics.md`), **progress board** (`docs/progress.md`).
- **Quality gates:** a phase closes only when its acceptance commands pass and an independent review passes (Phase 0 review: PASS).
- **Automated tests:** about 750, running offline with recorded model responses (no quota spent).
