-- 070 — findings views (owner: gis-engineer). Replaces the typed placeholders of 030 (same columns)
-- now that the `finding` table exists (migration 0014, filled by `python -m pipeline.findings`).
CREATE OR REPLACE VIEW v_discrepancy_open WITH (security_invoker = true) AS
SELECT f.id, f.parcel_uid, f.category, f.severity,
       jsonb_build_object('title', f.title, 'metrics', f.metrics, 'paper', f.paper_evidence,
                          'planet', f.planet_evidence, 'evidence_level', f.evidence_level) AS evidence,
       f.confidence, f.created_at
FROM finding f WHERE f.status = 'open';

CREATE OR REPLACE VIEW v_findings_open WITH (security_invoker = true) AS
SELECT f.id, f.parcel_uid, f.category, f.verdict, f.confidence, f.caveats, NULL::bigint AS run_id
FROM finding f WHERE f.status = 'open';

CREATE OR REPLACE VIEW v_finding_summary WITH (security_invoker = true) AS
SELECT category, severity, evidence_level, count(*) AS n, round(avg(confidence)::numeric, 3) AS avg_conf
FROM finding WHERE status = 'open' GROUP BY 1, 2, 3;

-- PV4 idle land bank by block (hectares = geodesic sum, D-023; distances in km)
CREATE OR REPLACE VIEW v_idle_land_bank WITH (security_invoker = true) AS
SELECT f.id AS finding_id, f.village, f.unit_id, f.block_id, (f.metrics->>'idle_ha_sum')::numeric AS idle_ha,
       (f.metrics->>'n_parcels')::int AS n_parcels, (f.metrics->>'oldest_possession')::date AS oldest_possession,
       (f.metrics->>'min_major_road_km')::numeric AS min_major_road_km,
       (f.metrics->>'min_substation_km')::numeric AS min_substation_km, f.evidence_level, f.confidence
FROM finding f WHERE f.category = 'PV4_IDLE_LAND_BANK' AND f.status = 'open';

GRANT SELECT ON finding, v_discrepancy_open, v_findings_open, v_finding_summary, v_idle_land_bank TO agent_ro;
