---
name: free-model-router
description: The free-only, privacy-tiered, quota-aware model registry and routing policy for NilaSaatchi AI — model roles, fallback chains, privacy tiers, pseudonymisation gateway, quota/circuit-breaker rules, shadow-cost telemetry and how to add or verify a model. Load before any code that calls an LLM/VLM/OCR API, edits config/models.yaml, or designs prompts that will run on hosted models.
---

# Free Model Router

## Hard rules
1. **Free only.** Every model must have `cost_class: free | local | capped_fallback` in `config/models.yaml`. `capped_fallback` means Bedrock via the FarmwiseAI event account (team 49, profile `fai-builder`, ap-south-1), under the **$15 self-imposed cap** (D-028, `make aws-cost`). Anything else needs an escalation to the user. Approved Bedrock models only: `mistral.ministral-3-3b-instruct`, `mistral.ministral-3-8b-instruct` (both text+image), `amazon.titan-embed-text-v2:0`, `amazon.titan-embed-image-v1`. Ministral 3 14B is visible but **not approved**.
2. **All calls go through `app.router.call(task, payload, schema)`.** Direct SDK calls outside `app/router/providers/` are a review failure.
3. **Privacy tiers** (decision D-005):
   - `PII`: owner names, patta numbers, per-owner amounts, handwritten Form E, and raw crops of award, Form F or notice tables. **Only** models with `trains_on_free_tier: false` may receive it: Groq, Mistral with the opt-out verified, local, Bedrock.
   - `PSEUDO`: text that has passed through the pseudonymisation gateway (`⟨OWNER_n⟩`, `⟨PATTA_n⟩`, `⟨AMT_n⟩`). Any model may receive it.
   - `PUBLIC`: the GO/AS/LPS, gazette schedules, GIS data, **satellite imagery, chips and time-series plots**, schema, the user's question. Any model may receive it.
4. **Critic independence.** The adversarial critic's `vendor` must differ from the vendor that produced the claim.
5. **Determinism.** No random routing. Given the same state, registry and quota snapshot, the router makes the same choice.

## Registry seed (`config/models.yaml`)
These are the verified-by-research values from 2026-09-26. **`make doctor` must re-probe them**, and its results override these.

```yaml
models:
  - id: gemini-flash-lite        # role: planner, judge, presenter, critic (PSEUDO/PUBLIC)
    provider: gemini
    model: gemini-3.5-flash-lite
    fallback_model: gemini-3.1-flash-lite
    vendor: google
    weights: proprietary
    modalities: [text, image, pdf]
    json_schema: true
    tools: true
    trains_on_free_tier: true
    cost_class: free
    limits: {rpd: 500, rpm: 15}                 # disputed; verify in AI Studio dashboard
    list_price_per_mtok: {in: 0.10, out: 0.40}  # for shadow cost; update from pricing page
  - id: gemini-flash              # hard replans only
    provider: gemini
    model: gemini-3.8-flash
    vendor: google
    weights: proprietary
    modalities: [text, image, pdf]
    json_schema: true
    trains_on_free_tier: true
    cost_class: free
    limits: {rpd: 20}
  # DISABLED (D-018) — kept for reference only
  - id: mistral-ocr               # formerly: PII table second reader
    provider: mistral
    model: mistral-ocr-latest
    vendor: mistral
    weights: proprietary
    modalities: [pdf, image]
    output: markdown_html_tables_bboxes
    trains_on_free_tier: false_if_opted_out     # P0 must confirm opt-out, else treat as true
    cost_class: free
    limits: {monthly_credit_usd: 10, usd_per_1k_pages: 4}
  - id: groq-qwen-vl              # text-to-SQL, JSON, cell re-read
    provider: groq
    model: qwen/qwen3.8-27b
    vendor: alibaba-open
    weights: open
    modalities: [text, image]
    json_schema: strict
    tools: true
    trains_on_free_tier: false
    cost_class: free
    limits: {rpm: 30, rpd: 1000, tpm: 8000, tpd: 200000}
  - id: groq-gpt-oss
    provider: groq
    model: openai/gpt-oss-120b
    vendor: openai-open
    weights: open
    modalities: [text]
    json_schema: strict
    trains_on_free_tier: false
    cost_class: free
    limits: {rpd: 1000, tpd: 200000}
  - id: cohere-command-a          # judge/critic fallback
    provider: cohere
    model: command-a
    vendor: cohere
    weights: proprietary
    modalities: [text]
    trains_on_free_tier: unknown                # treat as true → PSEUDO only
    cost_class: free
    limits: {monthly_calls: 1000, rpm: 20}
  - id: local-qwen
    provider: ollama
    model: qwen3.5:4b
    vendor: local
    weights: open
    modalities: [text]
    trains_on_free_tier: false
    cost_class: local
  - id: bedrock-ministral
    provider: bedrock
    model: ministral-8b-instruct
    region: ap-south-1
    vendor: mistral-open
    weights: open
    modalities: [text]
    trains_on_free_tier: false
    cost_class: capped_fallback
    cap_usd: 5
engines:
  - {id: paddle-ta, kind: ocr, lang: ta, local: true}
  - {id: tesseract, kind: ocr, lang: tam+eng, local: true}
  - {id: bge-m3, kind: embedding, local: true}
```

