---
name: phase4-agentic-core
description: Phase 4 (Day 4–8) — the Task-1 agentic runtime: typed RunState, LangGraph graph (intake, planner, router, executors, verifier, adversarial critic, judge, presenter, recover), tool registry and 13 tool contracts (incl. satellite and paper-vs-planet tools), guarded text-to-SQL/spatial SQL, pseudonymisation gateway, hash-chained ledger, telemetry/shadow cost, FastAPI + SSE contracts, fixtures-based offline tests. Load for orchestration, routing integration, verification, ledger or API-contract work.
---

# Phase 4 — Agentic Core

Also load `free-model-router` and `land-domain-knowledge`.

**Owners:**
- agent-architect: graph, tools, verifier, ledger
- backend-engineer: API, SSE, run store

**This phase runs in parallel with P2 and P3.** Build against fixture KG data (`tests/fixtures/kg_small.sql`: 2 villages, 40 parcels, synthetic facts), and switch to the real KG as P2 lands.

## Package layout
```
app/agent/state.py        RunState, StepRecord, Claim, ExecutionPlan, WorkspaceSpec (Pydantic; JSON-schema export)
app/agent/graph.py        LangGraph StateGraph wiring + Postgres checkpointer
app/agent/nodes/{intake,planner,dispatch,execute,verify,critic,judge,present,recover}.py
app/tools/registry.py     Tool(name, description, input_schema, output_schema, privacy_tier, fn)
app/domains/land_acquisition/{tools.py,prompts/,glossary.md,schemas/}
app/domains/agri_claims/  (P5)
app/ledger/               append(), verify_chain(), CLI
app/api/                  FastAPI routers
```

## Node contracts
- **intake**
  - Detects modalities: text, pdf, image, geojson, csv.
  - Extracts slots (villages via aliases, survey patterns, stages, thresholds, units) **deterministically first**, and uses an LLM only for residual ambiguity.
  - Emits `missing_info[]`. Blocking gaps produce a `clarify` event; non-blocking gaps become assumptions.
- **planner** (`plan` task)
  - Input: the request, slots, the tool catalog (names, descriptions and I/O schemas), a KG schema summary, and the conversation's workspace spec when modifying.
  - Output: `ExecutionPlan{goal, steps[{id, tool|llm_task, args, depends_on[], expected_output_schema, verify: bool, rationale}], outputs[]}`.
  - Limits: at most 12 steps. The plan is validated (the DAG is acyclic, tools exist, args validate) and repaired once on failure.
- **dispatch/execute**
  - Runs ready steps in parallel with asyncio, with 4 concurrent steps at most.
  - Each LLM step calls `router.call()`. Each tool step calls `registry.run()`.
  - Every result is stored by reference (`output_ref` → the run store), so that the state stays small.
- **verify**
  - Collects `Claim`s from step outputs, meaning every number or entity destined for a KPI, table, discrepancy or narrative.
  - Runs the deterministic checks from `plan.md` §5.7.
- **critic**
  - Uses a model from a vendor different from the producer, and receives the PSEUDO payload.
  - Returns `{claim_id, hypothesis, check: {tool, args}}`. The system executes the check.
- **judge**
  - Returns a verdict per claim (ACCEPT, DOWNGRADE, REROUTE, REVIEW) plus a calibrated confidence.
  - A REROUTE sends the step back through dispatch with the failing model or tool excluded, at most twice.
- **present**
  - Builds the `WorkspaceSpec` deterministically via `compose_workspace`.
  - Adds a bilingual narrative (`present` task, PSEUDO), then re-inserts names locally.
  - Every narrative number must reference a claim ID. The presenter prompt forbids new numbers, and a post-check enforces that.
- **recover** follows the §5.8 matrix. Each attempt is appended to `StepRecord.attempts`.

## Tools (implement all 13 from `plan.md` §5.5)
The satellite tools (`satellite_timeseries`, `satellite_chip`, `landuse_state`) wrap the eo-engineer's `planet/` package. `paper_vs_planet` and `evidence_pack` wrap P5. Stub all of them with fixtures until those phases land.
- **`sql_query`** (guarded text-to-SQL):
  - NL + schema card (tables, columns, enums, 5 example queries written for *different* questions than the eval set) → Qwen3.8 → `sqlglot` parse → AST checks:
    - one SELECT only
    - no DDL/DML
    - only allow-listed relations
    - `LIMIT ≤ 5000` injected
    - `statement_timeout = 5s`
  - Runs as `agent_ro`.
  - On an error: error-guided repair, at most 2 times.
  - Returns the SQL text for display.
- **`spatial_query`:** parameterised templates (buffer_within, intersects_layer, distance_to_nearest, area_by_group, filter_by_raster_stat). Generated PostGIS SQL is the fallback and goes through the same guard.
- **`extract_document`:** wraps the P2 pipeline for an uploaded file. It streams progress events, respects privacy tiers, and returns rows with evidence plus auto-matching (`match_parcels`).
- **Evidence:** every tool returns `evidence[]` of `{kind: page_bbox|sql|layer_feature|raster, ref, excerpt}`.

## Pseudonymisation gateway
Implement it per `free-model-router`. Include a property test: a PSEUDO payload never contains any owner name from `owner.name_variants`.

## Ledger (`app/ledger`)
- Hash rule: `hash = sha256(prev_hash + run_id + seq + node + canonical_json(payload_without_private))`. The genesis hash is `sha256("nilasaatchi-genesis")`.
- `make verify-ledger [RUN=…]` recomputes the chain and reports the first bad `seq`.
- Test: mutate a row's payload and assert that verification fails at that `seq`.

## API (backend-engineer)
- `POST /runs` takes `{request, attachments[], workspace_id?}`. Response: `run_id`. `GET /runs/{id}/events` is the SSE stream.
- SSE event types: `plan`, `step_start`, `step_end`, `verify`, `critic`, `judge`, `fallback`, `clarify`, `result`, `error`, `ledger`.
- `GET /runs/{id}` returns the full RunState (public part). `POST /runs/{id}/clarify`.
- Workspace endpoints: `GET/POST/PUT /workspaces`, `GET /workspaces/{id}/versions/{v}`, `POST /workspaces/{id}/export?fmt=geojson|csv|pdf|json`.
- Document endpoints: `GET /documents`, `GET /documents/{id}/pages/{n}.webp`, `GET /evidence/{extraction_id}`.
- Review endpoints: `GET /review-queue`, `POST /review/{id}`. The decision is written back as a verified fact with `review_status=human_verified`.
- Layer endpoints: `GET /layers/{name}.geojson?bbox=` (simplified by zoom), plus vector tiles if needed via `ST_AsMVT`.
- `make gen-schemas` → `web/src/api/schemas.js` (zod schemas from OpenAPI; the frontend is JavaScript, per D-011).

## Tests (offline, no quota)
- The fixture recorder captures real provider responses once (`make record-fixtures`). CI replays them.
- Graph tests:
  - a plan for each of the 12 dev queries validates
  - a forced 429 → fallback chosen
  - invalid JSON → repair → alternate model
  - SQL error → repair
  - critic success → REROUTE → ACCEPT with the alternate model
  - quota exhausted → local model
- Determinism: the same fixtures produce an identical route trace.

## Exit gate
```
make test && make eval-agent && make verify-ledger && make chaos-suite
```
Targets are in `plan.md` §8 P4. The report covers:
- a Mermaid diagram of the graph as built
- the route distribution per task
- agent metrics
- chaos results
- the PII audit, which must show 0
