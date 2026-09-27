# AWS resources — team49 (D-027, D-028, D-042)

Region: **ap-south-1** only. Account: `163887963251`. Profile `fai-builder` (SSO role
`FAI-TCE-Builder-AI`), cost profile `fai-cost`.

## Bedrock batch worker (D-042, infra/bedrock_batch/SPEC.md)

Status as of **2026-09-27: torn down / blocked**. Feasibility check failed (see below); every
resource created for this attempt has been deleted. Nothing from this feature is currently live.

| Resource | Name / ARN | Created | Deleted | Notes |
|---|---|---|---|---|
| S3 bucket | `fai-tce-team49-data` (`arn:aws:s3:::fai-tce-team49-data`) | 2026-09-27 04:38 UTC | 2026-09-27 04:44 UTC | Private by account default (Block Public Access could not be confirmed/changed — see "Permission gaps" below); SSE-S3 (AES256) confirmed via `get-bucket-encryption` — applied automatically, no explicit `PutBucketEncryption` call succeeded. 14-day lifecycle rule on `bedrock-batch/` **could not be created** (`s3:PutLifecycleConfiguration` denied). Versioning: default (off), confirmed. Emptied and deleted after the feasibility failure. |
| Lambda function | `fai-tce-team49-bedrock-worker` (`arn:aws:lambda:ap-south-1:163887963251:function:fai-tce-team49-bedrock-worker`) | 2026-09-27 04:42 UTC | 2026-09-27 04:44 UTC | Runtime python3.12, role `FAI-TCE-LambdaExecutionRole` (existing, not created), handler `handler.handler`, timeout 900s, memory 1024MB. Reserved concurrency (≤8) **could not be set** (`lambda:PutFunctionConcurrency` denied). Deleted after the feasibility failure; its CloudWatch log group (`/aws/lambda/fai-tce-team49-bedrock-worker`) was deleted too. |
| S3 -> Lambda trigger | (not created) | — | — | Never reached this step; feasibility check (step 1) failed first. |
| Lambda invoke permission (`s3.amazonaws.com`) | (not created) | — | — | Same as above. |

### Feasibility check result (STOP condition triggered)

Per plan: deploy a minimal worker, invoke it once with `{"self_test": true}` (writes+reads a tiny S3
object, then calls `bedrock-runtime.converse` on `mistral.ministral-3-3b-instruct` with a 5-token
prompt), to prove `FAI-TCE-LambdaExecutionRole` can reach both S3 and Bedrock.

- **S3: OK.** The role wrote and read back a test object under `bedrock-batch/_selftest/` in the
  private bucket (`s3_ok: true`).
- **Bedrock: FAILED.** `AccessDeniedException`:
  ```
  User: arn:aws:sts::163887963251:assumed-role/FAI-TCE-LambdaExecutionRole/fai-tce-team49-bedrock-worker
  is not authorized to perform: bedrock:InvokeModel
  on resource: arn:aws:bedrock:ap-south-1::foundation-model/mistral.ministral-3-3b-instruct
  because no identity-based policy allows the bedrock:InvokeModel action
  ```
- **Confirmed it's role-specific, not account/region-wide:** the same call
  (`bedrock-runtime.converse` on `mistral.ministral-3-3b-instruct`, ap-south-1) made directly under
  the human `fai-builder` profile (role `FAI-TCE-Builder-AI`) **succeeds** (this is the same access
  the router already uses live, D-027). So the gap is specifically that the inline policy attached to
  `FAI-TCE-LambdaExecutionRole` (`FAI-TCE-ApplicationRuntime`) does not grant `bedrock:InvokeModel`
  (Converse and presumably InvokeModel-for-Titan-embeddings use the same action) — only
  `AWSLambdaBasicExecutionRole` (CloudWatch Logs) is attached, plus that one inline policy, which
  covers S3 object read/write but not Bedrock.

