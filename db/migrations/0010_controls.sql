-- 0010_controls.sql — D-035 control group, ploughing signature and difference-in-differences (owner: eo-engineer).
-- Control cells: non-acquired WorldCover-cropland pseudo-parcels (80 m) in a 2-6 km ring outside the park.
CREATE TABLE IF NOT EXISTS control_cell (
    cell_id       text PRIMARY KEY,
    geom          geometry(Polygon, 4326) NOT NULL,
    x0            double precision NOT NULL,    -- lower-left corner, EPSG:32643
    y0            double precision NOT NULL,
    sector        text,
    dist_km       real,                         -- distance to park boundary U FMB parcels
    worldcover    jsonb NOT NULL DEFAULT '{}',  -- class fractions (evidence for the selection)
    cell_version  text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS control_cell_geom ON control_cell USING gist (geom);

CREATE TABLE IF NOT EXISTS control_obs (
    cell_id         text NOT NULL REFERENCES control_cell(cell_id) ON DELETE CASCADE,
    scene_id        text NOT NULL,
    date            date NOT NULL,
    ndvi real, ndwi real, bsi real, ndbi real,
    valid_frac      real,
    n_px            integer,
    n_total         integer,
    low_support     boolean,
    geom_mode       text,
    extract_version text,
    PRIMARY KEY (cell_id, scene_id)
);

CREATE TABLE IF NOT EXISTS control_season (
    cell_id       text NOT NULL REFERENCES control_cell(cell_id) ON DELETE CASCADE,
    ag_year       smallint NOT NULL,
    season        text NOT NULL,
    features      jsonb NOT NULL DEFAULT '{}',
    plough_signal real,
    plough_date   date,
    PRIMARY KEY (cell_id, ag_year, season)
);

-- Parcel-side: ploughing signature + control-relative metrics per parcel-season.
ALTER TABLE parcel_season
    ADD COLUMN IF NOT EXISTS plough_signal real,
    ADD COLUMN IF NOT EXISTS plough_date   date,
    ADD COLUMN IF NOT EXISTS relative      jsonb NOT NULL DEFAULT '{}';

-- Difference-in-differences of (parcel - control) before vs after each acquisition event (for P5).
CREATE TABLE IF NOT EXISTS parcel_did (
    parcel_uid      text NOT NULL REFERENCES parcel(parcel_uid) ON UPDATE CASCADE,
    event           text NOT NULL CHECK (event IN ('t_3_1', 't_award', 't_possession')),
    metric          text NOT NULL,              -- vigour_z | plough_rate | ...
    season_scope    text NOT NULL,              -- all | rabi
    event_date      date,
    n_pre           integer,
    n_post          integer,
    pre_diff        real,                       -- mean (parcel - control) before the event
    post_diff       real,
    did             real,                       -- post_diff - pre_diff
    ci_lo           real,                       -- bootstrap 95 % CI of did
    ci_hi           real,
    farmland_like_p real,                       -- bootstrap P(post level is control-farmland-like)
    meta            jsonb NOT NULL DEFAULT '{}',
    did_version     text,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (parcel_uid, event, metric, season_scope)
);

GRANT SELECT ON control_cell, control_obs, control_season, parcel_did TO agent_ro;