**Unknown training policy means `true`.** The PII filter must fail closed.

## Task → chain
| Task | Chain (first eligible wins) |
|---|---|
| `plan` | gemini-flash-lite → gemini-flash (only if a replan is flagged hard) → groq-qwen-vl → local-qwen |
| `sql` / `tool_args` / `json_repair` | groq-qwen-vl → groq-gpt-oss → bedrock-ministral → local-qwen |
| `table_read_pii` | local OCR for headers → **bedrock-ministral-8b (vision, PII-OK)** → groq-qwen-vl (crop) → review queue (D-027). Mistral disabled (D-018) |
| `cell_reread` | bedrock-ministral-8b → groq-qwen-vl → review queue |
| `critic` | a vendor different from the producer, chosen from gemini-flash-lite (PSEUDO), groq-qwen-vl, cohere-command-a |
| `judge` | gemini-flash-lite (PSEUDO) → cohere-command-a → deterministic-only (DOWNGRADE) |
| `present` | gemini-flash-lite (PSEUDO) → groq-qwen-vl |
| `satellite_teacher` / `satellite_second_opinion` | gemini-flash-lite (PUBLIC: chips and plots only, no owner data) → groq-qwen-vl → field-verification queue |
| `classify_text` | bedrock-ministral-3b → groq-gpt-oss → local-qwen |

## Pseudonymisation gateway (`app/router/gateway.py`)
- **Detect** PII by schema field (for example `owner`, `patta_no`, `amount`), plus a regex/NER pass over free text: Tamil relation markers (மகன், மனைவி, க/பெ, த/பெ), `S/o`, `W/o`, `Thiru.`, `Tmt.`, and rupee patterns.
- **Replace** each value with a stable token per run. Store the reverse map only in `RunState.private`, which is never serialised to the ledger payloads. The ledger stores hashes instead.
- **Re-insert** the real values locally after the response.
- **Unit test:** a fixture page must yield 0 residual Tamil names above a length of 3 characters in its PSEUDO output.

## Quotas and failures
- **Quota DB:** `data/quota.sqlite`. Before each call, reserve the estimated tokens; reconcile them after. Keep a 10% daily reserve per model for the demo.
- **Circuit breaker:** after 3 consecutive 429s or 5xxs, open for 60 s, then allow one half-open probe.
- **Timeouts:** 20 s for text, 60 s for OCR. Retry once with jitter before falling back.
- **Chaos hook:** `ROUTER_CHAOS=gemini:down,groq:slow` (or a UI toggle) forces failures for the demo and tests.

## Telemetry per call
Log `{step_id, task, candidates[], filtered[{id, reason}], scores{}, choice, attempt, latency_ms, tokens_in/out, shadow_cost_usd, actual_cost_usd, privacy_tier, outcome}`. Shadow cost = the tokens used × `list_price_per_mtok`. Actual cost is 0, except for the Mistral credit and the Bedrock cap, which are both tracked.

## Adding or changing a model
1. Add its registry entry with honest flags.
2. Run `make doctor MODEL=<id>`.
3. Record its fixtures.
4. Run `make eval-agent`.
5. Log a `D-###` entry.

Changing a PII-eligible flag requires the user's approval.
