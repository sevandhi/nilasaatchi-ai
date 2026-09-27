# Agent ↔ API contract (P4) — owner: agent-architect, consumer: backend-engineer

Status: **v1.1, 2026-09-27** (v1.1 = additive: live route logs on `step_end` / `critic` / `judge`). Additive changes only; a breaking change bumps `CONTRACT_VERSION`
(`app.agent.CONTRACT_VERSION`) and is announced in `docs/decisions.md`.

The API layer (`app/api/**`) never imports graph internals. It uses only the symbols below.

## 1. Entry points (`from app.agent import ...`)

```python
async def run_request(request: str,
                      attachments: list[Attachment | dict] = (),
                      workspace_id: str | None = None,
                      *,
                      run_id: str | None = None,          # API may pre-allocate (uuid4 hex); else generated
                      domain: str | None = None,          # "land_acquisition" (default) | "agri_claims"; None = auto
                      lang: str | None = None,            # "en" | "ta"; None = detect
                      base_workspace: dict | None = None, # current WorkspaceSpec when modifying
                      ) -> AsyncIterator[AgentEvent]

async def resume_run(run_id: str, answer: str | dict) -> AsyncIterator[AgentEvent]
    # continue after a `clarify` event (POST /runs/{id}/clarify). Same event stream semantics.

def get_run_state(run_id: str) -> dict | None         # RunState.public_dict() of the latest checkpoint
def load_output(ref: str) -> dict                      # resolve a StepRecord.output_ref (run store)
def json_schemas() -> dict[str, dict]                  # {"RunState":…, "WorkspaceSpec":…, "AgentEvent":…, …}
CONTRACT_VERSION: str                                  # "1"
```

- `run_request` is an **async generator**. It yields events in order and finishes after exactly one
  terminal event: `result` (success, possibly degraded), `clarify` (paused, waiting for
  `resume_run`) or `error` (unrecoverable). It never raises for model/tool failures; it raises only
  for programmer errors (bad argument types).
- Cancellation: closing the generator (client disconnect) stops scheduling new steps; the partial
  state stays in the checkpointer.
- Concurrency: one generator per run. Different runs may run concurrently in one process.
- The API must not add owner names back into anything; names are re-inserted by the agent only in
  `RunState.narrative` / `WorkspaceSpec` fields it returns to the local UI (never in the ledger).

### Attachment
```json
{"id": "att_1", "kind": "pdf|image|geojson|csv|text", "name": "award.pdf",
 "path": "/abs/path/on/server", "media_type": "application/pdf", "size_bytes": 12345}
```
`path` is a server-local file the API already stored (e.g. `data/uploads/<run_id>/…`). `kind` may be
omitted; intake detects it from `media_type`/extension/content.

## 2. SSE events (`AgentEvent`)

Every event (the SSE `data:` payload is this JSON; SSE `event:` = `type`; SSE `id:` = `seq`):
```json
{"type": "plan", "run_id": "…", "seq": 7, "ts": "2026-09-27T10:00:00.123+05:30",
 "node": "planner", "data": { … type-specific … }}
```
`seq` is strictly increasing per run starting at 1 (usable as `Last-Event-ID`).

