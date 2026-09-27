"""Lambda worker for deferred Bedrock batches (D-042, infra/bedrock_batch/SPEC.md).

Runtime: Python 3.12, stdlib + boto3 only (zip deploy, no extra layers).
Role: FAI-TCE-LambdaExecutionRole (existing; never create a new role).
Region: ap-south-1 only.

Two entry points via the same `handler`:
  * S3 ObjectCreated event on a "<part>.requests.jsonl" object -> process that part:
    for each request line, load any referenced images from S3, call Bedrock (Converse for
    `op=converse`, InvokeModel for `op=embed_text`/`op=embed_image`), write one line per request to
    "<part>.responses.jsonl" plus a "<part>.done" marker.
  * A direct test invocation `{"self_test": true}` -> writes+reads a small S3 object and makes one
    tiny Converse call, to prove the role can reach both S3 and Bedrock (feasibility check).

Privacy: never log prompt text, system text, image bytes or output_text. Only request `key`,
`task`, model id, ok/error kind, token counts and latency are logged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.parse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "ap-south-1")
BUCKET = os.environ.get("BUCKET_NAME", "fai-tce-team49-data")

# Approved model IDs only (Documents/FarmwiseAI_TCE_AWS_Access_Guide.pdf, D-027).
ALLOWED_CONVERSE_MODELS = {"mistral.ministral-3-3b-instruct", "mistral.ministral-3-8b-instruct"}
ALLOWED_EMBED_MODELS = {"amazon.titan-embed-text-v2:0", "amazon.titan-embed-image-v1"}

# bedrock-batch/<batch_id>/parts/<part>.requests.jsonl
REQUESTS_KEY_RE = re.compile(r"^bedrock-batch/(?P<batch_id>[^/]+)/parts/(?P<part>[^/]+)\.requests\.jsonl$")

RETRYABLE_ERROR_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelTimeoutException", "InternalServerException", "ModelNotReadyException",
}
MAX_ATTEMPTS = 5
BASE_BACKOFF_S = 1.0
TIME_SAFETY_MARGIN_MS = 20_000  # stop retrying/processing with this much time left on the clock

_s3 = None
_bedrock = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION, config=Config(retries={"max_attempts": 3}))
    return _s3


def _bedrock_client():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client(
            "bedrock-runtime", region_name=REGION,
            config=Config(read_timeout=120, connect_timeout=10, retries={"max_attempts": 1}),
        )
    return _bedrock


def _get_json_lines(bucket: str, key: str) -> list[dict]:
    body = _s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    lines = []
    for ln in body.decode("utf-8").splitlines():
        if ln.strip():
            try:
                lines.append(json.loads(ln))
            except ValueError:
                lines.append({"_bad_line": True})
    return lines


def _put_lines(bucket: str, key: str, rows: list[dict]) -> None:
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    _s3_client().put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")


def _put_marker(bucket: str, key: str) -> None:
    _s3_client().put_object(Bucket=bucket, Key=key, Body=b"{}", ContentType="application/json")


def _load_image_bytes(bucket: str, batch_id: str, s3_key: str) -> bytes:
    full_key = f"bedrock-batch/{batch_id}/{s3_key}"
    return _s3_client().get_object(Bucket=bucket, Key=full_key)["Body"].read()


def _error_kind(e: Exception) -> tuple[str, bool]:
    """(error string, is_retryable)"""
    code = ""
    if isinstance(e, ClientError):
        code = (e.response.get("Error") or {}).get("Code", "")
    name = code or type(e).__name__
    return name, code in RETRYABLE_ERROR_CODES


def _call_with_retry(fn, remaining_ms_fn, key: str):
    """Call fn() with exponential backoff on retryable Bedrock errors; stops early if the Lambda's
    remaining time is running low (so the response can still be flushed for the rest of the part)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(), None
        except Exception as e:  # noqa: BLE001
            kind, retryable = _error_kind(e)
            remaining_ms = remaining_ms_fn()
            if not retryable or attempt >= MAX_ATTEMPTS or remaining_ms < TIME_SAFETY_MARGIN_MS:
                logger.info("request key=%s failed kind=%s attempt=%d retryable=%s", key, kind, attempt, retryable)
                return None, kind
            sleep_s = min(BASE_BACKOFF_S * (2 ** (attempt - 1)), remaining_ms / 1000 - 1)
            sleep_s = max(sleep_s, 0.1)
            logger.info("request key=%s retrying kind=%s attempt=%d sleep=%.1fs", key, kind, attempt, sleep_s)
            time.sleep(sleep_s)


