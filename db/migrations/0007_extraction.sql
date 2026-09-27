-- 0007_extraction.sql — doc-intel-engineer (P2, D-029/D-032/D-033).
-- Owns: extraction, owner, owner_variant, extraction_owner, parcel_fact, acquisition_event,
-- review_queue, pseudo_salt, v_owner_pseudo.
-- Every extracted value keeps its evidence (doc, page, bbox, raw cells, extractor, model).
-- parcel_uid stays NULL until the P5 matcher resolves village|survey|sub_div (never across villages).
-- Owner names are PII: agent_ro gets column grants that exclude every name column and sees owners
-- only through v_owner_pseudo (salted-hash tokens).

-- ------------------------------------------------------------------ extraction (one row per record)
CREATE TABLE IF NOT EXISTS extraction (
    id                   bigserial PRIMARY KEY,
    document_id          bigint REFERENCES document (id) ON DELETE CASCADE,  -- NULL for eval pages outside the catalog
    page_id              bigint REFERENCES page (id) ON DELETE CASCADE,
    page_no              integer,
    page_ref             text,                        -- '<pdf path>#p<n>' or eval page id
    record_no            integer NOT NULL,            -- order of the record within its page extraction
    schema_name          text NOT NULL,               -- parcel_row | form_f | village_totals | payment_instrument | price_rate | errata_correction | page_header
    doc_type             text,
    row_json             jsonb NOT NULL,              -- normalised record (owner names removed; see extraction_owner)
    bbox                 real[],                      -- [x0,y0,x1,y1] page fractions
    bbox_method          text,
    raw_cells            jsonb,                       -- raw VLM cells (may contain owner text: no agent_ro grant)
    extractor            text NOT NULL,               -- vlm:primary | vlm:second | tesseract | text_layer
    model_id             text,
    privacy_tier         text NOT NULL CHECK (privacy_tier IN ('PUBLIC', 'PII')),
    confidence           real CHECK (confidence BETWEEN 0 AND 1),
    self_consistency     text CHECK (self_consistency IN ('pass', 'fail', 'n/a')),
    owner_read_status    text CHECK (owner_read_status IN ('vlm_agreed', 'vlm_single')),
    second_read_decision text CHECK (second_read_decision IN ('second_improved', 'reads_agree', 'kept_first')),
    review_status        text NOT NULL DEFAULT 'auto'
                         CHECK (review_status IN ('auto', 'auto_unverified', 'queued', 'approved', 'corrected', 'rejected')),
    page_status          text,                        -- accepted | accepted_unverified | second_read_accepted | needs_human | failed
    content_hash         text NOT NULL,               -- sha256 of the page image + extractor version (idempotency key)
    schema_version       text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_hash, record_no)
);
CREATE INDEX IF NOT EXISTS extraction_document_idx ON extraction (document_id);
CREATE INDEX IF NOT EXISTS extraction_page_idx     ON extraction (page_id);
CREATE INDEX IF NOT EXISTS extraction_schema_idx   ON extraction (schema_name, doc_type);
CREATE INDEX IF NOT EXISTS extraction_review_idx   ON extraction (review_status);
CREATE INDEX IF NOT EXISTS extraction_hash_idx     ON extraction (content_hash);
CREATE INDEX IF NOT EXISTS extraction_row_gin      ON extraction USING gin (row_json jsonb_path_ops);

