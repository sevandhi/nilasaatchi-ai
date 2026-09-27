"""`GET /health/bedrock` — proves the Lambda execution role (`FAI-TCE-LambdaExecutionRole`) can
reach Bedrock in `ap-south-1` (D-067 notes FarmwiseAI enabled this after the D-042 feasibility
check found it denied — see `infra/RESOURCES.md`). Calls Converse with a 5-token prompt on
`mistral.ministral-3-3b-instruct` and caches the result for `BEDROCK_HEALTH_CACHE_SECONDS`
(default 600s) so repeated demo page-loads don't re-spend the $15 cap.
"""
from __future__ import annotations

import time
from typing import Any

from app.cloud.config import get_cloud_settings

_cache: dict[str, Any] = {"ts": 0.0, "result": None}


def _call_bedrock(model_id: str, region: str) -> dict[str, Any]:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=region)
    t0 = time.monotonic()
    try:
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Say OK."}]}],
            inferenceConfig={"maxTokens": 5},
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        text = ""
        try:
            text = resp["output"]["message"]["content"][0]["text"]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "model": model_id, "latency_ms": latency_ms, "error": None, "reply": text}
    except Exception as e:  # noqa: BLE001 — report, never raise (this is a health probe)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"ok": False, "model": model_id, "latency_ms": latency_ms, "error": str(e)}


def bedrock_health(force: bool = False) -> dict[str, Any]:
    settings = get_cloud_settings()
    now = time.monotonic()
    if not force and _cache["result"] is not None and (now - _cache["ts"]) < settings.bedrock_health_cache_seconds:
        cached = dict(_cache["result"])
        cached["cached"] = True
        cached["cache_age_s"] = round(now - _cache["ts"], 1)
        return cached
    result = _call_bedrock(settings.bedrock_model_id, settings.aws_region)
    _cache["ts"] = now
    _cache["result"] = result
    out = dict(result)
    out["cached"] = False
    out["cache_age_s"] = 0.0
    return out
