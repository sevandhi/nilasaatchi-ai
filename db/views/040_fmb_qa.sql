-- FMB quality findings input (D-023; feeds the P5 FMB-quality findings).
-- Materialised so the pairwise overlay runs once per `make load-gis`; rebuilt on every load.
-- Overlays in EPSG:32644 (geom_utm); areas geodesic via geo_area_ha(). Thresholds: overlap
-- > 1 m² (0.0001 ha), outside-survey > 100 m² (0.01 ha).
DROP VIEW IF EXISTS fmb_qa;
DROP MATERIALIZED VIEW IF EXISTS fmb_qa_overlap;
DROP MATERIALIZED VIEW IF EXISTS fmb_qa_outside_survey;

CREATE MATERIALIZED VIEW fmb_qa_overlap AS
SELECT t.uid_a, t.uid_b, round(t.overlap_ha::numeric, 4) AS overlap_ha, t.cross_village,
       t.village_a, t.village_b
FROM (
    SELECT p.parcel_uid AS uid_a, q.parcel_uid AS uid_b,
           geo_area_ha(ST_Intersection(p.geom_utm, q.geom_utm)) AS overlap_ha,
           p.village_id <> q.village_id AS cross_village,
           va.name AS village_a, vb.name AS village_b
    FROM parcel p
    JOIN parcel q ON p.parcel_uid < q.parcel_uid AND ST_Intersects(p.geom_utm, q.geom_utm)
    JOIN village va ON va.id = p.village_id
    JOIN village vb ON vb.id = q.village_id
) t
WHERE t.overlap_ha > 0.0001;
CREATE INDEX fmb_qa_overlap_a_idx ON fmb_qa_overlap (uid_a);
CREATE INDEX fmb_qa_overlap_b_idx ON fmb_qa_overlap (uid_b);

CREATE MATERIALIZED VIEW fmb_qa_outside_survey AS
SELECT t.parcel_uid AS uid, t.village, t.survey_no, round(t.outside_ha::numeric, 4) AS outside_ha,
       round((100 * t.outside_ha / nullif(t.area_ha_gis, 0))::numeric, 2) AS outside_pct
FROM (
    SELECT p.parcel_uid, v.name AS village, p.survey_no, p.area_ha_gis,
           geo_area_ha(ST_Difference(p.geom_utm, s.geom_utm)) AS outside_ha
    FROM parcel p
    JOIN survey s USING (village_id, survey_no)
    JOIN village v ON v.id = p.village_id
) t
WHERE t.outside_ha > 0.01;
CREATE INDEX fmb_qa_outside_survey_uid_idx ON fmb_qa_outside_survey (uid);

-- One long-format list for the findings engine.
CREATE VIEW fmb_qa WITH (security_invoker = true) AS
SELECT 'OVERLAP'::text AS issue, uid_a AS parcel_uid, uid_b AS other_parcel_uid, overlap_ha AS area_ha,
       cross_village
FROM fmb_qa_overlap
UNION ALL
SELECT 'OUTSIDE_SURVEY', uid, NULL, outside_ha, NULL
FROM fmb_qa_outside_survey;
