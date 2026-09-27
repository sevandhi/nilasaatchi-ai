-- 0015_ingest_job.sql — backend-engineer (new-document ingest path, plan.md phase4-agentic-core).
-- Owns: ingest_job. One row per POST /ingest/documents or POST /ingest/satellite-refresh call.
-- `stages` is an ordered jsonb array of {name, status, started_at, finished_at, detail}, mutated
-- in place as the background orchestrator (app/ingest) runs each stage. `result`/`error` mirror
-- the API Job schema documented in app/api/routers/ingest.py.

CREATE TABLE IF NOT EXISTS ingest_job (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('document', 'satellite')),
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed', 'duplicate')),
    filename     text,
    stages       jsonb NOT NULL DEFAULT '[]'::jsonb,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- inputs: path, village hint, max_scenes, ...
    result       jsonb,                                -- final stage-shaped result (see routers/ingest.py)
    error        text,
    document_id  bigint REFERENCES document (id),      -- set on catalog (new doc) or immediately (duplicate)
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingest_job_kind_idx     ON ingest_job (kind);
CREATE INDEX IF NOT EXISTS ingest_job_status_idx   ON ingest_job (status);
CREATE INDEX IF NOT EXISTS ingest_job_document_idx ON ingest_job (document_id);
