-- 0004_s2_scene_meta.sql — first-class AOI/scene metadata on s2_scene (owner: gis-engineer).
-- The eo-engineer was keeping AOI stats in hrefs._meta. hrefs must hold only asset hrefs
-- (they are the evidence), so the stats get their own columns here.
ALTER TABLE s2_scene
    ADD COLUMN IF NOT EXISTS aoi_valid_frac real CHECK (aoi_valid_frac IS NULL OR aoi_valid_frac BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS aoi_cloud_frac real CHECK (aoi_cloud_frac IS NULL OR aoi_cloud_frac BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}';   -- any other scene/AOI stats (SCL histogram, etc.)

-- Move whatever is already in hrefs._meta into the new columns, then remove it from hrefs.
UPDATE s2_scene
SET meta = meta || (hrefs -> '_meta'),
    aoi_valid_frac = coalesce(aoi_valid_frac,
                              CASE WHEN jsonb_typeof(hrefs -> '_meta' -> 'aoi_valid_frac') = 'number'
                                   THEN (hrefs -> '_meta' ->> 'aoi_valid_frac')::real END),
    hrefs = hrefs - '_meta'
WHERE hrefs ? '_meta' AND jsonb_typeof(hrefs -> '_meta') = 'object';

CREATE INDEX IF NOT EXISTS s2_scene_aoi_valid_idx ON s2_scene (aoi_valid_frac);
-- agent_ro already has SELECT on s2_scene (0003). Column-level grants are not needed.
