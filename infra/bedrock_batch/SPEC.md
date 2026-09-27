# Deferred Bedrock batch via Lambda (D-042)

**Why:** AWS SSO sessions last ~1 h with no refresh, so multi-hour Bedrock batches can't run on local credentials. A Lambda running as `FAI-TCE-LambdaExecutionRole` calls Bedrock with role credentials that don't expire mid-batch.

## Flow
1. **Collect (local, no AWS):** the router runs in `ROUTER_BEDROCK_MODE=collect`. Every Bedrock call that would be made is written as a request line to `data/bedrock_batch/<batch_id>/requests.jsonl`, and its image bytes go to `data/bedrock_batch/<batch_id>/img/<sha256>.jpg`. The call returns a deferred result (`ok=false, error_kind="deferred"`), so pipelines can mark pages "pending batch".
2. **Upload (≈10 min, needs SSO):** `python -m infra.bedrock_batch.upload <batch_id>` → `s3://fai-tce-team49-data/bedrock-batch/<batch_id>/requests/<part>.jsonl` (≤ 50 requests per part) + `img/`. Each requests part object triggers the worker Lambda (S3 ObjectCreated notification on the prefix `bedrock-batch/*/requests/`).
3. **Worker (Lambda `fai-tce-team49-bedrock-worker`, Python 3.12, boto3 only):** for each request line, it loads the images from S3 and calls `bedrock-runtime.converse(modelId, messages, inferenceConfig)`. It writes `…/responses/<part>.jsonl`, one line per request (`key, ok, output_text, usage{inputTokens, outputTokens}, latency_ms, error`), plus a `…/done/<part>` marker. Approved model IDs only (allow-list); ap-south-1 only; reserved concurrency ≤ 8 (account limit 10); timeout 900 s; memory 1024 MB.
4. **Download + import (needs SSO, a few min):** `python -m infra.bedrock_batch.download <batch_id>` pulls the responses; then `python -m app.router.batch_import <batch_id>` writes them into the router's response cache/fixture store keyed by the same request hash, and into router_log with the actual cost.
5. **Re-run (local):** the pipeline re-runs with `ROUTER_BEDROCK_MODE=cache_only`. Every Bedrock call is served from the imported cache; a miss is collected into the next batch (e.g. second reads).

## Request line (JSON)
```
{"key": "<sha256 of canonical request: model_id|messages(text + image sha256s)|inferenceConfig|schema>",
 "batch_id": "...", "task": "table_read_pii", "model_id": "mistral.ministral-3-8b-instruct",
 "system": "...", "messages": [{"role": "user", "content": [{"text": "..."}, {"image": {"format": "jpeg", "s3_key": "img/<sha256>.jpg"}}]}],
 "inferenceConfig": {"maxTokens": 4096, "temperature": 0}, "privacy_tier": "PII", "created_at": "..."}
```

## Rules
- The spend guard applies at collect time: the estimated cost of a batch must fit under 80% of `AWS_SPEND_CAP_USD` minus spend to date.
- The bucket is private (Block Public Access on), SSE-S3, with a lifecycle rule deleting `bedrock-batch/` objects after 14 days. Owner-bearing images are never kept longer.
- `make aws-cost` runs before upload and after download.
- Every created resource is named `fai-tce-team49-*` and recorded in `infra/RESOURCES.md`, with a teardown script `make aws-down`.
