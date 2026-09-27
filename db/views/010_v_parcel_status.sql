-- v_parcel_status — one row per FMB parcel. PLACEHOLDER until P2/P5: the lifecycle columns
-- (stage, last_event_date, ...) are filled from acquisition_event once 0004+ exists.
CREATE OR REPLACE VIEW v_parcel_status WITH (security_invoker = true) AS
SELECT p.parcel_uid,
       v.name              AS village,
       p.kide,
       p.survey_no,
       p.sub_div,
       p.unit_id,
       p.block_id,
       p.area_ha_gis,                      -- geodesic (D-023)
       p.inside_park_boundary,
       p.intersects_waterbody,
       NULL::text          AS stage,            -- lifecycle stage code (land-domain-knowledge §2)
       NULL::date          AS last_event_date,
       'NO_DOC_EVIDENCE_YET'::text AS status_note,
       round((ST_Area(p.geom_utm) / 1e4)::numeric, 4) AS area_ha_utm   -- planar EPSG:32644, reference only (D-023)
FROM parcel p
JOIN village v ON v.id = p.village_id;
