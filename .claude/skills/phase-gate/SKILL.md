---
name: phase-gate
description: Operating model for the zero-human-implementation build — how the lead agent starts, dispatches, verifies and closes each phase; when and how to escalate a decision to the user (decision authority); definition of done; progress and decision logs. Load at the start and end of every phase, and whenever a decision, blocker or discrepancy appears.
---

# Phase Gate & Escalation Protocol

**Roles**
- **The user is the decision authority.** They do not write code, and they may not read code.
- **The lead agent** (the main Claude Code session) plans, dispatches subagents, integrates their work and verifies it.
- **Subagents** (`.claude/agents/*.md`) implement.

## 1. Starting a phase
1. Read `plan.md` §Phases for this phase, and read the phase's skill (`phaseN-*`).
2. Read `docs/progress.md` and `docs/decisions.md` to pick up the current state.
3. Break the phase into tasks with explicit owners (subagent), inputs, outputs and acceptance checks. Record them in `docs/progress.md` under a `## Phase N` heading.
4. Dispatch independent tasks **in parallel**, as several Agent calls in one message. Give each subagent:
   - the task ID
   - the files it owns (no two parallel agents may own the same file)
   - the skill(s) to load
   - the acceptance command it must make pass
   - the instruction to report back its changed files, the commands it ran with their results, and any open questions

## 2. During a phase
- Subagents never make product or scope decisions. They return a question, and the lead agent decides or escalates.
- The lead agent **may decide alone** when the choice is reversible, internal, has a conventional default, and matches `plan.md`. Log the decision in `docs/decisions.md` as `D-###`, with context, choice and alternatives.
- The lead agent **must escalate to the user** (AskUserQuestion: 2–4 options, recommended option first, one-line trade-offs) when any of these apply:
  - scope changes versus `plan.md` or the proposal
  - any spend of money, or a paid model or API
  - sending owner-level personal data to a provider that trains on free-tier data
  - the dataset contradicts `land-domain-knowledge` in a way that changes results
  - a phase exit criterion cannot be met
  - an external account or API key is needed (the user must create it: Gemini AI Studio, GitHub token, Mistral, Groq, OpenRouter, AWS SSO)
  - a demo-visible UX choice has two or more reasonable options
  - anything is irreversible (deleting data, publishing, pushing to a remote, deploying to AWS)
- Batch escalations. Ask at most once per half-day of work unless you are blocked.

## 3. Closing a phase (the gate)
A phase closes only when **every** item holds:
1. The phase's acceptance commands pass. Run them yourself; do not trust a subagent's claim.
2. `make test` passes. There are no skipped tests without a logged reason.
3. `qa-evaluator` has reviewed the phase diff and its blocking findings are fixed.
4. Metrics have been appended to `docs/metrics.md` when the phase defines metrics.
5. `docs/progress.md` has been updated with done, deferred and risks.
6. A **gate report** has been sent to the user: at most 10 lines covering what works (with the demo command), metrics against targets, deviations, and decisions needed. If the phase is demo-visible, include a screenshot path.

If a criterion fails:
- Fix it and re-run the checks, with at most 2 repair loops.
- If it still fails, escalate with options (cut scope, extend time, or accept the risk).

## 4. Non-negotiables (every phase)
- **No hard-coded answers.** Brief queries and their answers must never appear as literals in application code. Tests use the *comparable/unseen* query set in `eval/queries.yaml`.
- **Evidence or it didn't happen.** Every extracted value stores `doc_id, page, bbox|line, extractor, model, confidence`.
- **Free models only.** Every model call goes through the router, which enforces the registry in `free-model-router`. AWS (Bedrock, S3, DynamoDB, Lambda) is capped at **US$15** in total (D-028): run `make aws-cost` before and after every AWS job and stop at 80%.
- **Secrets:**
  - Keys live only in `.env`, which is git-ignored.
  - Never print keys in logs.
  - The AWS SSO URLs in `Documents/` are not secrets, but credentials are, and they must never be committed.
- **Personal data:** see `land-domain-knowledge` §13.
- **Reproducibility:** every pipeline step is a `make` target and idempotent (content-hash caching).
- **Honesty:** report failures with their output. Never claim a metric you did not measure.

## 5. File conventions
- `docs/progress.md`: the phase checklist and status (the lead agent owns it).
- `docs/decisions.md`: the decision log `D-###` (the lead agent owns it).
- `docs/metrics.md`: measured numbers with date and command (`qa-evaluator` appends).
- `docs/escalations.md`: questions asked, answers given, and the date.
