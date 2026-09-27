-- v_discrepancy_open / v_findings_open — PLACEHOLDERS (typed, always empty) until the
-- discrepancy/finding tables arrive in 0004+. Column contract follows plan §5.4/§5.10.
CREATE OR REPLACE VIEW v_discrepancy_open WITH (security_invoker = true) AS
SELECT NULL::bigint  AS id,
       NULL::text    AS parcel_uid,
       NULL::text    AS category,
       NULL::text    AS severity,
       NULL::jsonb   AS evidence,
       NULL::real    AS confidence,
       NULL::timestamptz AS created_at
WHERE false;

CREATE OR REPLACE VIEW v_findings_open WITH (security_invoker = true) AS
SELECT NULL::bigint  AS id,
       NULL::text    AS parcel_uid,
       NULL::text    AS category,
       NULL::text    AS verdict,
       NULL::real    AS confidence,
       NULL::text[]  AS caveats,
       NULL::bigint  AS run_id
WHERE false;