| type | node(s) | `data` |
|---|---|---|
| `plan` | planner | `{plan: ExecutionPlan, model_id, attempt, repaired: bool, template: bool, slot_filled: ["s1.parcel_uid", …]}` (`template` = deterministic fallback plan; `slot_filled` = required args copied from request slots) |
| `step_start` | execute | `{step_id, tool?, llm_task?, args_preview, attempt}` |
| `step_end` | execute | `{step_id, status: ok\|failed\|skipped, output_ref?, summary, n_rows?, latency_ms, model_id?, tool?, shadow_cost_usd, error?, actual_cost_usd, tokens_in, tokens_out, route: [RouteTraceEntry]}` (`route` = the step's LLM calls, `[]` for pure tools) |
| `verify` | verify | `{claims_checked, passed, failed, checks: [{claim_id, name, passed, detail}]}` |
| `critic` | critic | `{claim_id, hypothesis, check: {tool, args}, result: {refuted: bool, detail}, model_id, vendor, producer_vendor, route: [RouteTraceEntry], check_route: [RouteTraceEntry]}` (one per challenge; `check` = the test, `result` = its outcome; `route` = the critic model call, `check_route` = LLM calls made by the check; `data.skipped=true` with `reason` (+ `route`) when no eligible critic) |
| `judge` | judge | `{verdicts: [{claim_id, verdict: ACCEPT\|DOWNGRADE\|REROUTE\|REVIEW, confidence, reason}], model_id?, n_claims, route: [RouteTraceEntry]}` |
| `fallback` | any | `{step_id?, task?, from, to, reason}` — a model/tool fallback or a reroute (`reason` e.g. `429`, `schema_invalid`, `sql_error`, `critic_refuted`, `quota`) |
| `clarify` | intake / execute | `{question, missing: [slot…], options?: [str…]}` — terminal (paused) |
| `result` | present | `{state: RunState.public_dict(), workspace: WorkspaceSpec, degraded: bool}` — terminal |
| `error` | any | `{message, node, recoverable: false}` — terminal |
| `ledger` | any | `{seq, node, hash, n_entries}` — emitted when a node finishes; `seq`/`hash` = current ledger head of the run (every event above is appended to the ledger) |

Unknown event types must be ignored by clients (forward compatibility). Additional informational
type `status` `{node, message}` may appear.

## 3. RunState — public fields (`RunState.public_dict()`)

```text
run_id: str                 workspace_id: str|None        contract_version: "1"
request: str                lang: "en"|"ta"               domain: str
attachments: [Attachment]   status: queued|running|clarify|done|failed
created_at, updated_at: ISO-8601
slots: {villages[], parcel_uids[], surveys[], stages[], seasons[], years[], thresholds{}, units{}, polygons_ref?}
missing_info: [{slot, blocking: bool, question}]
assumptions: [str]
plan: ExecutionPlan|null
steps: {step_id: StepRecord}
claims: [Claim]
critic: [CriticChallenge]
verdicts: {claim_id: Verdict}
reroutes: int  (≤ 2)
workspace: WorkspaceSpec|null
narrative: {en: str, ta: str|null, claim_refs: [claim_id]}
route_trace: [RouteTraceEntry]
  RouteTraceEntry = {step_id, task, choice, reason, candidates[], filtered[{id,reason}], scores{model: total},
                     attempts[{model_id, outcome, error_kind, latency_ms, tokens_in, tokens_out}], latency_ms,
                     tokens_in, tokens_out, shadow_cost_usd, actual_cost_usd, privacy_tier, effective_tier}
                     (scores/tokens/actual_cost/tiers added in v1.1)
telemetry: {latency_ms, tokens_in, tokens_out, shadow_cost_usd, actual_cost_usd, llm_calls, tool_calls}
ledger_head: str|null       errors: [{node, message}]      degraded: bool
```
`RunState.private` (pseudonym reverse map, raw uploads) is **never** part of `public_dict()`,
events, the ledger or checkpoints sent over the wire.

### ExecutionPlan
```json
{"goal": "…", "steps": [{"id": "s1", "tool": "sql_query", "llm_task": null, "args": {…},
  "depends_on": [], "expected_output_schema": "ToolResult", "verify": true, "rationale": "…"}],
 "outputs": ["kpis", "map", "table", "chart", "timeline", "narrative"]}
```
≤ 12 steps; exactly one of `tool` / `llm_task` per step; DAG acyclic; `args` may reference an earlier
step's output as `"$s1.data.rows"` (dotted path).

### StepRecord
`{id, tool?, llm_task?, args, depends_on[], status: pending|running|ok|failed|skipped, output_ref?,
summary, n_rows?, attempts: [{n, by, outcome, error?, latency_ms, reason}], excluded: [model_or_tool_id],
model_id?, latency_ms, tokens_in, tokens_out, shadow_cost_usd, actual_cost_usd, started_at?, ended_at?}`

### Claim
`{id, step_id, kind: kpi|table_cell|discrepancy|finding|entity|narrative, subject, field, value,
unit?, evidence: [Evidence], producer: {kind: tool|model, id, vendor?}, checks: [{name, passed, detail}],
status: pending|verified|downgraded|review, verdict?, confidence}`

### Evidence (every tool returns `evidence[]`)
`{kind: page_bbox|sql|layer_feature|raster|satellite_chip|timeseries|document, ref, excerpt?,
bbox?: [x0,y0,x1,y1], page?: int, document_id?: int}`

## 4. WorkspaceSpec (`schemas/agent/WorkspaceSpec.json`, `spec_version: "1"`)

Deterministically built by `compose_workspace`. All data is inline (≤ 5 000 rows per table/layer).
```text
spec_version: "1"   workspace_id?: str   version?: int   title: str   domain: str
run_id: str         created_at: ISO      ledger_head?: str    lang: en|ta
kpis:    [{id, label, value, unit?, claim_id, status: verified|downgraded|review, confidence}]
map:     {bbox?: [minx,miny,maxx,maxy], layers: [{id, title, kind: parcels|polygons|points|reference,
          geojson?: FeatureCollection, ref_layer?: str, color_by?: str, legend?: [{value,label,color}]}],
          time_slider?: {field: "season", values: [str]}}
tables:  [{id, title, columns: [{key, label, type: string|number|date|bool}], rows: [obj], claim_ids: [..], sql?: str}]
charts:  [{id, title, type: bar|stacked_bar|line|pie, x, y: [str], data: [obj], claim_ids: [..]}]
timeline?: {subject, events: [{date, stage, label, evidence_ref?}], series: [{date, ndvi?, bsi?, valid?}]}
narrative: {en, ta?, claim_refs: [claim_id]}
evidence: [{claim_id, items: [Evidence]}]
verification: {summary: {accepted, downgraded, review, rerouted}, claims: [{claim_id, verdict, confidence, checks: [..], critic?: {...}}]}
sql:     [{step_id, sql}]
route_trace: [RouteTraceEntry]           caveats: [str]
```
Unknown keys may appear (additive); clients must ignore them.

## 5. Persistence

- Checkpoints: LangGraph Postgres checkpointer (`langgraph-checkpoint-postgres`) in `DATABASE_URL`,
  `thread_id = run_id`; falls back to in-memory when the DB is down (`degraded=true`).
- Step outputs: `data/runs/<run_id>/steps/<step_id>.json` via `load_output(ref)`; ref = `run://<run_id>/<step_id>`.
- Ledger: table `agent_ledger(run_id, seq, node, ts, payload jsonb, prev_hash, hash)` (migration
  `db/migrations/0012_agent_ledger.sql`, owner agent-architect). `python -m app.ledger verify [--run ID]`
  (`make verify-ledger [RUN=…]`) exits 1 and prints the first bad `seq` on tampering.
- Workspaces/runs tables (0011) belong to the backend; the backend stores `result.data.workspace`
  and may pass it back as `base_workspace` for modify-requests.

## 6. Headless use
`python -m app.agent.cli "request" [--attach file] [--domain agri_claims] [--jsonl]` prints events.
