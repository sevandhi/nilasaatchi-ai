---
name: phase2-document-intelligence
description: Phase 2 (Day 2–7) — privacy-tiered Tamil/English OCR and table extraction over ~12.5k unique pages: text-layer first, PaddleOCR+Tesseract voting, table grid and typed cell OCR, per-doc-type JSON schemas, self-consistency arithmetic, escalation to Mistral OCR / Groq vision / review queue within daily quotas, normalisers, evidence bboxes, KG loading and golden-set evaluation. Load for any OCR, extraction, normalisation or extraction-prompt work.
---

# Phase 2 — Document Intelligence

Also load `land-domain-knowledge` and `free-model-router`.

**Owners:**
- doc-intel-engineer (lead)
- data-engineer: batch runner, quota scheduler
- qa-evaluator: golden set of 150+ rows

## Pipeline per page (D-010, from docs/spikes/ocr-bakeoff.md; supersedes the earlier cell-OCR design)
```
text_ok? ──yes──► text-layer lines (pdftotext -bbox-layout keeps coordinates)
   │no
   ▼
Tesseract tam+eng (hOCR, psm 4, local, ~5 s/page) → header fields (regex + dictionary) + full text for search
   ▼
page has a table? (grid detector or VLM says so) ──no──► prose-only extraction (headers, dates, refs)
   │yes
   ▼
Bedrock Ministral 3 8B (router task `table_read_pii`, privacy PII unless GO/AS/LPS): WHOLE PAGE, JPEG long side
1800 px, max_tokens 4096, schema {header, tables[{columns, rows[[cells]]}]} with raw cell strings
   ▼
per-doc-type column mapping (header keywords → survey | subdiv | classification | extent_ha | extent_ac | cents |
patta | owner | amount | serial) → normalisers → schema validation → self-consistency
   ▼
self_consistency=fail, VLM header vote disagrees with Tesseract, or low confidence
   ▼
Groq Qwen vision (same prompt) as second read → still conflicting → review queue (needs_human)
Handwritten Form E and blurred newsprint 3(2) pages go straight to the review queue after one VLM attempt.
```
- Evidence bbox: the grid detector (`spikes/ocr_bakeoff/grid.py`, promote it) gives table/row boxes; map each VLM row to its row box by order, and record `bbox_method: grid_row | page` when no grid is found.
- Baseline to beat (bake-off (g), 20 golden pages, schema-agnostic recall): numbers 63%, surveys 83%, owners 59%. Promote the bake-off code (`fullpage_vlm.py`, normalize.py, parse helpers) into `pipeline/extract/` rather than rewriting it.
- Budget: ~$0.0005/page; run `make aws-cost` before and after each batch, and stop at 80% of the $15 cap.

## Scope (quota-driven; plan v2)
- **All documents:** header fields (type, village, unit, block, doc no, date, survey refs). These produce the `acquisition_event` rows.
- **Full tables only for the priority types:** GO/AS/LPS, AWARD_7_2/7_3, FORM_F, LDR/POSSESSION_CERT, CHITTA (digital).
- **Claims the Proof pillar needs (mandatory fields):**
  - `classification` per row (புன்செய்/நன்செய்/புறம்போக்கு, dry/wet/poramboke; normalised to DRY|WET|PORAMBOKE|OTHER)
  - `standing_assets` lines: trees (மரம்/மரங்கள், with count and species if printed), wells (கிணறு), structures (கட்டிடம்), with their amounts, and whether the rate is stated as "excluding trees and buildings" (மரங்கள் மற்றும் கட்டிடங்கள் நீங்கலாக)
  - `handover_date` from LDR/possession documents
  - chitta `remarks` such as well markers (கிணறு, சதுரக்கிணறு)
- Targets for these fields: classification ≥ 90% exact, handover date ≥ 95%.

