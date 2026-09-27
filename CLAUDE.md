# NilaSaatchi AI — Lead-Agent Operating Guide

This repository is built **entirely by AI agents**. The human user is the **decision authority**: they approve, choose and supply keys, and they never write code.

**Product:** NilaSaatchi AI, "Paper vs Planet". It cross-examines land-acquisition documents against satellite evidence.

## Source of truth
1. `plan.md`: architecture, phases, targets. **Read it first, every session.**
2. `docs/decisions.md`: decisions made since the plan was written. A decision here overrides `plan.md`.
3. `docs/progress.md`: where the build currently stands.
4. `proposal.md`: the external promise to FarmwiseAI. Do not contradict it without the user's approval.

## How to work
- At the start and end of each phase, load the `phase-gate` skill, then load the current phase skill:
  - `phase0-bootstrap`
  - `phase1-data-foundation`
  - `phase2-document-intelligence`
  - `phase3-satellite-evidence`
  - `phase4-agentic-core`
  - `phase5-verification-analytics`
  - `phase6-workspace-ui`
  - `phase7-validation-demo`
- Cross-cutting skills:
  - `land-domain-knowledge` (dataset truth)
  - `free-model-router` (all model usage)
- Dispatch the implementation to the subagents in `.claude/agents/`. Their responsibilities:
  - data-engineer, doc-intel-engineer, eo-engineer, gis-engineer, agent-architect: pipelines, extraction, satellite evidence, GIS and the agent core
  - backend-engineer, frontend-engineer: API and UI
  - qa-evaluator: gates and evaluation
  - docs-writer: documentation
- Run independent tasks in parallel. File ownership must never overlap.
- Verify every subagent claim by running the acceptance commands yourself.

## Escalate to the user (AskUserQuestion, recommended option first) when a choice involves:
- scope
- money
- PII leaving the machine on a training-tier provider
- data contradicting `land-domain-knowledge`
- a missed exit criterion
- a new account or key
- demo-visible UX forks
- anything irreversible (deploy, delete, push)

Otherwise decide, and log the decision in `docs/decisions.md`.

## Hard rules
- **Free models only.** Every call goes through `app/router` (see `free-model-router`).
- **No hard-coded brief queries or answers** in `app/`, `pipeline/` or `web/`.
- **Evidence on every extracted value.** Every claim in the UI must be verified or visibly downgraded.
- **Data handling:**
  - `Dataset/` and `Documents/` are read-only and never committed.
  - `.env` and `data/` are git-ignored.
  - Owner names are masked in demo material.
- **Environment:** Python 3.12 via `uv run` (the system Python 3.14 breaks paddle). PostGIS runs via `docker compose`.
- **Honesty:** report measured numbers only. Report failures with their output.

## AWS (D-027, D-028)
- Team **49**, role FAI-TCE-Builder-AI, region **ap-south-1** only; profiles `fai-builder` (build) and `fai-cost` (Cost Explorer). Names `fai-tce-team49-*`; S3 private; only `FAI-TCE-LambdaExecutionRole`; no EC2/RDS/IAM changes.
- Approved: S3, Lambda, API Gateway, DynamoDB, CloudWatch Logs, Bedrock (Ministral 3 3B/8B, Titan Text V2, Titan Multimodal). Textract is denied.
- **Spend cap $15**: run `make aws-cost` before and after every AWS job; stop and ask the user at 80%.
- If the SSO session has expired, run `make aws-login` and give the user the device URL/code. Never ask for their password or MFA.
- Creating or deleting AWS resources is outward-facing: confirm each with the user.

## Quick commands (as they become available)
`make setup` · `make doctor` · `make db-up` · `make test` · `make eval` · `make demo`
