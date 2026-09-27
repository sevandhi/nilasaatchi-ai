# 04 · Model Router, Privacy and AWS

## 1. Why a router?
Task 1 asks for multiple models, each with a justified role, chosen by capability, confidence, latency and cost, with fallback. We never call an AI SDK directly. Everything goes through `app.router.call(task, payload, schema=…, privacy_tier=…, images=…)`, which picks the model, logs why, validates the JSON, and falls back when something fails.

## 2. The models and their roles
| Model | Type | Role | Why this one |
|---|---|---|---|
| **Gemini 3.5 / 3.1 flash-lite** (free tier) | Proprietary #1 | Planner, judge, narrative; **satellite chip teacher** | Strong reasoning and vision. The free tier trains on inputs, so it only gets **non-personal or pseudonymised** data (satellite chips are public) |
| **Cohere Command A + Command A Vision** (trial) | Proprietary #2 | Adversarial critic, judge fallback, second vision opinion | A different vendor, so the critic's mistakes differ from the producer's |
| **AWS Bedrock Ministral 3 8B** (vision) | Open-weight, hosted on AWS | **Main reader of document tables** (owner pages) | Won the OCR bake-off; AWS doesn't train on customer data, so it is safe for owner names |
| **Bedrock Ministral 3 3B** | Open-weight | Document classification | Cheap and fast |
| **Groq Qwen3.8-27B / gpt-oss-120b** (free) | Open-weight | Text-to-SQL, JSON repair, vision fallback | Fast, strict JSON, no training on inputs |
| **Titan Text / Multimodal Embeddings** (Bedrock) | Proprietary (Amazon) | Search embeddings | Inside FarmwiseAI's approved stack |
| **Tesseract** (local) | Open-source | Headers and prose OCR | Free, private, unlimited |
| **LightGBM student** (ours) | Small local model | Classifies every parcel-season's land use | Thousands of predictions for free |

**Dropped along the way (and why):**
- **Mistral OCR:** its API needs a billing method even on the free plan, which breaks the free-only rule (D-018).
- **GitHub Models:** retired in July 2026.
- **Cerebras:** requires a card.

## 3. How a routing decision is made
1. **Hard filters:**
   - the required modality (text/image);
   - the **privacy tier**:
     - `PII` (owner data) → only models with `trains_on_free_tier: false`;
     - `PSEUDO` (names replaced by tokens) or `PUBLIC` → any model;
   - circuit breaker (after 3 consecutive 429/5xx errors, skip the model for 60 s);
   - daily quota reserve;
   - the AWS spend guard.
2. **Score:** capability + expected confidence − latency − cost − quota pressure. It is deterministic, and every candidate and its score is logged. Some tasks use **strict order** (e.g. satellite second opinion: Cohere first).
3. **Call → validate JSON against the schema → repair once → fall back to the next model**, or end at a terminal step (review queue / clarify / deterministic-only).
4. **Telemetry:** latency, tokens, **shadow cost** (what it would cost at list prices), actual cost, privacy tier, outcome → `data/router_log.sqlite`.

**Fails closed:** if a provider's training policy is unknown, it is treated as "trains", so it never gets PII.

## 4. Pseudonymisation gateway
Before any content goes to a training-tier model, owner names, patta numbers and amounts are replaced with tokens (`⟨OWNER_17⟩`). The answer is re-filled locally. The database also has a view (`v_owner_pseudo`) that gives the agent tokens instead of names.

## 5. `make doctor`
It probes every model live (one tiny call each) and records status, latency, the resolved model ID and rate-limit headers. For vision models it **proves they can see images**: it names the colour of a solid 256-px image, and checks that token counts rise when an image is attached. This caught two real problems:
- Cohere's image tokens were hidden (the model *could* see; our accounting was wrong).
- The 64-px probe was too small for Ministral, which gave a false failure.

## 6. AWS (Team 49, FAI-TCE-Builder-AI, Mumbai ap-south-1)
- **Approved services:** S3, Lambda, API Gateway, DynamoDB, CloudWatch, and Bedrock (Ministral 3B/8B, Titan). Textract is not approved for our category, and doesn't read Tamil anyway. No EC2/RDS.
- **Login:** SSO device code (the user approves in a browser; we never handle passwords). A session lasts **2 hours** (confirmed by FarmwiseAI).
- **Spend control:** a self-imposed **US$15 cap** of the US$100 budget.
  - `make aws-cost` reads Cost Explorer.
  - The router adds a local estimate, and stops Bedrock at 80%.
  - **Spend so far: about US$7.9** (mostly Stage B, ~US$0.001/page).
- **Lambda batch worker** (D-042): to run multi-hour Bedrock jobs without a human login, requests are collected locally, uploaded to private S3, processed by an S3-triggered Lambda, then imported back.
  - The code is complete and tested (23 tests).
  - The Lambda role currently **lacks Bedrock permission**; FarmwiseAI is reviewing it.
  - Until then, bulk jobs run in ≤ 2-hour chunks. The router already supports `ROUTER_BEDROCK_MODE=collect|cache_only`.

## 7. Likely review questions
- *"Isn't Ministral open-weight, not proprietary?"* Yes. Our proprietary models are Gemini and Cohere (plus Titan embeddings); Ministral is our main open-weight model, with Qwen and gpt-oss as fallbacks.
- *"How do you know every model has a real role?"* The router log shows which task each model served. The roles differ: planning/judging, critique, table reading, SQL, classification and satellite teaching.
- *"What if Gemini is down?"* The chain falls back (Groq/Cohere), and the chaos hook (`ROUTER_CHAOS=gemini:down`) demonstrates it.