## Schemas (`schemas/extraction/*.json`)
- Every row carries `{doc_id, page, bbox:[x0,y0,x1,y1], row_index, source_engine, model_id, privacy_tier, confidence, raw_cells[]}`.
- Doc-level fields: `village, taluk, unit_no, block_no, doc_no, doc_date, gazette_no, gazette_date, rate_per_acre`.
- `AwardRow`: `serial, survey_no, sub_div, extent_ha, extent_ac, patta_no, owners[{name, relation, relation_name}], classification, amount_rs?`
- `FormFApportionment`: `owners[], amount_rs, amount_words, award_ref, award_date, sec31_gazette_no, sec31_date`. Cross-check `amount_words` against `amount_rs` using a Tamil/English number-words parser.
- `LandDeliveryRow`: `serial, survey_no, sub_div, extent_ha, extent_ac, boundaries{N,E,S,W}, classification, handover_date`.
- `ChittaRow` (digital text): `patta_no, owner(s), survey_no, sub_div, extent_ha (H - AA.SS), assessment, remarks, signed_by, signed_date`.
- `GOVillageTotals` / `ASVillageTotals` / `LPSVillageTotals`: `village, wet, dry, poramboke, total_ha, total_ac`.
- `ErrataCorrection`: `published_as{…row}`, `should_read_as{…row}`, `gazette_ref`.
- The remaining types follow `plan.md` §5.3. Keep the schemas minimal and add fields only if the evidence exists.

## Self-consistency checks (these produce `self_consistency` and feed confidence)
- The sum of row extents equals the printed total (±0.0005 ha).
- ha↔ac agreement with **either** factor 2.47 or 2.47105 (see `land-domain-knowledge` §5); documents mostly use 2.47.
- amount ≈ ac × rate (±₹1 per row). `rate` comes from the same document or its linked DLPNC/SLPNC.
- The survey number exists in FMB/cadastral for the stated village. This is a soft check that feeds confidence only; never auto-correct.
- The date lies within the §5 range, and the stage date order is plausible.

## Normalisers (`pipeline/normalize/`, pure functions, ≥ 60 table-driven tests + hypothesis)
- `parse_extent`: `"0.95.50"→0.9550`, `"0 - 53.50"→0.5350`, `"10.30.50"→10.3050`, `"904.40.0"→904.40`; cents and acres with units.
- `parse_amount`: `"ரூ.12,60,000/-"`, `"1,35,496/-"`, and number words in Tamil and English.
- `parse_survey`: `"177/4A"→(177,"4A")`, `"6,7,8,10A"` as a list, `"172/1 மற்றும் 172/2"`, and Tamil numerals.
- `normalise_village`: via `config/aliases.yaml`, with fuzzy matching only above ratio 90 and restricted to the 7 villages.
- `normalise_name`: Tamil → ISO-15919 → a Latin key, stripping honorifics (Thiru, Tmt, திரு, திருமதி) and splitting relations (S/o, W/o, மகன், மனைவி, க/பெ, த/பெ).
- `parse_date`: numeric and Tamil month names → ISO.

## Batch scheduler (`make extract-drain`)
- Bedrock is cost-bound, not rate-bound (4 parallel workers; ~4 h for the corpus). Groq fallback is ~97 pages/day. The priority queue order is: GO/AS/LPS → AWARD_7_2/7_3 → FORM_F → DISBURSEMENT/COURT_DEPOSIT → LDR/POSSESSION → SEC32 → FORM_E.
- Drain up to the daily budget per route, minus a 10% reserve. The scheduler is resumable, and it is idempotent by (page hash, route).
- Log consumption to `docs/metrics.md` every day.

## Loading (`make load-extractions`)
- `extraction` rows go to `parcel_fact` and `acquisition_event`. Keep **all** facts. Errata supersede via `supersedes_id`. Conflicting facts from different documents are *both* kept, and P5 turns them into discrepancies.
- The stage comes from `classified_type` through the stage map in `land-domain-knowledge` §2. The event date is the document date, or the handover date for LDR documents.

## Golden set (qa-evaluator)
- ≥ 150 rows across 8 document types and all 7 villages, labelled from the images.
- 20 rows are reserved as a *hidden* set that is never shown to prompt authors.
- Uncertain items → `needs_human` → one batched user review of 40 items or fewer.

## Targets and exit gate
Targets are in `plan.md` §8 P2.
```
make ocr extract extract-drain resolve load-extractions && make eval-extract && make test
psql -c "select count(*) from router_log where privacy_tier='PII' and trains_on_free_tier" # must be 0
```
The report covers:
- field metrics by document type
- coverage (the share of FMB parcels with at least one fact, by village)
- the escalation mix (local, Groq, review)
- quota used against the budget
- the top 5 failure patterns, each with an example image path
