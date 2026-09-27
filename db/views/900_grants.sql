-- Allow-listed read access for the agent SQL tool (applied after every view file).
-- Plain views use security_invoker so agent_ro can never read through a view what it cannot read directly.
GRANT SELECT ON v_parcel_status, v_village_progress, v_discrepancy_open, v_findings_open,
                fmb_qa, fmb_qa_overlap, fmb_qa_outside_survey TO agent_ro;
GRANT EXECUTE ON FUNCTION geo_area_ha(geometry) TO agent_ro;
