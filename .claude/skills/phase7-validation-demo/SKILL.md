---
name: phase7-validation-demo
description: Phase 7 (Day 11–13) — final evaluation (extraction, matching, agent, UI, privacy), route comparison (routed vs all-proprietary vs all-open on accuracy, latency, shadow cost), chaos drills, clean-machine demo rehearsal, README/architecture/eval-report/demo-script, proposal sync, and the optional approval-gated AWS serverless stretch. Load for evaluation reporting, packaging, documentation or deployment.
---

# Phase 7 — Validation, Hardening & Demo

**Owners:**
- qa-evaluator (lead)
- docs-writer
- backend-engineer (packaging, AWS stretch)
- all agents on fixes

## T6.1 Full evaluation (`make eval`)
- Run every suite: classification, extraction (including the hidden 20), matching, **planet (student vs audit)**, **findings audit precision**, agent, the unseen queries, chaos and privacy audit, and UI e2e.
- Append the results to `docs/metrics.md` with the date, commit and command.
- If a target is missed, do **not** tune on the hidden set. Report the gap honestly.

## T6.2 Route comparison (`make eval-routes`)
- Run the 25-query suite under three policies:
  - (a) **routed** (ours)
  - (b) **all-proprietary**: every LLM task on Gemini flash-lite/flash, PSEUDO-only. PII steps are skipped and marked N/A.
  - (c) **all-open**: every task on Groq Qwen/gpt-oss or local
- Report: rubric score, verified-claim rate, p50/p95 latency, calls per query, shadow cost per query, actual cost, and quota consumed.
- Record fixtures for (b) and (c), so reruns cost nothing.
- Output `docs/eval-report.md#routes`, with a chart made in the `dataviz` style.

## T6.3 Chaos drills (`make chaos-suite`)
- Gemini down, Groq slow, Cohere trial quota exhausted, a corrupted page image, malformed JSON, an SQL guard violation attempt ("drop table"), and a ledger tamper.
- Each drill must end in a graceful result, with its fallback visible in the trace.

## T6.4 Demo rehearsal
- `make demo` does the following on a clean clone plus `.env`:
  - `db-up`
  - restores `data/snapshots/kg_latest.dump`, a KG snapshot, so the demo never re-OCRs
  - starts the API and web
  - opens the browser
- It must run twice consecutively without manual steps.
- `docs/demo-script.md` has an 8-minute script (`plan.md` §13, which opens on the Melathattaparai 233 Paper-vs-Planet hook) with exact clicks, fallback plans (fixtures mode if Wi-Fi fails: `API_MODE=replay`), and the expected numbers on screen, each with its source.

## T6.5 Documentation (docs-writer)
- **`README.md`:** outcome summary, 5-command quick start, screenshots (masked), architecture diagram, model lineup, and a "FarmwiseAI-provided vs team-sourced data" table.
- **`docs/architecture.md`:** the as-built diagrams from P3/P5, tool contracts, and routing policy.
- **`docs/eval-report.md`:** all metrics with links to `docs/metrics.md` rows.
- **`proposal.md` sync:** update the "Status / measured results" appendix only. Never change the promises made in the original sections without the user's approval.

## T6.6 AWS serverless deployment (approved: D-027; cap $15, D-028)
Approved scope, within `Documents/FarmwiseAI_TCE_AWS_Access_Guide.pdf`. Team number is **49**; the profile is `fai-builder`. Every create/delete still needs a one-line confirmation from the user (irreversible or outward-facing):
- Region `ap-south-1`. Names `fai-tce-team49-*`. No EC2/RDS. Private S3. The existing `FAI-TCE-LambdaExecutionRole`.
- Components:
  - S3 for page images and the KG export (GeoParquet)
  - Lambda (container image, Mangum) running the API with a **DuckDB-spatial** adapter over GeoParquet, since there is no RDS
  - DynamoDB for workspaces and runs
  - API Gateway
  - Bedrock Ministral 8B as a capped fallback
- Spend guard: `make aws-cost` before and after every AWS job (Budgets API is denied). Stop and escalate at 80% of the $15 cap.
- A teardown script `make aws-down` that has been tested.

## Final gate
```
make eval && make chaos-suite && make e2e && make demo-check
```
The final report to the user covers:
- the metrics table against targets
- the route comparison headline
- the privacy audit
- known limitations
- the demo readiness checklist
- a request for sign-off
