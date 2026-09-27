-- 0011_workspace_run.sql — workspace + run persistence (owner: backend-engineer, P4).
--
-- Aligned to app/agent/CONTRACT.md v1 (landed 2026-09-27, after this migration's first draft):
-- the ledger is `agent_ledger` (db/migrations/0012_agent_ledger.sql, owner agent-architect) — not
-- duplicated here. `workspace.spec` is the agent's `WorkspaceSpec` (app/agent/state.py: kpis, map,
-- tables, charts, timeline, narrative, evidence, verification, sql, route_trace, caveats) exactly
-- as returned in a `result` event's `data.workspace` (contract §5: "the backend stores
-- result.data.workspace and may pass it back as base_workspace for modify-requests"). It is stored
-- as opaque jsonb and validated by app/workspace/models.py (which imports the Pydantic type from
-- app.agent.state), so this migration needs no change when that schema gains fields (additive).
--
-- `run` mirrors the public parts of RunState (contract §3) needed to serve GET /runs/{id} without
-- depending on app.agent.get_run_state() being implemented yet; `run_event` persists every
-- AgentEvent (contract §2) in order so GET /runs/{id}/events can replay a finished or reconnecting
-- run — `seq`/`ts`/`node` come from the event itself (agent-assigned), not computed here.

CREATE TABLE IF NOT EXISTS workspace (
    id           text PRIMARY KEY,             -- uuid4 hex
    name         text NOT NULL DEFAULT 'Untitled workspace',
    version      integer NOT NULL DEFAULT 1,
    spec         jsonb NOT NULL,                -- agent WorkspaceSpec (app/agent/state.py)
    ledger_head  text,                          -- hash-chain head (agent_ledger.hash) at last save
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_version (
    workspace_id text NOT NULL REFERENCES workspace (id) ON DELETE CASCADE,
    version      integer NOT NULL,
    spec         jsonb NOT NULL,
    ledger_head  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, version)
);

CREATE TABLE IF NOT EXISTS run (
    id           text PRIMARY KEY,             -- uuid4 hex (contract: RunState.run_id)
    workspace_id text REFERENCES workspace (id) ON DELETE SET NULL,
    request      jsonb NOT NULL,               -- {request, attachments[], domain?, lang?}
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'clarify', 'done', 'failed')),
    state        jsonb NOT NULL DEFAULT '{}',  -- reconstructed public RunState-shaped snapshot
    result       jsonb,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_workspace_idx ON run (workspace_id);
CREATE INDEX IF NOT EXISTS run_status_idx ON run (status);

-- Every AgentEvent (contract §2: {type, run_id, seq, ts, node, data}) is persisted so
-- GET /runs/{id}/events can replay a finished (or reconnecting) run.
CREATE TABLE IF NOT EXISTS run_event (
    id         bigserial PRIMARY KEY,
    run_id     text NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    seq        integer NOT NULL,   -- agent-assigned (AgentEvent.seq), not computed here
    event      text NOT NULL,      -- AgentEvent.type: plan|step_start|step_end|verify|critic|judge|fallback|clarify|result|error|ledger|status
    payload    jsonb NOT NULL,     -- the full AgentEvent envelope
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);
CREATE INDEX IF NOT EXISTS run_event_run_idx ON run_event (run_id, seq);

-- No agent_ro grant here deliberately: `request`/`state` may echo user free text (which can name
-- an owner), and these are app-internal tables, not part of the KG schema the guarded sql_query
-- tool should expose.
