-- D-031: allow the 'insufficient_data' land-use state (planet/phase3 skill); plus gap flag for peak-dependent states.
DO $$
DECLARE c text;
BEGIN
  SELECT conname INTO c FROM pg_constraint
   WHERE conrelid = 'parcel_season'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%cropped%';
  IF c IS NOT NULL THEN EXECUTE format('ALTER TABLE parcel_season DROP CONSTRAINT %I', c); END IF;
END $$;
ALTER TABLE parcel_season ADD CONSTRAINT parcel_season_state_check CHECK (state IS NULL OR state = ANY (ARRAY[
  'cropped','irrigated_multi','perennial_veg','bare_fallow','cleared_or_built','water','unknown','insufficient_data']));