def _process_converse(bucket: str, batch_id: str, req: dict, remaining_ms_fn) -> dict:
    model_id = req.get("model_id", "")
    key = req.get("key", "")
    if model_id not in ALLOWED_CONVERSE_MODELS:
        return {"key": key, "ok": False, "output_text": None, "usage": {}, "latency_ms": 0,
                "error": f"model_not_allowed:{model_id}"}
    try:
        msgs = []
        for m in req.get("messages", []):
            blocks = []
            for b in m.get("content", []):
                if "image" in b:
                    img = b["image"]
                    data = _load_image_bytes(bucket, batch_id, img["s3_key"])
                    blocks.append({"image": {"format": img.get("format", "jpeg"), "source": {"bytes": data}}})
                elif "text" in b:
                    blocks.append({"text": b["text"]})
            msgs.append({"role": m.get("role", "user"), "content": blocks})
    except Exception as e:  # noqa: BLE001
        kind, _ = _error_kind(e)
        return {"key": key, "ok": False, "output_text": None, "usage": {}, "latency_ms": 0,
                "error": f"image_load_failed:{kind}"}

    system_text = req.get("system") or ""
    inference = req.get("inferenceConfig") or {"maxTokens": 1024, "temperature": 0}

    def _call():
        kwargs = {"modelId": model_id, "messages": msgs, "inferenceConfig": inference}
        if system_text:
            kwargs["system"] = [{"text": system_text}]
        t0 = time.monotonic()
        r = _bedrock_client().converse(**kwargs)
        return r, int((time.monotonic() - t0) * 1000)

    result, err = _call_with_retry(_call, remaining_ms_fn, key)
    if err is not None:
        return {"key": key, "ok": False, "output_text": None, "usage": {}, "latency_ms": 0, "error": err}
    r, latency_ms = result
    blocks = (r.get("output", {}).get("message", {}) or {}).get("content", [])
    text = "".join(b.get("text", "") for b in blocks if "text" in b)
    usage = r.get("usage") or {}
    return {"key": key, "ok": True, "output_text": text,
            "usage": {"inputTokens": int(usage.get("inputTokens", 0)), "outputTokens": int(usage.get("outputTokens", 0))},
            "latency_ms": int((r.get("metrics") or {}).get("latencyMs", latency_ms)), "error": None}


def _process_embed_text(req: dict, remaining_ms_fn) -> dict:
    model_id = req.get("model_id", "")
    key = req.get("key", "")
    if model_id not in ALLOWED_EMBED_MODELS:
        return {"key": key, "ok": False, "usage": {}, "latency_ms": 0, "error": f"model_not_allowed:{model_id}"}
    body = json.dumps({"inputText": req["inputText"], "dimensions": req.get("dimensions", 1024),
                        "normalize": req.get("normalize", True)})

    def _call():
        t0 = time.monotonic()
        r = _bedrock_client().invoke_model(modelId=model_id, contentType="application/json",
                                           accept="application/json", body=body)
        j = json.loads(r["body"].read())
        return j, int((time.monotonic() - t0) * 1000)

    result, err = _call_with_retry(_call, remaining_ms_fn, key)
    if err is not None:
        return {"key": key, "ok": False, "usage": {}, "latency_ms": 0, "error": err}
    j, latency_ms = result
    return {"key": key, "ok": True, "embedding": j["embedding"],
            "usage": {"inputTokens": int(j.get("inputTextTokenCount", 0)), "outputTokens": 0},
            "latency_ms": latency_ms, "error": None}


def _process_embed_image(bucket: str, batch_id: str, req: dict, remaining_ms_fn) -> dict:
    model_id = req.get("model_id", "")
    key = req.get("key", "")
    if model_id not in ALLOWED_EMBED_MODELS:
        return {"key": key, "ok": False, "usage": {}, "latency_ms": 0, "error": f"model_not_allowed:{model_id}"}
    try:
        png = _load_image_bytes(bucket, batch_id, req["image"]["s3_key"])
    except Exception as e:  # noqa: BLE001
        kind, _ = _error_kind(e)
        return {"key": key, "ok": False, "usage": {}, "latency_ms": 0, "error": f"image_load_failed:{kind}"}
    body = json.dumps({"inputImage": base64.b64encode(png).decode(),
                        "embeddingConfig": {"outputEmbeddingLength": req.get("dimensions", 1024)}})

    def _call():
        t0 = time.monotonic()
        r = _bedrock_client().invoke_model(modelId=model_id, contentType="application/json",
                                           accept="application/json", body=body)
        j = json.loads(r["body"].read())
        return j, int((time.monotonic() - t0) * 1000)

    result, err = _call_with_retry(_call, remaining_ms_fn, key)
    if err is not None:
        return {"key": key, "ok": False, "usage": {}, "latency_ms": 0, "error": err}
    j, latency_ms = result
    return {"key": key, "ok": True, "embedding": j["embedding"], "usage": {"inputTokens": 0, "outputTokens": 0},
            "latency_ms": latency_ms, "error": None}


