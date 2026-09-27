-- 0002_documents.sql — data-engineer (T1.1/T1.2/T1.6). Owns: document, page.
-- Do not add village/parcel/survey/ref_layer/s2_* tables here — see 0003 (gis-engineer, D-022).

CREATE TABLE IF NOT EXISTS document (
    id                  bigserial PRIMARY KEY,
    sha256              text NOT NULL UNIQUE,
    md5                 text NOT NULL,
    dup_group_id        text NOT NULL,              -- = sha256 of the group leader (itself; one row per sha256)
    bytes               bigint NOT NULL,
    pages               integer NOT NULL,
    text_chars_per_page integer[] NOT NULL DEFAULT '{}',
    folder_label        text,                       -- immediate stage folder of the primary path (raw, a prior only)
    path                text NOT NULL,               -- primary (deterministic: lexicographically first) path
    all_paths           text[] NOT NULL DEFAULT '{}',-- every path (repo-relative) that hashes to this sha256
    zip_member          text,                        -- original zip-internal path, when sourced from a zip archive
    classified_type     text,                        -- content classifier result (pipeline/classify) -- separate from folder_label
    type_confidence     real,
    type_evidence       jsonb,
    scheme_relevance    text,                        -- allikulam | other_scheme | unknown
    unit_no             text,
    block_no            text,
    village             text,
    doc_no              text,
    doc_date            date,
    date_precision      text,                        -- day | month | year (blank-day dates get month precision)
    lang                text,
    status              text NOT NULL DEFAULT 'catalogued',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_dup_group_idx     ON document (dup_group_id);
CREATE INDEX IF NOT EXISTS document_folder_label_idx  ON document (folder_label);
CREATE INDEX IF NOT EXISTS document_classified_idx    ON document (classified_type);
CREATE INDEX IF NOT EXISTS document_scheme_idx        ON document (scheme_relevance);
CREATE INDEX IF NOT EXISTS document_village_idx       ON document (village);
CREATE INDEX IF NOT EXISTS document_all_paths_gin     ON document USING gin (all_paths);
CREATE INDEX IF NOT EXISTS document_type_evidence_gin ON document USING gin (type_evidence);

CREATE TABLE IF NOT EXISTS page (
    id            bigserial PRIMARY KEY,
    document_id   bigint NOT NULL REFERENCES document (id) ON DELETE CASCADE,
    page_no       integer NOT NULL,
    text_layer_ok boolean NOT NULL DEFAULT false,
    text_quality  real,
    preview_path  text,                 -- 150-dpi webp (D-021), rendered up front by `make catalog`
    img300_path   text,                 -- 300-dpi png, rendered on demand in P2 OCR (null here)
    ocr           jsonb,                -- filled in P2
    text          text,                 -- text-layer (or later OCR) text for this page
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED,
    embedding     vector(1024),         -- filled in P2 (BGE-M3)
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, page_no)
);

CREATE INDEX IF NOT EXISTS page_document_idx  ON page (document_id);
CREATE INDEX IF NOT EXISTS page_tsv_idx       ON page USING gin (tsv);
CREATE INDEX IF NOT EXISTS page_embedding_idx ON page USING hnsw (embedding vector_cosine_ops);
