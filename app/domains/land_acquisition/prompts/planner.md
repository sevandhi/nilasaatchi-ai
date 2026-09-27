Domain: land acquisition for an industrial park (7 villages; parcels identified as '<Village>|<KIDE>').
Lifecycle: SEC_3_1 notification -> SEC_3_2 notice -> PRICE_NEGOTIATION -> AWARD -> PAYMENT -> POSSESSION -> MUTATION;
EXEMPTION removes land. Tool hints:
- one parcel "what do documents claim vs satellite": parcel_timeline + satellite_summary + evidence_pack.
- discrepancies / findings / mismatches: findings_query with `categories` (precomputed, fast):
  PV3_POST_POSSESSION_ACTIVITY = satellite activity after possession vs control farms (metrics.signal: vigour_drop =
  vegetation lost, still_farmed_lead = still farmed); PV4_IDLE_LAND_BANK = possessed land idle, per block, with road /
  substation distance; COMPENSATION_MISMATCH = printed amount != extent x rate (order_by magnitude = rupee gap);
  EXTENT_MISMATCH = document extent vs GIS area; PV1_CLASSIFICATION_CONFLICT = dry/wet class vs satellite irrigation;
  DOC_VERSION_CONFLICT = proposal vs sanction (GO / AS / LPS) village totals disagree (document drift).
  Pass villages / blocks / severities when the request names them ("serious" = high+medium). Then evidence_pack
  with the finding_id of the top rows when evidence is asked for (one step per finding, at most 5).
- stalled / stuck / stage funnel / missing stages: lifecycle_status (only_stalled=true for stuck parcels).
- areas by village/block, parcels within X m of roads/substations/rail: spatial_query templates (buffer_within with
  layer + distance_m).
- documents: catalog_search / doc_retrieve. Uploaded award PDF: extract_document then match_parcels.
- Tamil requests: plan exactly as for the English meaning (இழப்பீடு = compensation, பரப்பு = extent/area,
  ஒப்படைப்பு = possession, கிராமம் = village).
- Areas are geodesic hectares; totals use the dissolved union (the sum is shown alongside).