def _process_part(bucket: str, batch_id: str, part: str, context) -> dict:
    remaining_ms_fn = context.get_remaining_time_in_millis if context is not None else (lambda: 600_000)
    requests_key = f"bedrock-batch/{batch_id}/parts/{part}.requests.jsonl"
    responses_key = f"bedrock-batch/{batch_id}/parts/{part}.responses.jsonl"
    done_key = f"bedrock-batch/{batch_id}/parts/{part}.done"

    reqs = _get_json_lines(bucket, requests_key)
    rows = []
    for req in reqs:
        if req.get("_bad_line"):
            continue
        if remaining_ms_fn() < TIME_SAFETY_MARGIN_MS:
            rows.append({"key": req.get("key"), "ok": False, "usage": {}, "latency_ms": 0,
                         "error": "lambda_time_budget_exceeded"})
            continue
        op = req.get("op", "converse")
        if op == "converse":
            rows.append(_process_converse(bucket, batch_id, req, remaining_ms_fn))
        elif op == "embed_text":
            rows.append(_process_embed_text(req, remaining_ms_fn))
        elif op == "embed_image":
            rows.append(_process_embed_image(bucket, batch_id, req, remaining_ms_fn))
        else:
            rows.append({"key": req.get("key"), "ok": False, "usage": {}, "latency_ms": 0,
                         "error": f"unknown_op:{op}"})
        r = rows[-1]
        logger.info("processed key=%s op=%s ok=%s error=%s tokens_in=%s tokens_out=%s latency_ms=%s",
                    r.get("key"), op, r.get("ok"), r.get("error"),
                    (r.get("usage") or {}).get("inputTokens"), (r.get("usage") or {}).get("outputTokens"),
                    r.get("latency_ms"))

    _put_lines(bucket, responses_key, rows)
    _put_marker(bucket, done_key)
    n_ok = sum(1 for r in rows if r.get("ok"))
    return {"batch_id": batch_id, "part": part, "requests": len(reqs), "ok": n_ok, "errors": len(rows) - n_ok}


def _self_test(context) -> dict:
    """Feasibility check: role can write+read S3 and call Bedrock Converse (no real batch data touched)."""
    test_key = "bedrock-batch/_selftest/probe.txt"
    payload = b"fai-tce-team49-bedrock-worker self-test"
    s3 = _s3_client()
    s3.put_object(Bucket=BUCKET, Key=test_key, Body=payload, ContentType="text/plain")
    read_back = s3.get_object(Bucket=BUCKET, Key=test_key)["Body"].read()
    s3.delete_object(Bucket=BUCKET, Key=test_key)
    s3_ok = read_back == payload

    model_id = "mistral.ministral-3-3b-instruct"
    t0 = time.monotonic()
    try:
        r = _bedrock_client().converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with one word: OK"}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0},
        )
    except Exception as e:  # noqa: BLE001 -- diagnostic only; self-test uses a fixed non-PII prompt
        return {"s3_ok": s3_ok, "bedrock_ok": False, "error": type(e).__name__, "error_detail": str(e)[:800]}
    latency_ms = int((time.monotonic() - t0) * 1000)
    blocks = (r.get("output", {}).get("message", {}) or {}).get("content", [])
    text = "".join(b.get("text", "") for b in blocks if "text" in b)
    usage = r.get("usage") or {}
    return {"s3_ok": s3_ok, "bedrock_ok": True, "model_id": model_id, "output_text": text,
            "usage": {"inputTokens": int(usage.get("inputTokens", 0)), "outputTokens": int(usage.get("outputTokens", 0))},
            "latency_ms": latency_ms}


def handler(event, context=None):
    if event.get("self_test"):
        result = _self_test(context)
        logger.info("self_test result: %s", {k: v for k, v in result.items() if k != "output_text"})
        return result

    results = []
    for record in event.get("Records", []):
        s3info = record.get("s3", {})
        bucket = s3info.get("bucket", {}).get("name", BUCKET)
        key = urllib.parse.unquote_plus(s3info.get("object", {}).get("key", ""))
        m = REQUESTS_KEY_RE.match(key)
        if not m:
            logger.info("skipping non-request key=%s", key)
            continue
        batch_id, part = m.group("batch_id"), m.group("part")
        logger.info("processing batch_id=%s part=%s", batch_id, part)
        results.append(_process_part(bucket, batch_id, part, context))
    return {"processed": results}
