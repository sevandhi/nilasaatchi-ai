-- 0003_spatial_kg.sql — spatial knowledge graph (owner: gis-engineer, D-022)
--
-- Parcel identity follows D-020: parcel_uid = '<canonical village>|<KIDE>' because the FMB
-- KIDE repeats across villages. `kide` is a plain attribute only.
-- Storage CRS is EPSG:4326 (the source layers are CRS84). Every area and distance is computed
-- in EPSG:32644 (UTM 44N); `geom_utm` is a stored generated column so KNN/area work is indexed.
-- Polygonal geom_utm is re-validated after projection: FMB Keelathattaparai|433/2 is valid in
-- 4326 but self-intersects once projected to UTM (sub-millimetre vertex collapse).
-- This migration must not reference the document/page tables (owned by 0002).
-- Views live in db/views/*.sql and are (re)applied by `python -m pipeline.gis.views`
-- (called at the end of `make load-gis`).

-- ---------------------------------------------------------------- core KG
CREATE TABLE IF NOT EXISTS village (
    id        smallserial PRIMARY KEY,
    name      text NOT NULL UNIQUE,              -- canonical English name (config/aliases.yaml)
    name_ta   text,
    aliases   text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS parcel (
    parcel_uid              text PRIMARY KEY,     -- '<canonical village>|<KIDE>' (D-020)
    kide                    text NOT NULL,
    village_id              smallint NOT NULL REFERENCES village(id),
    survey_no               text NOT NULL,
    sub_div                 text,                 -- normalised from KIDE (upper-case, no spaces)
    unit_id                 integer,
    block_id                integer,
    land_id                 text,                 -- FMB Land_id; not always numeric (A1, J5 ...)
    raw                     jsonb NOT NULL DEFAULT '{}',  -- source properties, verbatim
    geom                    geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm                geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED,
    area_ha_gis             numeric(12, 4),
    dist_major_road_m       numeric(10, 1),
    dist_any_road_m         numeric(10, 1),
    dist_substation_m       numeric(10, 1),
    dist_rail_station_m     numeric(10, 1),
    intersects_waterbody    boolean,
    waterbody_overlap_ha    numeric(12, 4),
    inside_park_boundary    boolean,
    area_outside_boundary_ha numeric(12, 4),
    raster_stats            jsonb NOT NULL DEFAULT '{}',
    loaded_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (village_id, kide)
);
CREATE INDEX IF NOT EXISTS parcel_geom_gix      ON parcel USING gist (geom);
CREATE INDEX IF NOT EXISTS parcel_geom_utm_gix  ON parcel USING gist (geom_utm);
CREATE INDEX IF NOT EXISTS parcel_kide_idx      ON parcel (kide);
CREATE INDEX IF NOT EXISTS parcel_village_survey_idx ON parcel (village_id, survey_no);
CREATE INDEX IF NOT EXISTS parcel_unit_block_idx ON parcel (unit_id, block_id);

CREATE TABLE IF NOT EXISTS survey (
    village_id   smallint NOT NULL REFERENCES village(id),
    survey_no    text NOT NULL,
    unit_id      integer,
    block_id     integer,
    raw          jsonb NOT NULL DEFAULT '{}',
    geom         geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm     geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED,
    area_ha_gis  numeric(12, 4),
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (village_id, survey_no)
);
CREATE INDEX IF NOT EXISTS survey_geom_gix ON survey USING gist (geom);
CREATE INDEX IF NOT EXISTS survey_geom_utm_gix ON survey USING gist (geom_utm);

-- Provenance of every loaded layer (sha256 of the source file, counts, repairs).
CREATE TABLE IF NOT EXISTS gis_layer_load (
    layer          text PRIMARY KEY,
    target_table   text NOT NULL,
    source_path    text NOT NULL,
    sha256         text NOT NULL,
    n_features     integer NOT NULL,
    n_z_stripped   integer NOT NULL DEFAULT 0,
    n_invalid_fixed integer NOT NULL DEFAULT 0,
    notes          jsonb NOT NULL DEFAULT '{}',
    loaded_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- reference layers
-- Common shape: id, name, typed columns where useful, raw jsonb (all source attributes), geom.
CREATE TABLE IF NOT EXISTS ref_layer_roads (
    id        serial PRIMARY KEY,
    osm_id    text,
    fclass    text,
    name      text,
    ref       text,
    is_major  boolean NOT NULL DEFAULT false,   -- trunk/primary/secondary(+_link, motorway) or ref NH/SH/MDR*
    is_vehicular boolean NOT NULL DEFAULT true, -- false for footway/path/steps/pedestrian
    raw       jsonb NOT NULL DEFAULT '{}',
    geom      geometry(MultiLineString, 4326) NOT NULL,
    geom_utm  geometry(MultiLineString, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_rail (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(MultiLineString, 4326) NOT NULL,
    geom_utm geometry(MultiLineString, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_rail_stations (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(Point, 4326) NOT NULL,
    geom_utm geometry(Point, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_waterbodies (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',   -- name = Tank_name
    geom geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_substations (
    id serial PRIMARY KEY, name text, voltage text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_airport (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_seaport (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(Point, 4326) NOT NULL,
    geom_utm geometry(Point, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_schools (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(Point, 4326) NOT NULL,
    geom_utm geometry(Point, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_sipcot_parks (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(Point, 4326) NOT NULL,
    geom_utm geometry(Point, 32644) GENERATED ALWAYS AS (ST_Transform(geom, 32644)) STORED
);
CREATE TABLE IF NOT EXISTS ref_layer_park_boundary (
    id serial PRIMARY KEY, name text, raw jsonb NOT NULL DEFAULT '{}',
    geom geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED,
    area_ha_gis numeric(12, 4)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['roads','rail','rail_stations','waterbodies','substations','airport',
                             'seaport','schools','sipcot_parks','park_boundary'] LOOP
        EXECUTE format('CREATE INDEX IF NOT EXISTS ref_layer_%s_gix ON ref_layer_%s USING gist (geom)', t, t);
        EXECUTE format('CREATE INDEX IF NOT EXISTS ref_layer_%s_utm_gix ON ref_layer_%s USING gist (geom_utm)', t, t);
    END LOOP;
END $$;
CREATE INDEX IF NOT EXISTS ref_layer_roads_major_utm_gix ON ref_layer_roads USING gist (geom_utm) WHERE is_major;

-- ---------------------------------------------------------------- Sentinel-2 (plan §5.10)
CREATE TABLE IF NOT EXISTS s2_scene (
    id              text PRIMARY KEY,           -- STAC item id (highest baseline kept per date, D-015)
    datetime        timestamptz NOT NULL,
    tile            text,
    cloud           real,
    baseline        text,
    hrefs           jsonb NOT NULL DEFAULT '{}',
    aoi_cache_path  text,
    source          text,                       -- earth-search | planetary-computer
    dn_offset       integer NOT NULL DEFAULT 0,
    epsg            integer,
    duplicates      text[] NOT NULL DEFAULT '{}' -- collapsed baseline ids
);
CREATE INDEX IF NOT EXISTS s2_scene_datetime_idx ON s2_scene (datetime);

CREATE TABLE IF NOT EXISTS upload_polygon (          -- agri-claims pack uploads
    id            serial PRIMARY KEY,
    workspace_id  text,                              -- no FK yet: workspace table arrives later
    name          text,
    raw           jsonb NOT NULL DEFAULT '{}',
    geom          geometry(MultiPolygon, 4326) NOT NULL,
    geom_utm      geometry(MultiPolygon, 32644) GENERATED ALWAYS AS
        (ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(geom, 32644)), 3))) STORED,
    area_ha       numeric(12, 4),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS upload_polygon_geom_gix ON upload_polygon USING gist (geom);

CREATE TABLE IF NOT EXISTS parcel_obs (
    id           bigserial PRIMARY KEY,
    parcel_uid   text REFERENCES parcel(parcel_uid) ON UPDATE CASCADE,
    polygon_id   integer REFERENCES upload_polygon(id) ON DELETE CASCADE,
    scene_id     text NOT NULL REFERENCES s2_scene(id),
    date         date NOT NULL,
    ndvi         real,
    ndwi         real,
    bsi          real,
    ndbi         real,
    valid_frac   real,
    n_px         integer,
    low_support  boolean NOT NULL DEFAULT false,
    CHECK ((parcel_uid IS NULL) <> (polygon_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS parcel_obs_parcel_scene_uq ON parcel_obs (parcel_uid, scene_id) WHERE parcel_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS parcel_obs_poly_scene_uq ON parcel_obs (polygon_id, scene_id) WHERE polygon_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS parcel_obs_parcel_date_idx ON parcel_obs (parcel_uid, date);
CREATE INDEX IF NOT EXISTS parcel_obs_scene_idx ON parcel_obs (scene_id);

CREATE TABLE IF NOT EXISTS parcel_season (
    id             bigserial PRIMARY KEY,
    parcel_uid     text REFERENCES parcel(parcel_uid) ON UPDATE CASCADE,
    polygon_id     integer REFERENCES upload_polygon(id) ON DELETE CASCADE,
    ag_year        smallint NOT NULL,        -- start year of the Jun–May agricultural year (2023 = Jun 2023–May 2024)
    season         text NOT NULL CHECK (season IN ('kharif', 'rabi', 'summer', 'annual')),
    features       jsonb NOT NULL DEFAULT '{}',
    state          text CHECK (state IN ('cropped', 'irrigated_multi', 'perennial_veg', 'bare_fallow',
                                         'cleared_or_built', 'water', 'unknown')),
    p_state        real,
    teacher_label  text,
    teacher_src    text,
    route          text,                     -- student | vlm_second_opinion | disagreement
    needs_field    boolean NOT NULL DEFAULT false,
    CHECK ((parcel_uid IS NULL) <> (polygon_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS parcel_season_parcel_uq ON parcel_season (parcel_uid, ag_year, season) WHERE parcel_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS parcel_season_poly_uq ON parcel_season (polygon_id, ag_year, season) WHERE polygon_id IS NOT NULL;

-- ---------------------------------------------------------------- read-only agent role
-- NOLOGIN: the SQL tool connects as the app user and runs each guarded query inside
-- `BEGIN READ ONLY; SET LOCAL ROLE agent_ro; ...`. No password exists or is needed.
-- Grants are an explicit allow-list (no ALTER DEFAULT PRIVILEGES), so tables added later
-- (e.g. document/page with personal data) are NOT readable unless granted deliberately.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;
GRANT agent_ro TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON village, parcel, survey, gis_layer_load,
    ref_layer_roads, ref_layer_rail, ref_layer_rail_stations, ref_layer_waterbodies,
    ref_layer_substations, ref_layer_airport, ref_layer_seaport, ref_layer_schools,
    ref_layer_sipcot_parks, ref_layer_park_boundary,
    s2_scene, parcel_obs, parcel_season, upload_polygon,
    spatial_ref_sys
TO agent_ro;
