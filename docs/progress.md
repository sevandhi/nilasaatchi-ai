# Progress: NilaSaatchi AI

_Last updated: 2026-09-27 11:30 IST, by the lead agent. Decisions (D-###) are in `docs/decisions.md`; measured numbers are in `docs/metrics.md`._

## Overall completion: **≈ 88%** (weighted by effort)
| Phase | Weight | Done | Contribution |
|---|---|---|---|
| P0 Bootstrap & spikes | 10% | 100% | 10.0 |
| P1 Data foundation | 12% | 100% ✅ (classifier v2: stage 0.90, type 0.80, scheme 0.96) | 12.0 |
| P2 Paper (document intelligence) | 18% | 100% ✅ at core scope (Stage B 5,796 pages; 42k facts; 87% of parcels linked) | 18.0 |
| P3 Planet (satellite evidence) | 14% | 100% at core scope ✅ (control-group DiD done, D-045) | 14.0 |
| P4 Agentic core | 16% | 90% ✅ (router + API + lean agent loop on real KG; 8/8 eval queries correct) | 14.4 |
| P5 Proof (matching, findings, evidence packs) | 12% | 80% (matcher, lifecycle, 1,388 findings + evidence packs; unseen-query suite pending) | 9.6 |
| P6 Workspace UI | 10% | 100% ✅ (9 pages live; e2e 9/10) | 10.0 |
| P7 Validation, AWS deploy, demo | 8% | 0% | 0 |
| **Total** | 100% | | **≈ 88%** |

## Hardest pending tasks (in order of risk)
1. **AWS session lifetime (2 h per FarmwiseAI; Lambda role lacks Bedrock: permission under review, D-044).** Bulk Bedrock runs in ≤ 110-min chunks meanwhile. Previously: Every Bedrock batch (classifier full run, Stage B ~4.5k pages, AWARD_7_2) stalls when SSO expires. Proposed fix: run bulk Bedrock jobs as **AWS Lambda workers** using `FAI-TCE-LambdaExecutionRole` (no SSO expiry), or run in ≤ 50-min chunks with a re-login between them.
2. **Stage B bulk extraction** (~4.5k pages, ≤ $5): depends on the classifier full run and on (1).
3. **AWARD_7_2 award schedules**: held-out 30% → targeted fix (merged survey cells, summary tables) in flight; must pass the held-out test before its Stage B.
4. **Parcel matching (P5)**: document rows → FMB `parcel_uid` across survey/sub-division variants, OCR confusions and the 366 FMB overlaps.
5. **Honest post-possession signal (D-035)**: control-group DiD + ploughing signature; the controls extraction stalled at 240/307 scenes under CPU contention and must be resumed.
6. **Findings engine + evidence packs (P5)**: PV1–PV6 + 10 document rules with caveats; the qa audit of findings precision.
7. **Agent end-to-end on the real KG (P4)**: tools over real facts, critic/judge, ledger; 25-query unseen suite ≥ 85%.
8. **Workspace UI (P6)**: Paper-vs-Planet timeline, season slider, evidence packs, cross-filter (React JS).
9. **Serverless AWS deploy (P7)**: Lambda + API Gateway + DuckDB-spatial on S3 (no RDS), within the $15 cap.
10. **Final evaluation + demo rehearsal (P7)**.

## Incidents today (resolved or mitigated)
- Claude API timeouts and session limits interrupted agents repeatedly; all were resumed with context.
- AWS SSO expired twice (19:29 and ~01:20). Bedrock calls fell back or failed; guards now stop batches on fallback/expired credentials.
- The classifier full run spun for ~9 h: quick OCR had **no cache** and ~25 parallel Tesseract jobs competed with the satellite job (load avg 60). Stopped; an OCR text cache was added (`data/quick_ocr_cache/`).
- Classifier v2 once overwrote types with OTHER from failed calls; restored; types are never overwritten by failed calls now.

## Phase board
| Phase | Status | Gate |
|---|---|---|
| Planning (plan, proposal, skills, agents) | ✅ Done 2026-09-26 | — |
| Pivot to v2 "Paper vs Planet" + satellite spike | ✅ Done 2026-09-26 (D-008) | docs/spikes/sentinel2-spike.md |
| **P0** Bootstrap & spikes | ✅ **Closed** 2026-09-26 (qa PASS) | docs/reviews/phase-0.md |
| **P1** Data foundation | 🟡 **Gate pending**: classifier v2 passes (0.90); its full run over 2,362 docs must be redone (AWS expired; OCR cache added) | — |
| **P2** Paper: document intelligence | 🟡 In progress: Stage A done; Stage B split (D-038); repair loop 3 for AWARD_7_2 | — |
| **P3** Planet: satellite evidence | 🟡 In progress: T3.2/T3.3/T3.5/T3.4/T3.6 done; control-group redesign (D-035) + Cohere relabel running | — |
| **P4** Agentic core | 🟡 **Started 2026-09-27 00:45**: agent-architect (graph, 13 tools, gateway, verifier/critic/judge, ledger, 25-query suite) ∥ backend-engineer (FastAPI + SSE, workspaces, evidence, chips, review queue, 0011) on a fixture KG | — |
| **P5** Proof: verification & analytics | ⬜ Not started (needs P2 facts + P3 DiD) | — |
| **P6** Workspace UI (React JS, D-011) | ⬜ Not started | — |
| **P7** Validation, AWS deploy & demo | ⬜ Not started (AWS approved: D-027/D-028, cap $15) | — |

## Currently running (agents)
| Agent | Task | Spend |
|---|---|---|
| data-engineer | Classifier v2 (Bedrock 3B/8B) full run after restoring the types wrongly set to OTHER (0 OTHER now) | ~$1 |
| eo-engineer | Control group (2–6 km ring, non-acquired farmland) + ploughing signature + DiD (`parcel_did`); Cohere second-teacher relabel (198/300) | $0 (free tiers) |
| qa-evaluator | eval/dev2/: 8 large AWARD_7_2 schedule pages for repair loop 3 | $0 |
| (next) doc-intel-engineer | Repair loop 3 on dev2 → held-out re-test → AWARD_7_2 joins Stage B | ≤ $0.10 |
| (next) lead | Stage B run on passing types once the classifier finishes: `make extract-drain RUN=1` | ≤ $5 |
| agent-architect | P4 agent graph + tools + ledger + unseen-query suite (fixture KG) | < $0.10 |
| backend-engineer | P4 FastAPI/SSE API, workspaces, exports, evidence/chips endpoints | $0 |

**AWS:** team 49, SSO renewed 2026-09-27 00:15. Router ledger ≈ $0.06; Cost Explorer $0.00 (lags ~1 day). Cap $15, stop at $12.

---

## Phase 0: Bootstrap & spikes ✅
| Task | Owner | Result |
|---|---|---|
| T0.1–T0.3 Scaffold, uv/Py3.12, PostGIS+pgvector on :5439, Makefile, migrations | lead | ✅ |
| T0.4/T0.6 Router + `make doctor` | agent-architect | ✅ 7 providers; privacy-tiered; AWS spend guard; SSO-expiry handling; strict order; vision proof (D-037) |
| T0.5 Golden set | qa-evaluator | ✅ 20 pages / 103 rows |
| T0.5 OCR bake-off → routing | data-engineer + lead | ✅ D-010: Bedrock Ministral 8B whole-page wins |
| T0.5b Satellite spike reproduction | eo-engineer | ✅ within ±0.002 (legacy grid) |

## Phase 1: Data foundation 🟡 (gate pending)
| Task | Owner | Result |
|---|---|---|
| T1.1 Catalog, dedup, previews | data-engineer | ✅ 2,362 docs / 12,859 pages (incl. zip members) |
| T1.3 Schema 0003/0004 + views + agent_ro | gis-engineer | ✅ |
| T1.4 GIS load + reconciliation | gis-engineer | ✅ geodesic union 901.50 ha; 366 overlaps; 28 parcels outside their survey (D-023) |
| T1.5 Rasters + zonal stats | data-engineer | ✅ DEM, slope, JRC water, GloFAS, WorldCover for 1,242/1,242 parcels |
| T1.5b S2 inventory + WorldCover fractions | eo-engineer | ✅ 587 scenes |
| T1.6 Classifier | data-engineer + lead | ✅ **v2 passes the gate: stage accuracy 0.90** on the 100 labels (v1 rules: 0.45). 🔄 Full run over 2,362 docs relaunched by the lead 2026-09-27 (Bedrock only; failed calls never overwrite types) |
| Classifier labels | qa-evaluator | ✅ 100 docs, 33% folder ≠ type |
| **Gate to do** | lead + qa | Re-score v2 → qa review → close P1 |

## Phase 2: Paper 🟡
| Task | Owner | Result |
|---|---|---|
| Stage A pipeline (whole-page Bedrock + per-doc-type mapping + consistency) | doc-intel | ✅ golden legible: survey 95.5 / extent 100 / owner 100 / headers 100% |
| 0007 tables, pseudonymised owner view, loader, Stage-B runner, D-033 review rule | doc-intel | ✅ |
| Held-out set (12 pages) | qa-evaluator | ✅ |
| Repair loops 1–2 | doc-intel | ✅ every type ≈100% on held-out **except AWARD_7_2** (survey 30 / extent 33 / owner 30%) |
| Dev sets | qa-evaluator | ✅ eval/dev (6 pages); ✅ eval/dev2 (9 pages, 79 rows: large schedules, sideways scans, heirs/share tables) |
| Repair loop 3 (AWARD_7_2 schedules) | doc-intel | 🔄 started 2026-09-27 |
| Stage B (passing types; ~4k pages; ≤ $5) | lead | ⏳ after classifier v2 |
| Stage B (AWARD_7_2) | lead | ⏳ after loop 3 passes the held-out test |

## Phase 3: Planet 🟡
| Task | Owner | Result |
|---|---|---|
| T3.2 per-parcel indices | eo-engineer | ✅ 381,294 obs (307 scenes × 1,242); dn_offset bug fixed (D-031) |
| T3.3 seasonal features | eo-engineer | ✅ 40,986 parcel-seasons, λ=1000 |
| T3.5 chips | eo-engineer | ✅ |
| T3.4 teacher labels + LightGBM student | eo-engineer | ✅ macro-F1 0.634 / acc 0.936 vs Gemini labels; 🔄 Cohere relabel (D-036b) |
| T3.6 event windows | eo-engineer | ✅ with fallback dates (per-parcel dates await P2) |
| **Control-group DiD + ploughing signature (D-035)** | eo-engineer | 🔄 extracting controls |
| qa blind audit (50 items) | qa-evaluator | ⏳ pack ready at eval/planet/audit/ |

## Key corrections made along the way (honesty log)
- 911.395 ha is the Tamil *proposal*, not the AS letter (D-014).
- "The map exceeds the sanction" was withdrawn: the parcels overlap (D-023).
- Satellite scenes: 587, not 170; headline restated to 444/1,073 (D-015).
- Post-monsoon green-up is near-universal, so the post-possession claim is redesigned as a control-group DiD (D-035).
- The Golden-set extraction scores were over-fitted; the held-out set exposed AWARD_7_2 (D-034).

## Next milestones
1. Classifier v2 ≥ 90% → **close P1**.
2. Stage B (passing types) → load facts → match to parcels.
3. Loop 3 → AWARD_7_2 → Stage B remainder → **close P2**.
4. Control-group DiD + qa audit → **close P3**.
5. **Start P4** (agentic graph, tools, verifier, ledger, API). The router is already built.

## Update 2026-09-27 11:30
- **Held-out extraction (partially seen, D-043), legible:** survey **96.3% ✅**, owner **90.9% ✅**, amount 94.3%, extent_ha 72.4% (AWARD_7_2 group extents), headers 93.3%. Tuning against this set has stopped; the final number will come from a fresh heldout2 in P7.
- **P4 API:** ✅ done (21 endpoints, SSE, workspaces/exports, evidence, chips, review queue, masking; verified live by the lead: /health, /examples, 1,242 parcel features, /documents).
- **Lambda Bedrock worker:** code done (23 tests) but blocked because the FAI-TCE-LambdaExecutionRole lacks bedrock:InvokeModel. Everything created was torn down ($0 impact). FarmwiseAI is reviewing the permission (D-044).
- **Running:** classifier full run (Bedrock, OCR cache 1,377 pages); fresh eo-engineer (control-group DiD, D-035); fresh agent-architect (P4 graph/tools/ledger/25-query suite).
- **Next:** classifier done → P1 gate → Stage B in ≤ 110-min chunks (passing types + AWARD_7_2 with extent caveat).
- **AWS spend:** $0.634 of $15.

## Update 2026-09-27 11:55: P3 closed (core scope)
- DiD vs control group: 143 parcels with a significant post-possession drop vs controls; 1,143 farmland-like on greenness; demo parcel 233 shows a significant ploughing-like signal after handover (1 season; field verification needed). Report: eval/planet/did_report.json.
- Priority now (user): finish P1 (classifier run) and P2 (Stage B + load) with minimal Claude usage.

## Update 2026-09-27 12:00: P1 closed
- Classifier v2 full run done (2,362 docs; 38 pending review; 9 other-scheme). Gate: stage 0.90 ✅, type 0.80, scheme 0.96. Separate qa review skipped per the user's scope cut.

## Update 2026-09-27 13:00
- **Stage B running** (`scripts/stageb_loop.sh 10`, ~33 pages/min, auto-retry). Incremental load so far: 3,768 pages → 18,092 extractions, 30,266 parcel facts, 11,693 acquisition events, 1,001 review-queue items.
- **Started in parallel (independent of Stage B):** the matcher (facts/events → parcel_uid; all 30k facts were unlinked) + the parcel lifecycle/stalled views (P5 T5.1–T5.2), one lean gis-engineer. This connects Paper to Planet per parcel.
- AWS login expires ~14:05; one more device code is needed to finish Stage B.

## Update 2026-09-27 14:55: ✅ Phases 1, 2 and 3 complete (core scope)
- **Stage B done:** 5,796 pages, 5 failed, ≈ $5.6. KG: 26,269 extractions, 42,380 facts, 15,342 events, 1,302 review items.
- **Matcher:** 82.1% of matchable facts linked; 1,082/1,242 parcels (87%) have document facts; lifecycle views with parcel- vs block-level evidence.
- **Remaining (not started / partial):** P4 agent loop, P5 findings engine + evidence packs, P6 UI, P7 deploy/demo + fresh heldout2 evaluation.

## Update 2026-09-27 15:20: P1+P2 independent review PASS after fixes
- Fixed: owner_vlm_raw leak (4,153 rows scrubbed; loader patched), names redacted, doc spend corrected.
- Final extraction score (fresh heldout2): survey 89.5 / extent 79.2 / owner 83.3 / headers 91.1%, accepted as limitations (D-055).

## Update 2026-09-27 15:45: P5 findings engine done
- 1,388 findings (7 categories + extraction errors) with evidence packs (`make findings`, `make evidence-pack ID=…`).
- Next candidates: minimal UI to show findings on a map (P6), agent loop (P4), demo prep (P7).

## Update 2026-09-27 16:30: P4 agent loop done
- 8/8 eval queries (demo + unseen + Tamil) correct vs SQL references; cross-vendor critic; ledger verified. `make eval-agent`, `make verify-ledger`.
- Remaining: minimal UI (P6), demo packaging/rehearsal (P7), optional AWS deploy.

## Update 2026-09-27 17:00: P6 UI started
- Full glass-box UI per docs/ui-spec.md (9 pages); backend-engineer (endpoints) ∥ frontend-engineer (React JS). The broken dev ledger run was deleted (user OK); the ledger verifies: 122 runs / 2,253 entries.

## Update 2026-09-27 18:20: P6 UI built
- 9 pages on the live API (`make api` + `make web`); screenshots in docs/screens/. Fixes in flight: timeline truncation, event-label overlap, footer spend, chaos toggle wiring.

## Update 2026-09-27 18:45: P6 closed
- UI complete (`make api` + `make web` → http://localhost:5173); screenshots in docs/screens/. Remaining: P7 packaging + demo rehearsal (+ optional AWS deploy).

## Phase 7: Validation & demo + read-only cloud demo (started 2026-09-27 21:45)
User: P7 + read-only cloud demo (D-067); AWS create approved (bucket fai-tce-team49-data, Lambda fai-tce-team49-api, API Gateway fai-tce-team49-api; public link, no passcode). FarmwiseAI enabled Bedrock for FAI-TCE-LambdaExecutionRole.
| Task | Owner | Output | Acceptance |
|---|---|---|---|
| T7.1 Cloud read-only API + snapshot export + Lambda package + deploy/teardown scripts | backend-engineer | app/cloud/, scripts/cloud_*.py, infra/cloud_demo/ | local run of the cloud app serves every UI GET from the snapshot; tests pass |
| T7.2 UI read-only mode (cloud build) | frontend-engineer | VITE_READ_ONLY build | lint/build; e2e unchanged; cloud build hides uploads/agent run/review actions/refresh/chaos |
| T7.3 Final evaluation + chaos + privacy audit + demo-check | qa-evaluator | docs/metrics.md, data/eval/p7_*.json | measured numbers appended |
| T7.4 AWS deploy + Bedrock-from-Lambda self-test | lead | public URL, infra/RESOURCES.md | aws-cost before/after; URL loads all read-only pages |
| T7.5 README, architecture, eval report, 8-min demo script, proposal status appendix | docs-writer | README.md, docs/architecture.md, docs/eval-report.md, docs/demo-script.md | after T7.3 + T7.4 |
Done already (earlier today): make demo / demo-setup / package, ref/ separation, upload path + report (D-063..D-066).
