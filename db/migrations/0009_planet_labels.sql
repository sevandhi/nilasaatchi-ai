-- 0009_planet_labels.sql — P3 T3.4/T3.6 (owner: eo-engineer).
-- planet_teacher_label: one row per (item, teacher). Teachers see PUBLIC satellite panels only (no owner
-- data). Every label keeps the teacher/model, prompt version, rationale (visual_evidence) and the panel
-- provenance (sha256, scene ids + hrefs), so the qa-evaluator can audit it from the same image.
CREATE TABLE IF NOT EXISTS planet_teacher_label (
    id              bigserial PRIMARY KEY,
    item_id         text NOT NULL,              -- '<parcel_uid>|<ag_year>|<season>'
    parcel_uid      text NOT NULL REFERENCES parcel(parcel_uid) ON UPDATE CASCADE,
    ag_year         smallint NOT NULL,
    season          text NOT NULL,
    teacher         text NOT NULL CHECK (teacher IN ('gemini', 'cohere', 'prior', 'second_opinion', 'qa_audit')),
    task            text,                       -- router task (satellite_teacher / satellite_second_opinion)
    model_id        text,                       -- router model id actually used (fallbacks are visible here)
    resolved_model  text,
    prompt_version  text,
    state           text CHECK (state IS NULL OR state IN ('cropped','irrigated_multi','perennial_veg','bare_fallow',
                                                           'cleared_or_built','water','insufficient_data')),
    confidence      real,
    rationale       text,                       -- VLM visual_evidence, or the rule reason for the prior
    caveats         text[] NOT NULL DEFAULT '{}',
    panel_sha256    text,
    panel_path      text,
    provenance      jsonb NOT NULL DEFAULT '{}',-- chip dates, scene ids, hrefs, router step_id, latency, tokens
    ok              boolean NOT NULL DEFAULT true,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, teacher)
);
CREATE INDEX IF NOT EXISTS planet_teacher_label_parcel ON planet_teacher_label (parcel_uid, ag_year, season);

-- Classification outputs on parcel_season (state, p_state, teacher_label, teacher_src, route, needs_field exist).
ALTER TABLE parcel_season
    ADD COLUMN IF NOT EXISTS label_set         text CHECK (label_set IS NULL OR label_set IN ('train', 'audit')),
    ADD COLUMN IF NOT EXISTS teacher_agreement text,       -- '3of3' | '2of3' | 'none'
    ADD COLUMN IF NOT EXISTS model_version     text,
    ADD COLUMN IF NOT EXISTS classify_meta     jsonb NOT NULL DEFAULT '{}',  -- probs, caps, route reason, caveats
    ADD COLUMN IF NOT EXISTS classified_at     timestamptz;

-- T3.6 event windows (document-level fallback dates until P2 provides per-parcel dates).
CREATE TABLE IF NOT EXISTS parcel_event_window (
    parcel_uid     text NOT NULL REFERENCES parcel(parcel_uid) ON UPDATE CASCADE,
    window_name    text NOT NULL CHECK (window_name IN ('pre_notification', 'pending', 'post_award', 'post_possession')),
    start_date     date NOT NULL,
    end_date       date NOT NULL,
    date_basis     jsonb NOT NULL DEFAULT '{}',  -- which event dates, their source and precision
    n_seasons      integer NOT NULL DEFAULT 0,
    state_counts   jsonb NOT NULL DEFAULT '{}',
    state_share    jsonb NOT NULL DEFAULT '{}',
    dominant_state text,
    seasons        jsonb NOT NULL DEFAULT '[]',  -- [{ag_year, season, state, p_state, route}]
    caveats        text[] NOT NULL DEFAULT '{}',
    events_version text,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (parcel_uid, window_name)
);

-- Public satellite-derived tables: readable by the guarded SQL tool role (no personal data).
GRANT SELECT ON planet_teacher_label, parcel_event_window TO agent_ro;
