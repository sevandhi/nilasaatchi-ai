-- 0006_planet.sql — P3 extraction provenance on parcel_obs (owner: eo-engineer).
-- D-026: structurally low-support parcels (< 10 px inside the -5 m buffer) are measured on the
-- unbuffered outline and flagged mixed_pixel (their confidence is capped downstream).
-- geom_mode records which footprint produced the observation: 'buf5' (default -5 m inward buffer),
-- 'unbuffered' (centre-in on the original outline) or 'all_touched' (tiny parcels with no pixel centre).
-- n_total = pixels in the footprint (valid or not); extract_version ties rows to the code that made them.
ALTER TABLE parcel_obs
    ADD COLUMN IF NOT EXISTS mixed_pixel      boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS geom_mode        text CHECK (geom_mode IS NULL OR geom_mode IN ('buf5', 'unbuffered', 'all_touched')),
    ADD COLUMN IF NOT EXISTS n_total          integer,
    ADD COLUMN IF NOT EXISTS extract_version  text;

-- Feature provenance on parcel_season (state columns stay NULL until T3.4).
ALTER TABLE parcel_season
    ADD COLUMN IF NOT EXISTS features_version text,
    ADD COLUMN IF NOT EXISTS features_at      timestamptz;
