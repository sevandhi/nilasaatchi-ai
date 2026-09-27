-- 0014 — findings engine (T5.3). One row per finding; `finding_key` makes runs idempotent (upsert).
-- evidence_level: parcel | block (block-level documents, D-052) | village | fmb.
CREATE TABLE IF NOT EXISTS finding (
    id              bigserial PRIMARY KEY,
    finding_key     text NOT NULL UNIQUE,
    category        text NOT NULL,
    severity        text NOT NULL CHECK (severity IN ('high','medium','low')),
    confidence      real NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    parcel_uid      text REFERENCES parcel(parcel_uid),
    village         text,
    unit_id         int,
    block_id        int,
    title           text NOT NULL,
    metrics         jsonb NOT NULL DEFAULT '{}'::jsonb,
    paper_evidence  jsonb NOT NULL DEFAULT '[]'::jsonb,
    planet_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    caveats         text[] NOT NULL DEFAULT '{}',
    evidence_level  text NOT NULL CHECK (evidence_level IN ('parcel','block','village','fmb')),
    verdict         text,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open','stale','closed')),
    rule_version    text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS finding_cat_idx ON finding(category, severity);
CREATE INDEX IF NOT EXISTS finding_parcel_idx ON finding(parcel_uid);
