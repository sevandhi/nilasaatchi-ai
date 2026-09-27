-- 050 — T5.2 parcel lifecycle (owner: gis-engineer). Stage order: land-domain-knowledge §2.
-- Events reach a parcel through acquisition_event_link (migration 0013; filled by `python -m pipeline.match`):
--   'parcel' (single match) | 'list_member' (survey lists) | 'survey_level' (survey only, linked to every
--   FMB subdivision under it) | 'block_level' (document-level event without survey refs, linked to every
--   parcel in its village+unit+block, D-052).
-- Stall thresholds come from the `threshold` table (synced from config/thresholds.yaml by the matcher).

-- column lists change between versions: drop our own dependants first (060 recreates its views)
DROP VIEW IF EXISTS v_lifecycle_funnel, v_village_stage_matrix, v_parcel_lifecycle, v_block_stage_evidence;

CREATE OR REPLACE FUNCTION stage_order(s text) RETURNS int
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE s WHEN 'GO_AS_LPS' THEN 0 WHEN 'SEC_3_1' THEN 1 WHEN 'SEC_3_2' THEN 2 WHEN 'EXEMPTION' THEN 3
                WHEN 'PRICE_NEGOTIATION' THEN 4 WHEN 'POSSESSION_NOTICE' THEN 5 WHEN 'AWARD' THEN 6
                WHEN 'PAYMENT' THEN 7 WHEN 'POSSESSION' THEN 8 WHEN 'MUTATION' THEN 9 END
$$;

CREATE OR REPLACE FUNCTION thr(k text, dflt numeric) RETURNS numeric
LANGUAGE sql STABLE AS $$ SELECT COALESCE((SELECT value FROM threshold WHERE key = k), dflt) $$;

CREATE OR REPLACE VIEW v_parcel_events WITH (security_invoker = true) AS
SELECT l.parcel_uid, a.id AS event_id, a.stage, stage_order(a.stage) AS stage_ord, a.sub_stage,
       a.event_date, a.date_precision, a.document_id, a.extraction_id, a.amount,
       l.link_kind, l.score AS match_score, a.survey_ref
FROM acquisition_event_link l
JOIN acquisition_event a ON a.id = l.event_id;

CREATE OR REPLACE VIEW v_parcel_lifecycle WITH (security_invoker = true) AS
WITH ev AS (
  SELECT parcel_uid,
         max(stage_ord) FILTER (WHERE stage <> 'EXEMPTION') AS cur_ord,
         max(stage_ord) FILTER (WHERE stage <> 'EXEMPTION' AND link_kind <> 'block_level') AS cur_ord_parcel,
         bool_or(stage = 'EXEMPTION' AND link_kind <> 'block_level') AS has_exemption,
         bool_or(link_kind IN ('parcel','list_member')) AS has_parcel_level,
         bool_or(link_kind = 'survey_level') AS has_survey_level,
         count(*) AS n_events,
         count(*) FILTER (WHERE link_kind = 'block_level') AS n_block_events,
         array_agg(DISTINCT stage) AS stages,
         array_agg(DISTINCT stage) FILTER (WHERE link_kind <> 'block_level') AS parcel_stages,
         array_agg(DISTINCT stage) FILTER (WHERE link_kind = 'block_level') AS block_stages,
         jsonb_agg(jsonb_build_object('stage', stage, 'date', event_date, 'precision', date_precision,
                   'document_id', document_id, 'event_id', event_id, 'link', link_kind)
                   ORDER BY event_date NULLS LAST, stage_ord) AS events
  FROM v_parcel_events GROUP BY parcel_uid
), cur AS (
  SELECT ev.*, s.stage AS current_stage, s.entered AS stage_entered, s.parcel_ev AS current_parcel_level
  FROM ev
  LEFT JOIN LATERAL (SELECT min(e.stage) AS stage, min(e.event_date) AS entered,
                            bool_or(e.link_kind <> 'block_level') AS parcel_ev
                     FROM v_parcel_events e
                     WHERE e.parcel_uid = ev.parcel_uid AND e.stage_ord = ev.cur_ord) s ON true
)
SELECT p.parcel_uid, v.name AS village, p.kide, p.unit_id, p.block_id, p.area_ha_gis,
       COALESCE(c.n_events, 0) AS n_events, COALESCE(c.n_block_events, 0) AS n_block_events,
       c.current_stage, c.cur_ord AS current_stage_ord, c.stage_entered,
       (current_date - c.stage_entered) AS days_in_stage,
       -- evidence behind the current stage: 'parcel' (parcel/list/survey-level link) or 'block_level' only
       CASE WHEN c.cur_ord IS NULL THEN NULL WHEN c.current_parcel_level THEN 'parcel' ELSE 'block_level' END
           AS current_stage_evidence,
       CASE WHEN c.cur_ord_parcel IS NULL THEN NULL ELSE
            (ARRAY['GO_AS_LPS','SEC_3_1','SEC_3_2','EXEMPTION','PRICE_NEGOTIATION','POSSESSION_NOTICE',
                   'AWARD','PAYMENT','POSSESSION','MUTATION'])[c.cur_ord_parcel + 1] END AS current_stage_parcel_only,
       CASE c.cur_ord WHEN 9 THEN NULL WHEN 8 THEN 'MUTATION' WHEN 7 THEN 'POSSESSION' WHEN 6 THEN 'PAYMENT'
            ELSE CASE WHEN c.cur_ord IS NULL THEN 'SEC_3_1' ELSE 'AWARD' END END AS next_expected_stage,
       ARRAY(SELECT m FROM unnest(ARRAY['SEC_3_1','SEC_3_2','AWARD','POSSESSION']) m
             WHERE stage_order(m) < c.cur_ord AND NOT (m = ANY (c.stages))) AS missing_mandatory,
       COALESCE(c.has_exemption, false) AS has_exemption,
       COALESCE(c.has_exemption AND c.cur_ord >= 6, false) AS exemption_conflict,
       CASE WHEN c.n_events IS NULL THEN 'none' WHEN c.has_parcel_level THEN 'parcel'
            WHEN c.has_survey_level THEN 'survey_level' ELSE 'block_level' END AS evidence_level,
       CASE WHEN c.cur_ord = 6 AND current_date - c.stage_entered > thr('lifecycle.stall_days.award_to_payment', 60)
                 THEN 'AWARD_NOT_PAID'
            WHEN c.cur_ord = 7 AND current_date - c.stage_entered > thr('lifecycle.stall_days.payment_to_possession', 30)
                 THEN 'PAID_NO_POSSESSION'
            WHEN c.cur_ord = 8 AND current_date - c.stage_entered > thr('lifecycle.stall_days.possession_to_mutation', 90)
                 THEN 'POSSESSION_NO_MUTATION'
       END AS stalled_flag,
       COALESCE(c.parcel_stages, '{}') AS parcel_level_stages,
       COALESCE(c.block_stages, '{}') AS block_level_stages,
       c.stages, c.events
FROM parcel p
JOIN village v ON v.id = p.village_id
LEFT JOIN cur c ON c.parcel_uid = p.parcel_uid;

GRANT SELECT ON v_parcel_events, v_parcel_lifecycle, threshold TO agent_ro;
GRANT EXECUTE ON FUNCTION stage_order(text), thr(text, numeric) TO agent_ro;
