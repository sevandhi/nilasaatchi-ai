# 08 · Phase 4: API and Agent

## 1. The API (✅ done): `app/api/`
The FastAPI service behind the React workspace. `make demo` starts it (port 8000; interactive docs at `/docs`).

| Endpoint | Purpose |
|---|---|
| `POST /runs` → `GET /runs/{id}/events` (SSE) | Start an agent run and stream live progress (plan, steps, verification, result) |
| `GET /runs/{id}`, `POST /runs/{id}/clarify` | Run state; answer a clarifying question |
| `GET/POST/PUT /workspaces`, `/versions/{v}`, `/export?fmt=geojson/csv/json/pdf` | Save, version and export dashboards |
| `GET /documents`, `/documents/{id}/pages/{n}.webp`, `/evidence/{id}` | Document list, page images, the evidence box behind a value |
| `GET /layers/{name}.geojson` | Map layers (parcels, FMB quality, roads, water…) |
| `GET /chips/{parcel_uid}/{date}.png` | Satellite image chips |
| `GET /review-queue`, `POST /review/{id}` | Human review of uncertain extractions |
| `GET /stats/overview`, `/parcels/{uid}` (+ `/timeline`, `/extractions`, `/satellite`, `/findings`) | Overview counts; everything about one parcel |
| `GET /findings`, `/findings/summary`, `/findings/{id}/evidence-pack`, `/idle-land` | Findings with filters, evidence packs, idle land bank |
| `GET /router/models`, `/router/usage`, `/router/chains`; `POST /ledger/verify` | Model registry, usage/cost, chains; verify the hash chain |
| `POST /ingest/documents`, `GET /ingest/jobs/{id}`, `GET /ingest/jobs/{id}/report?fmt=pdf\|csv` | Upload a document, watch its 7 steps, download the verification report (chapter 13) |
| `POST /ingest/satellite-refresh`, `GET /ingest/satellite/status` | Fetch new Sentinel-2 scenes; data freshness |
| `GET /examples`, `GET /health` | Example queries; health check |

**Built-in safety:**
- owner names are masked by default;
- bank-account-like fields are always stripped;
- the database role for agent queries is read-only.

**Verified:** 928 backend tests and 14 Playwright UI tests; every UI page runs on this API.

## 2. The agent (✅ built and evaluated): `app/agent/`
**Design** (from `plan.md` §5.4; the contract is in `app/agent/CONTRACT.md`):
```
intake → planner → router → tools (parallel) → verifier → critic → judge → presenter
                         ↑__________ reroute (max 2) if the critic's challenge succeeds
```
- **Planner:** turns the request into an explicit step plan (JSON), validated.
- **Tools:**
  - catalog search, document search, guarded SQL (SELECT-only, read-only role, 5-s timeout), spatial queries;
  - lifecycle status, satellite time series/chips/land-use state;
  - parcel matching, paper-vs-planet findings, evidence packs, workspace builder.
- **Verifier:** deterministic checks: recomputed areas, table sums, compensation = acres × rate, date sanity, "the value appears inside its evidence box".
- **Critic:** a model from a **different vendor** than the one that produced the claim. It must propose a *testable* reason the claim is wrong, and the system runs that test.
- **Judge:** accept / downgrade / reroute / send to review, with calibrated confidence.
- **Ledger:** every step is appended to a SHA-256 hash chain (`agent_ledger`); tampering with a row breaks the chain.
- **Domain packs:** `land_acquisition` (built and evaluated) and `agri_claims` (crop presence on a farm plot: scaffolded in `app/domains/`; uploading your own plots is **not** in this build). The graph stays the same and only tools and prompts change, which is the reusability argument.

**Status (27 Sep): ✅ working end-to-end on the real data.**
- **Tools on the real knowledge graph:** `sql_query` (guarded), `spatial_query` (within X km of roads/substations), `findings_query`, `evidence_pack`, `parcel_timeline`, `satellite_summary`.
- **Routing:**
  - planner: Gemini flash-lite → gpt-oss → Qwen → Cohere;
  - critic: always a *different vendor* from the planner;
  - judge: Gemini → Cohere;
  - SQL: Qwen → gpt-oss.
- **Evaluation** (`make eval-agent`), 8 queries (4 demo, 4 unseen incl. one in Tamil):
  - plans valid 8/8, the expected tool used 8/8;
  - **answers match SQL reference checks 8/8**;
  - 78.5% of claims verified, 24/24 critic challenges from another vendor;
  - median 50 s per query, actual cost $0 (shadow $0.31).
- **Chaos drill:** with Gemini "down", the planner falls back automatically.
- **Ledger:** `make verify-ledger RUN=…` confirms the hash chain is intact.
- **Limitations:**
  - free Gemini/Groq daily quotas can run out (the router then falls back to Cohere);
  - the agent excludes Bedrock by default to save money;
  - about 7,600 OCR-noise strings were being treated as "owner names" and are now filtered.

## 3. What we can already demonstrate for Task 1
- **Multiple models with distinct roles:** router log + `make doctor`.
- **Fallback:** router chains and the chaos hook.
- **Privacy tiers:** unit tests show PII never reaches training-tier models.
- **Cost awareness:** shadow cost vs actual cost per call, and the AWS spend guard.
- **Structured outputs:** JSON-schema validation on every model call.