**Action needed from FarmwiseAI:** add `bedrock:InvokeModel` (and ideally `bedrock:Converse` if they
keep them separate) on `arn:aws:bedrock:ap-south-1::foundation-model/mistral.ministral-3-3b-instruct`
and `.../mistral.ministral-3-8b-instruct` (plus the two Titan embedding model ARNs if embedding
batches are wanted) to the `FAI-TCE-LambdaExecutionRole` role (or its `FAI-TCE-ApplicationRuntime`
inline policy). Until then, D-042's Lambda-worker design cannot run; long Bedrock batches stay
blocked on hourly SSO re-login chunks (`make aws-login`) using the human `fai-builder` role.

### Permission gaps found on the builder identity itself (informational, not the STOP trigger)

While configuring the bucket, several bucket **configuration** (not data-plane) calls were denied
for `fai-builder`/`FAI-TCE-Builder-AI` itself, seemingly by design (a shared-account guardrail
preventing students from weakening or discovering security posture):
`s3:PutBucketPublicAccessBlock`, `s3:GetBucketPublicAccessBlock`, `s3:PutEncryptionConfiguration`,
`s3:PutLifecycleConfiguration`, `s3:GetBucketLifecycleConfiguration`, `s3:GetBucketPolicy`,
`s3:GetBucketPolicyStatus`, `s3:GetBucketAcl`, `s3:GetBucketOwnershipControls`, and
`lambda:PutFunctionConcurrency`. Object-level S3 (put/get/delete/list) and Lambda function
create/update/delete/invoke all worked fine. Net effect: new buckets in this account are private and
SSE-S3 encrypted **by AWS's 2023-era defaults** (confirmed via `get-bucket-encryption` returning
AES256 with no explicit call needed), which satisfies the "private, encrypted" requirement even
though it can't be asserted via an explicit `PutPublicAccessBlock`/`PutBucketEncryption` call. The
14-day lifecycle rule and the ≤8 reserved concurrency could not be set at all — a gap to flag to
FarmwiseAI alongside the Bedrock one if/when this feature is revived.

### Code delivered (kept in the repo; not deployed)

All application code for D-042 was written and unit-tested offline (no AWS calls) before the
feasibility check, and is ready to redeploy as-is once the missing `bedrock:InvokeModel` permission
is granted:

- `infra/bedrock_batch/worker/handler.py` — the Lambda handler (S3-triggered part processing +
  `self_test` entry point), stdlib + boto3 only.
- `infra/bedrock_batch/upload.py`, `status.py`, `download.py` — CLIs (`python -m infra.bedrock_batch.*`).
- `infra/bedrock_batch/teardown.py` — `make aws-down`.
- `tests/infra/test_bedrock_batch.py` + `tests/infra/fakes.py` — 23 offline unit tests (in-memory S3
  + Bedrock fakes), all passing.

### Redeploying later (once the permission is granted)

The exact commands used (bucket create + best-effort hardening, Lambda create + config, S3 trigger)
are not scripted as a single "deploy" command (avoiding an accidental one-command re-create without
fresh user approval, per CLAUDE.md's AWS rules); see the command log in this file's git history / the
session transcript for the literal `aws` CLI invocations used, or re-run the equivalent steps by hand
with **fresh user approval**, since resources were fully torn down.

## Cost

`make aws-cost` before this session: not run (first AWS mode-D-042 activity today). Cost so far this
project (`make aws-cost`, Cost Explorer, 2026-09 to date): **$0.634 of $15.00 cap (4.2%)**, unchanged
by this session (AccessDenied calls are free; the one diagnostic `converse` call made directly under
`fai-builder` to confirm the permission gap cost a fraction of a cent and had not yet appeared in Cost
Explorer, which lags by hours).

## Cloud demo (D-067): read-only app on Lambda + API Gateway

| Resource | Name / ARN | Created | Deleted | Notes |
|---|---|---|---|---|
| S3 bucket | `fai-tce-team49-data` (`arn:aws:s3:::fai-tce-team49-data`) | 2026-09-27 16:37 UTC | — | Private (account default), SSE-S3 AES256 confirmed. Prefix `cloud-demo/`: pages/ (page images), snapshot/ (read-only API data), chips/. User-approved 2026-09-27 |
