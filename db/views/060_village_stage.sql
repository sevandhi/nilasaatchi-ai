-- 060 — T5.2 aggregates (owner: gis-engineer). Areas are geodesic (D-023): ha_sum from
-- parcel.area_ha_gis, ha_union from the dissolved union (FMB parcels overlap, 7.3 ha double-counted).
CREATE OR REPLACE VIEW v_village_stage_matrix WITH (security_invoker = true) AS
SELECT l.village, COALESCE(l.current_stage, 'NO_EVIDENCE') AS stage,
       COALESCE(l.current_stage_ord, -1) AS stage_ord,
       count(*) AS parcels,
       round(sum(l.area_ha_gis), 4) AS ha_sum,
       round(geo_area_ha(ST_Union(p.geom))::numeric, 4) AS ha_union,
       count(*) FILTER (WHERE l.stalled_flag IS NOT NULL) AS stalled,
       count(*) FILTER (WHERE l.stalled_flag IS NOT NULL AND l.current_stage_evidence = 'block_level') AS stalled_block_evidence,
       count(*) FILTER (WHERE l.current_stage_evidence = 'block_level') AS stage_from_block_evidence
FROM v_parcel_lifecycle l JOIN parcel p USING (parcel_uid)
GROUP BY 1, 2, 3;

-- Funnel: parcels with evidence of each stage (not only the current one), 3(1) -> mutation.
CREATE OR REPLACE VIEW v_lifecycle_funnel WITH (security_invoker = true) AS
SELECT l.village, s.stage, stage_order(s.stage) AS stage_ord,
       count(*) FILTER (WHERE s.stage = ANY (l.stages)) AS parcels_with_evidence,
       round(sum(l.area_ha_gis) FILTER (WHERE s.stage = ANY (l.stages)), 4) AS ha_sum,
       count(*) AS parcels_total
FROM v_parcel_lifecycle l
CROSS JOIN unnest(ARRAY['SEC_3_1','SEC_3_2','POSSESSION_NOTICE','AWARD','PAYMENT','POSSESSION','MUTATION']) AS s(stage)
GROUP BY 1, 2, 3;

GRANT SELECT ON v_village_stage_matrix, v_lifecycle_funnel TO agent_ro;
