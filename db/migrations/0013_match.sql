-- 0013 — parcel matcher (T5.1). parcel_uid on parcel_fact / acquisition_event is set only for a
-- single, unambiguous parcel. Multi-parcel links (survey-level expansion, survey lists such as
-- "26/1E,42/4C") live in the *_link tables, which the lifecycle views use.
ALTER TABLE parcel_fact       ADD COLUMN IF NOT EXISTS match_status text;
ALTER TABLE parcel_fact       ADD COLUMN IF NOT EXISTS match_village text;   -- canonical village used for matching
ALTER TABLE acquisition_event ADD COLUMN IF NOT EXISTS match_village text;
ALTER TABLE parcel_fact       ADD COLUMN IF NOT EXISTS match_score  real;
ALTER TABLE parcel_fact       ADD COLUMN IF NOT EXISTS matched_at   timestamptz;
ALTER TABLE acquisition_event ADD COLUMN IF NOT EXISTS match_status text;
ALTER TABLE acquisition_event ADD COLUMN IF NOT EXISTS match_score  real;
ALTER TABLE acquisition_event ADD COLUMN IF NOT EXISTS matched_at   timestamptz;
ALTER TABLE acquisition_event ADD COLUMN IF NOT EXISTS match_candidates jsonb NOT NULL DEFAULT '[]'::jsonb;

-- match_status: accepted | survey_level | list | ambiguous | no_candidate | no_ref | no_village
CREATE TABLE IF NOT EXISTS parcel_fact_link (
    fact_id    bigint NOT NULL REFERENCES parcel_fact(id) ON DELETE CASCADE,
    parcel_uid text   NOT NULL REFERENCES parcel(parcel_uid),
    link_kind  text   NOT NULL CHECK (link_kind IN ('parcel','survey_level','list_member')),
    score      real   NOT NULL,
    PRIMARY KEY (fact_id, parcel_uid)
);
CREATE TABLE IF NOT EXISTS acquisition_event_link (
    event_id   bigint NOT NULL REFERENCES acquisition_event(id) ON DELETE CASCADE,
    parcel_uid text   NOT NULL REFERENCES parcel(parcel_uid),
    link_kind  text   NOT NULL CHECK (link_kind IN ('parcel','survey_level','list_member')),
    score      real   NOT NULL,
    PRIMARY KEY (event_id, parcel_uid)
);
CREATE INDEX IF NOT EXISTS parcel_fact_link_uid_idx ON parcel_fact_link(parcel_uid);
CREATE INDEX IF NOT EXISTS acquisition_event_link_uid_idx ON acquisition_event_link(parcel_uid);
CREATE INDEX IF NOT EXISTS parcel_fact_match_status_idx ON parcel_fact(match_status);
CREATE INDEX IF NOT EXISTS acquisition_event_match_status_idx ON acquisition_event(match_status);

-- D-052: block-level evidence + thresholds synced from config/thresholds.yaml by the matcher
ALTER TABLE acquisition_event_link DROP CONSTRAINT IF EXISTS acquisition_event_link_link_kind_check;
ALTER TABLE acquisition_event_link ADD CONSTRAINT acquisition_event_link_link_kind_check
    CHECK (link_kind IN ('parcel','survey_level','list_member','block_level'));
CREATE TABLE IF NOT EXISTS threshold (key text PRIMARY KEY, value numeric NOT NULL, updated_at timestamptz DEFAULT now());
