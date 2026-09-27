-- 0012_agent_ledger.sql — hash-chained agent ledger (owner: agent-architect, plan §5.9, D-039)
-- hash = sha256(prev_hash || run_id || seq || node || canonical_json(payload)); genesis = sha256('nilasaatchi-genesis').
-- Payloads never contain RunState.private (pseudonym reverse map) or owner names; PII-bearing values are hashed.
CREATE TABLE IF NOT EXISTS agent_ledger (
    run_id     text        NOT NULL,
    seq        integer     NOT NULL CHECK (seq >= 1),
    node       text        NOT NULL,
    event_type text        NOT NULL,
    ts         timestamptz NOT NULL,
    payload    jsonb       NOT NULL,
    prev_hash  text        NOT NULL,
    hash       text        NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS agent_ledger_ts_idx ON agent_ledger (ts);
-- The agent SQL tool (agent_ro) gets no access to the ledger.
