# Phase 0 gate review (qa-evaluator, 2026-09-26)

**Verdict: PASS**. No blocking issues.

## Checks
1. **Secrets.** Scanned the tree (excluding .env, data/, .venv) for the four .env key values verbatim and for Gemini `AIza`, Groq `gsk_`, OpenRouter `sk-or-`, AWS `AKIA`/`ASIA`, and `aws_secret_access_key`/`aws_session_token` patterns. No hits. No code logs an API key. `git check-ignore` confirms that .env (.gitignore:1), data/ (:4), .venv/ (:9), eval/golden/pages/ (:12) and eval/golden/mini.jsonl (:13) are ignored.
2. **Router compliance.** Outside app/router/, nothing in app/, spikes/, pipeline/, planet/ or scripts/ matches `google.genai`, `groq`, `cohere.`, `mistralai`, `boto3…bedrock`, `bedrock-runtime`, `litellm.completion` or raw provider hostnames. The only match is scripts/doctor.py:238, a provider-name comparison, which is an allowed exception.
3. **Privacy (config/models.yaml).** The PII chains are sql/tool_args/json_repair, table_read_pii, cell_reread and classify_text. They use only groq-qwen-vl, groq-gpt-oss, bedrock-ministral-8b/3b and local-qwen, and every one of those has `trains_on_free_tier: false`. Gemini and Cohere are absent from every PII chain. app/router/eligibility.py:43 also rejects PII traffic to training-tier models at runtime, whatever the chain says. The Bedrock allow-list (app/router/registry.py:125-130) contains Ministral 3 3B and 8B plus the two Titan embedding models. ministral-3-14b is absent, and registry.py:134 rejects unlisted Bedrock models. mistral-ocr stays commented out (D-018).
4. **Hard-coding.** app/ and pipeline/ contain none of the brief/demo queries (plan.md §117-126, proposal.md §108-111) as literals. The 904.40 / 911.395 ha anchors in pipeline/gis/ground_truth.py:18,30 are imported only by pipeline/gis/kg_check.py (a data-integrity check), which is acceptable. planet/spike.py:58 (PUB_233) holds published spike values that `make s2-spike` compares against. That is a reproduction script outside app/, so it is acceptable. app/router/embeddings.py:5 has "survey 233/2" in a docstring example only.
5. **Tests.** I re-ran `make test` offline: 406 passed, 4 deselected (`-m "not live"`), 11 s. Every skip or deselection states a reason: live/LIVE=1, docker not available, golden images not present, PostGIS not reachable, replay-only.
6. **Plan deliverables.**
   - data/doctor.json exists, with models, chains, aws_spend (ok, $0.03 of $15) and the Mistral opt-out status.
   - docs/spikes/ocr-bakeoff.md has measured recall, latency and capacity per engine on 20 golden pages.
   - D-010 is logged with thresholds.
   - `make s2-spike` target (Makefile:35) and planet/spike.py + spikes/s2_ndvi_spike.py exist. The reproduction is logged in docs/metrics.md.
   - The golden set is used only by the scorers (spikes/ocr_bakeoff/score.py, value_recall.py, fullpage_recall.py). prompts/ is empty, so there is no few-shot overlap.

## Non-blocking notes
- The spend cap is $15 (D-028, User). The phase-gate skill still says US$5 (D-004 superseded). Update the skill text or cross-reference D-028.
- data/doctor.json `mistral_training_optout` shows the env var `true` while `resolved_trains_on_free_tier` is null. This is harmless because mistral-ocr is disabled. Resolve it before any re-enable.
- eval/queries.yaml (the comparable/unseen query set the gate skill refers to) does not exist yet. It is needed by P4 at the latest.
- docs/metrics.md had "398 passed". I appended the 406 result.
- The repo has no commits yet. The ignore checks passed, but confirm with `git status --ignored` before the first commit.
