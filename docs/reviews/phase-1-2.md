# Gate review: Phases 1 and 2 (independent evaluator, 2026-09-27)

**Verdict: FAIL.** There are 4 blocking issues. Everything else checked is green.

## Scope and evidence
- **Documents read:**
  - plan.md §8 rows for P1 and P2
  - docs/decisions.md D-020 to D-053
  - docs/metrics.md
  - docs/progress.md
  - docs/team/*.md
- **Checks run:**
  - `make test`, offline
  - `git ls-files` and `git check-ignore`
  - a grep for SDK and endpoint calls
  - psql through `docker compose exec db`
  - `data/router_log.sqlite`
- No live model calls and no AWS calls were made.

| Check | Result |
|---|---|
| 1 `make test` (offline) | ✅ 818 passed, 2 skipped, 4 deselected (live), 55 s |
| 2 Secrets or PII tracked | ✅ No commits yet (0 tracked files). All `eval/**/labels.jsonl`, `eval/**/pages/`, `data/`, `.env`, `Dataset/` and `Documents/` are ignored. No key patterns in the 546 files a first commit would add. ❌ One README would commit owner names (B2) |
| 3 Model SDK calls outside `app/router` | ✅ None in pipeline/, planet/, spikes/ or scripts/. `app/workspace/dynamo.py:44` uses boto3 for DynamoDB only, which is not a model call |
| 4 Hard-coded brief answers | ✅ None. `pipeline/gis/ground_truth.py` holds the skill §9 anchor totals and is used only by `pipeline/gis/kg_check.py` |
| 5 DB sanity | ✅ extraction 26,269, parcel_fact 42,380 and acquisition_event 15,342 match metrics.md. `classified_type`: 0 OTHER and 38 NULL (the pending documents, D-046). Parcels with ≥ 1 event: 1,241/1,242 (≥ 85% met). `SET ROLE agent_ro; SELECT FROM owner` fails with *permission denied* ✅. ❌ Names are still readable through `extraction.row_json` (B1) |
| 6 docs/team vs metrics.md | ❌ Spend is contradictory (B3). The held-out, Stage B, KG, classifier and DiD numbers match |
| P2 "0 PII to training tiers" | ✅ router_log: PII-tier calls went only to Bedrock 8B/3B and Groq, all `trains_on_free_tier: false` |

## Blocking issues
1. **B1: agent_ro can read owner names.** This is a PII leak into the agent path.
   - `pipeline/load/extractions.py:30` sets `PII_KEYS = {"owner_raw", "owners", "owner_other_read", "raw_cells"}`. It does not include `owner_vlm_raw`, which is set at `pipeline/extract/records.py:709`, and it does not cover the nested `raw` key.
   - Result: 1,519 `extraction.row_json` values are readable under `SET ROLE agent_ro`. The keys are `owner_vlm_raw` (1,456) and `raw` (63).
   - This contradicts `db/migrations/0007_extraction.sql:19` and `:158` ("owner names removed").
   - **Fix:** switch to allow-list stripping, or add these keys and strip any Tamil/English name + relation pattern. Add a test. Re-run `make load-extractions`. Re-probe with agent_ro and expect 0.
2. **B2: real owner names in a file that is not ignored.**
   - `eval/golden/README.md:59`, `:62` and `:63` quote the names from g07, g11 and g12. The README is not ignored, so the first commit would publish them.
   - **Fix:** replace them with `<owner>` placeholders.
   - Also review the comment at `pipeline/normalize/names.py:160`. The name may be real, so replace it with a synthetic one.
3. **B3: contradictory spend claims in the team docs.**
   - `docs/team/01-project-overview.md:61` and `docs/team/04-model-router-and-aws.md:52` say "≈ US$0.63".
   - `docs/team/09-results-and-honesty.md:19`, `docs/team/10-reviewer-qa.md:21` and metrics.md (Stage B ≈ $5.6) say ≈ US$6.3.
   - **Fix:** use the measured `make aws-cost` figure everywhere.
4. **B4: P2 exit criteria missed or not measured, and not escalated to the user.**
   - Held-out (partially seen) results below target: extent_ha 72.4% (target 90) and headers 93.3% (target 95).
   - Not measured anywhere: classification-claim (target ≥ 90%) and possession dates (target ≥ 95%).
   - D-053 closes P2 with owner "Lead". CLAUDE.md requires AskUserQuestion for a missed exit criterion.
   - **Fix:** get and record the user's waiver, or score these on `eval/heldout2/` (fresh; built today).

## Non-blocking notes
1. **Test counts are stale.** docs/team 03, 10 and 12 say "~750" and doc 09 says "~770". The measured count is 818.
2. **Stale remaining-work line.** `docs/team/05-phase1-data-foundation.md:56` still says the classifier full run is running. D-046 says it is done.
3. **Document count.** The DB has 2,362 documents (distinct sha256), but the plan and docs 09/10 say 2,361 unique PDFs. Explain the extra one (a zip?) or reconcile.
4. **Missing qualifier.** `docs/team/10-reviewer-qa.md:29` quotes the held-out 96/91% without "partially seen" (D-043). Doc 09 discloses it.
5. **Bedrock call path.** `infra/bedrock_batch/` calls Bedrock from Lambda outside `app/router`. D-042 approves this, and requests are built by the router's collect mode. Make sure the Lambda records its results back into the router ledger.
6. **38 unclassified documents.** These documents have a NULL `classified_type` (pending or fallback). Close them or list them in progress.md.
7. **Unit test for PII stripping.** Add one for `strip_pii` that runs over every key written by `pipeline/extract/records.py`. This prevents a repeat of B1.
8. **P1 gate evidence.** The P1 gate review was skipped (D-046). FMB totals within 0.5%, the STAC inventory and the rasters are reported by the builders only and were not re-run here.
9. **eval/classify/labels.csv is not ignored.** It contains no owner names, but consider whether it should be committed.

## Resolution (lead, 2026-09-27)
- Blocker 1 (agent_ro could read owner_vlm_raw): fixed. 4,153 rows scrubbed, and the key was added to the loader's PII strip list; agent_ro now sees 0.
- Blocker 2 (names in eval/golden/README.md, names.py comment): redacted.
- Blocker 3 (spend in team docs 01/04): corrected to ≈ US$6.3.
- Blocker 4 (P2 targets): measured on the fresh heldout2 set (survey 89.5 / extent 79.2 / owner 83.3 / headers 91.1%). **User accepted closure with these as documented limitations (D-055).**
- **Verdict after fixes: PASS (with accepted limitations).**