-- ------------------------------------------------------------------ owners (PII)
CREATE TABLE IF NOT EXISTS owner (
    id              bigserial PRIMARY KEY,
    owner_key       text NOT NULL UNIQUE,             -- ASCII matching key (pipeline.normalize.names)
    canonical_name  text NOT NULL,                    -- PII
    variants        text[] NOT NULL DEFAULT '{}',     -- PII: printed spellings seen
    relation        text CHECK (relation IN ('father', 'husband', 'mother', 'care_of')),
    relation_name   text,                             -- PII
    legal_heirs     boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS owner_name_trgm ON owner USING gin (canonical_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS extraction_owner (
    extraction_id   bigint NOT NULL REFERENCES extraction (id) ON DELETE CASCADE,
    owner_id        bigint NOT NULL REFERENCES owner (id) ON DELETE CASCADE,
    position        integer NOT NULL,                 -- order within the owner cell
    raw_text        text,                             -- PII: printed text of this owner
    PRIMARY KEY (extraction_id, position)
);
CREATE INDEX IF NOT EXISTS extraction_owner_owner_idx ON extraction_owner (owner_id);

-- ------------------------------------------------------------------ parcel facts (claims from paper)
CREATE TABLE IF NOT EXISTS parcel_fact (
    id                bigserial PRIMARY KEY,
    parcel_uid        text REFERENCES parcel (parcel_uid),   -- NULL until matched (P5)
    village           text,                                  -- canonical village (config/aliases.yaml)
    survey_no         text,
    sub_div           text,
    survey_ref        text,                                  -- 'village|survey|sub_div' (normalised; sub_div may be empty)
    fact_type         text NOT NULL,                         -- extent_ha | extent_ac | cents | classification | amount_rs | patta_no | owner | total_extent_ha | rate_per_acre | boundary
    value_num         numeric,
    value_text        text,
    unit              text,
    extraction_id     bigint NOT NULL REFERENCES extraction (id) ON DELETE CASCADE,
    doc_type          text,
    valid_from        date,
    supersedes_id     bigint REFERENCES parcel_fact (id),    -- errata "should read as" supersedes "published as"
    match_candidates  jsonb NOT NULL DEFAULT '[]',
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (extraction_id, fact_type)
);
CREATE INDEX IF NOT EXISTS parcel_fact_uid_idx     ON parcel_fact (parcel_uid);
CREATE INDEX IF NOT EXISTS parcel_fact_ref_idx     ON parcel_fact (village, survey_no, sub_div);
CREATE INDEX IF NOT EXISTS parcel_fact_type_idx    ON parcel_fact (fact_type);
CREATE INDEX IF NOT EXISTS parcel_fact_unmatched   ON parcel_fact (survey_ref) WHERE parcel_uid IS NULL;

-- ------------------------------------------------------------------ acquisition events
CREATE TABLE IF NOT EXISTS acquisition_event (
    id               bigserial PRIMARY KEY,
    parcel_uid       text REFERENCES parcel (parcel_uid),    -- NULL until matched (P5)
    village          text,
    survey_no        text,
    sub_div          text,
    survey_ref       text,                                   -- NULL for document-level events (whole unit/block)
    unit_no          text,
    block_no         text,
    stage            text NOT NULL,                          -- lifecycle stage (land-domain-knowledge §2)
    sub_stage        text,                                   -- e.g. award_7_2 | award_7_3 | handover | deposit
    event_date       date,
    date_precision   text CHECK (date_precision IN ('day', 'month', 'year')),
    source           text NOT NULL,                          -- classification | extraction
    document_id      bigint REFERENCES document (id) ON DELETE CASCADE,
    extraction_id    bigint REFERENCES extraction (id) ON DELETE CASCADE,
    amount           numeric,
    event_key        text NOT NULL UNIQUE,                   -- idempotency key
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acquisition_event_uid_idx   ON acquisition_event (parcel_uid);
CREATE INDEX IF NOT EXISTS acquisition_event_ref_idx   ON acquisition_event (village, survey_no, sub_div);
CREATE INDEX IF NOT EXISTS acquisition_event_stage_idx ON acquisition_event (stage, event_date);
CREATE INDEX IF NOT EXISTS acquisition_event_doc_idx   ON acquisition_event (document_id);

-- ------------------------------------------------------------------ review queue
CREATE TABLE IF NOT EXISTS review_queue (
    id              bigserial PRIMARY KEY,
    content_hash    text NOT NULL,                           -- page extraction
    document_id     bigint REFERENCES document (id) ON DELETE CASCADE,
    page_id         bigint REFERENCES page (id) ON DELETE CASCADE,
    page_ref        text,
    extraction_id   bigint REFERENCES extraction (id) ON DELETE CASCADE,  -- NULL = whole page
    reason          text NOT NULL,                           -- self_consistency_fail | handwriting | blurred_newsprint | prose_as_table | owner_sample | vlm_failed
    detail          jsonb NOT NULL DEFAULT '{}',
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'decided', 'dismissed')),
    decided_value   jsonb,
    decided_by      text,
    decided_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (content_hash, reason)
);
CREATE INDEX IF NOT EXISTS review_queue_open_idx ON review_queue (status, reason);

-- ------------------------------------------------------------------ pseudonymised owner view
-- The salt lives in a table agent_ro cannot read; tokens are stable per database.
CREATE TABLE IF NOT EXISTS pseudo_salt (
    id    boolean PRIMARY KEY DEFAULT true CHECK (id),
    salt  text NOT NULL DEFAULT md5(random()::text || clock_timestamp()::text)
);
INSERT INTO pseudo_salt DEFAULT VALUES ON CONFLICT DO NOTHING;

-- Deliberately NOT security_invoker: the view runs with its owner's rights so agent_ro can see
-- the tokens without any grant on owner / extraction_owner. It returns no name text.
CREATE OR REPLACE VIEW v_owner_pseudo WITH (security_invoker = false) AS
SELECT eo.extraction_id,
       eo.position,
       'OWNER-' || left(md5(s.salt || ':' || o.owner_key), 10) AS owner_token,
       o.relation,
       o.legal_heirs
FROM extraction_owner eo
JOIN owner o ON o.id = eo.owner_id
CROSS JOIN pseudo_salt s;

-- ------------------------------------------------------------------ agent_ro grants (allow-list)
-- Column-level: no raw_cells (can hold owner text); row_json has owner names stripped by the loader.
GRANT SELECT (id, document_id, page_id, page_no, page_ref, record_no, schema_name, doc_type, row_json, bbox,
              bbox_method, extractor, model_id, privacy_tier, confidence, self_consistency, owner_read_status,
              second_read_decision, review_status, page_status, content_hash, schema_version, created_at)
    ON extraction TO agent_ro;
GRANT SELECT ON parcel_fact, acquisition_event TO agent_ro;
GRANT SELECT (id, content_hash, document_id, page_id, page_ref, extraction_id, reason, status, decided_at, created_at)
    ON review_queue TO agent_ro;
GRANT SELECT ON v_owner_pseudo TO agent_ro;
-- parcel_fact rows of fact_type 'owner' carry only the pseudonymous token in value_text (loader).
