-- v_village_progress — per-village GIS totals (geodesic ha, 4 dp; D-023). fmb_area_ha is the sum of
-- parcel areas; fmb_union_ha dissolves the overlapping FMB polygons and is the primary total. Stage progress
-- columns are placeholders until acquisition events exist (P2/P5).
CREATE OR REPLACE VIEW v_village_progress WITH (security_invoker = true) AS
SELECT v.id                          AS village_id,
       v.name                        AS village,
       count(p.*)                    AS n_parcels,
       round(coalesce(sum(p.area_ha_gis), 0), 4) AS fmb_area_ha,
       (SELECT round(coalesce(sum(s.area_ha_gis), 0), 4) FROM survey s WHERE s.village_id = v.id)
                                     AS cadastral_area_ha,
       (SELECT count(*) FROM survey s WHERE s.village_id = v.id) AS n_surveys,
       NULL::numeric                 AS possessed_area_ha,   -- placeholder (P5)
       NULL::numeric                 AS mutated_area_ha,     -- placeholder (P5)
       round(geo_area_ha(ST_Union(p.geom))::numeric, 4) AS fmb_union_ha,   -- primary total (D-023)
       (SELECT round(geo_area_ha(ST_Union(s.geom))::numeric, 4) FROM survey s WHERE s.village_id = v.id)
                                     AS cadastral_union_ha
FROM village v
LEFT JOIN parcel p ON p.village_id = v.id
GROUP BY v.id, v.name;
