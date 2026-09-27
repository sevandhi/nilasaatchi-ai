-- 0005_document_segments.sql — data-engineer (T1.6, D-025 taxonomy update).
-- Adds the fields the qa golden-set labelling showed were missing from 0002_documents.sql:
-- doc_type (form) vs stage (lifecycle stage the document evidences) are separate; payee_kind
-- and committee_level disambiguate BANK_INSTRUMENT / price-negotiation docs; document_segment
-- carries the page-segmented classification (a gazette file can hold several notifications,
-- each with its own scheme_relevance -- land-domain-knowledge §3).

ALTER TABLE document ADD COLUMN IF NOT EXISTS stage           text;  -- one of the 10 lifecycle stages (land-domain-knowledge §2), or null
ALTER TABLE document ADD COLUMN IF NOT EXISTS payee_kind      text;  -- court | owner | treasury | null
ALTER TABLE document ADD COLUMN IF NOT EXISTS committee_level text;  -- district | state | null

CREATE INDEX IF NOT EXISTS document_stage_idx ON document (stage);

CREATE TABLE IF NOT EXISTS document_segment (
    id               bigserial PRIMARY KEY,
    document_id      bigint NOT NULL REFERENCES document (id) ON DELETE CASCADE,
    seg_no           integer NOT NULL,              -- 1-based order within the document
    page_start       integer NOT NULL,
    page_end         integer NOT NULL,
    doc_type         text,
    type_confidence  real,
    type_evidence    jsonb,
    scheme_relevance text,                          -- allikulam | other_scheme | unknown, per segment
    stage            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, seg_no)
);

CREATE INDEX IF NOT EXISTS document_segment_document_idx ON document_segment (document_id);
CREATE INDEX IF NOT EXISTS document_segment_scheme_idx   ON document_segment (scheme_relevance);
