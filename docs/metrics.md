# Metrics

| Date | Metric | Value | Target | Command |
|---|---|---|---|---|
| 2026-09-26 | Bake-off (g) Bedrock 8B whole page: numeric recall | 63% (123/194) | — | `python -m spikes.ocr_bakeoff.fullpage_recall data/bakeoff_bedrock/fullpage.json` |
| 2026-09-26 | Bake-off (g): survey-ref recall | 83% (65/78) | — | same |
| 2026-09-26 | Bake-off (g): owner recall | 59% (33/56) | — | same |
| 2026-09-26 | Bake-off (g): cost / latency | $0.00049 per page, 3.3 s | — | same |
| 2026-09-26 | Groq vision (row bands): numbers / surveys / owners | 26% / 38% / 16%, ~97 pages/day | — | `value_recall.py data/bakeoff/results.json groq_cell_reread` |
| 2026-09-26 | Satellite spike reproduction (legacy grid) | medians ±0.002; crop-like both 530 vs 532 | ±0.01 | `make s2-spike` |
| 2026-09-26 | Unit tests | 398 passed | green | `make test` |
| 2026-09-26 | Unit tests (P0 gate, qa-evaluator re-run) | 406 passed, 4 deselected (live) | green | `make test` |
| 2026-09-26 | **Held-out extraction (12 unseen pages), legible: survey / extent_ha / owner / headers** | 74.1% / 70.7% / 38.6% / 88.9% — **MISSED** (golden was over-fitted) | 92 / 90 / 85 / 95 | `PYTHONPATH=. uv run python -m eval.extraction.run --set heldout --live` ($0.011) |
| 2026-09-26 | Held-out by type: AWARD_7_2 survey/extent/owner | 30% / 33% / 17% | — | same |
| 2026-09-26 | Held-out by type: LDR, CHITTA, FORM_F survey/extent | ≈ 100% | — | same |
| 2026-09-26 | Held-out after repair loop 1, legible: survey / extent_ha / owner / amount / headers | 74.1 / 72.4 / 56.8 / 57.1 / 93.3% — MISSED; AWARD_7_3 + DISBURSEMENT now ~100%; AWARD_7_2 unchanged (30/33%); LDR needs_human regressed (extent 100→0%) | 92 / 90 / 85 / — / 95 | `eval.extraction.run --set heldout --live` ($0.0025) |
| 2026-09-27 | Classifier v2 stage accuracy (100 labels) | 0.90 (v1 rules: 0.45) | ≥ 0.90 | `make eval-classify` (reported by data-engineer; re-verified after the full run) |
| 2026-09-27 | Held-out after loop 3 (legible): survey / extent / owner / headers | 74.1 / 72.4 / 63.6 / 93.3% — AWARD_7_2 unchanged (30/33/35%); all other types ≈100% (AWARD_7_3 owner 93%) | 92/90/85/95 | `eval.extraction.run --set heldout --live` (cache) |
| 2026-09-27 | dev2 after loop 3 (legible): survey / extent / owner / amount | 100 / 100 / 96.0 / 97.6% | — | `eval.extraction.run --set dev2` |
| 2026-09-27 | Held-out after loop-3 follow-up (cache-only) | unchanged: AWARD_7_2 30/33% (fill-down needs an extent; survey values sit in the serial column); held-out now "partially seen" (D-043) | — | `eval.extraction.run --set heldout` |
| 2026-09-27 | Held-out after compensation-group fix (legible, partially seen) | survey 88.9 / extent 72.4 / owner 90.9 (MET) / amount 94.3%; AWARD_7_2 survey 70, owner 87, amount 93% | 92/90/85 | `eval.extraction.run --set heldout` (cache) |
| 2026-09-27 | Held-out after the final loop-3 step (legible, partially seen) | survey 96.3 ✅ / owner 90.9 ✅ / amount 94.3 / extent_ha 72.4 / headers 93.3% | 92/85/—/90/95 | `eval.extraction.run --set heldout` (cache) |
| 2026-09-27 | P3 DiD vs control group, post-possession rabi (1,242 parcels) | farmland-like (p ≥ 0.7): 1,143; significant vigour drop vs controls: 143; significant rise: 513; plough detected: 16/1,144 parcel-seasons | — | `python -m planet.controls did && … report` → eval/planet/did_report.json |
| 2026-09-27 | Demo parcel Melathattaparai 233 (possession 2025-03-21) | vigour DiD +0.35 [−0.13, 0.90] (farmland-like p 1.00); **plough DiD +2.15 [0.85, 3.70]**; only 1 post season | — | parcel_did |
| 2026-09-27 | Classifier v2 full run, eval on 100 labels | stage 0.90 / type 0.80 / scheme 0.96 | ≥ 0.90 stage | `make eval-classify` |
| 2026-09-27 | Matcher first run | facts linked 74.1% (16,438/22,194), ambiguous 3.2%; events 68.5%; eval village+survey 50/50 | — | `make match && make match-eval` |
| 2026-09-27 | Matcher after D-052 | facts 82.1% (18,439/22,472), ambiguous 3.2%; events 79.7% (incl. 1,273 block-level at conf 0.5); eval village+survey 50/50; parcel-level stalls 241, block-level-only 933 (not findings) | — | `make match && make match-eval` |
| 2026-09-27 | **Stage B complete** | 5,796 pages (5 failed); cost ≈ $5.6 (router ledger; Cost Explorer lags) | ≤ $7 | `scripts/stageb_loop.sh 10` |
| 2026-09-27 | Knowledge graph after Stage B | 26,269 extractions · 42,380 facts (24,054 single-parcel links + survey-level links) · 15,342 events · 1,302 review items · 1,082/1,242 parcels (87%) with linked facts | — | `make load-extractions match views` |
| 2026-09-27 | Unit tests (P1/P2 gate, qa-evaluator re-run, offline) | 818 passed, 2 skipped, 4 deselected (live) | green | `make test` |
| 2026-09-27 | KG counts re-verified by qa (psql) | extraction 26,269 · parcel_fact 42,380 · acquisition_event 15,342 · document 2,362 (38 classified_type NULL, 0 OTHER) · parcels with ≥ 1 linked event 1,241/1,242 (1,081 direct) | ≥ 85% parcels with an event | `docker compose exec db psql …` |
| 2026-09-27 | agent_ro PII probe | `owner` denied ✅; **1,519 `extraction.row_json` values readable by agent_ro contain owner names** (keys `owner_vlm_raw` 1,456, `raw` 63) ❌ | 0 | `SET ROLE agent_ro; SELECT … FROM extraction` |
| 2026-09-27 | PII to training-tier models (router_log, outcome=ok) | 0 (PII calls only to Bedrock 8B/3B and Groq, all `trains_on_free_tier: false`) | 0 | `data/router_log.sqlite` |
| 2026-09-27 | eval/heldout2 built (fresh, seed 20260930) | 10 pages, 56 rows, 1 needs_human; 0 sha256 overlap with golden/heldout/dev/dev2/classify; not yet scored | — | see eval/heldout2/README.md |
| 2026-09-27 | **FINAL extraction score: fresh heldout2 (10 never-seen pages, legible)** | survey 89.5 / extent_ha 79.2 / extent_ac 100 / owner 83.3 (precision 45) / class 93.8 / doc_type 100 / headers 91.1% | 92 / 90 / — / 85 / — / — / 95 | `eval.extraction.run --set heldout2 --live` ($0.0095) |
| 2026-09-27 | Findings engine (P5 T5.3–T5.5) | 1,388 findings: PV3 380 (117 medium drop vs controls, 263 low "still farmed" leads), PV4 79 blocks / 834 ha idle, compensation 127 (43 high), extent 485 (21 high), doc drift 2, FMB 270, class conflict 13, extraction errors 32 | — | `make findings` |
| 2026-09-27 | **Agent end-to-end on the real KG (8 queries: 4 demo + 4 unseen incl. Tamil)** | plan validity 8/8 · expected tool 8/8 · answers match SQL reference 8/8 · tool success 15/17 · verified-claim rate 0.785 · critic always another vendor (24/24) · p50 50.5 s · actual $0 (shadow $0.31) · ledger verified | — | `make eval-agent` · `make verify-ledger RUN=…` |
| 2026-09-27 | AWS spend, router ledger (live) vs Cost Explorer (lags) | $7.90 vs $0.72 of the $15 cap | stop at $12 | `GET /stats/overview` |
