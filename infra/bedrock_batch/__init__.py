"""Deferred Bedrock batch infra (D-042, SPEC.md): S3 layout + upload/status/download CLIs.

S3 layout under s3://fai-tce-team49-data/ (private bucket, ap-south-1):
    bedrock-batch/<batch_id>/img/<sha256>.jpg
    bedrock-batch/<batch_id>/parts/<part>.requests.jsonl    <- uploaded; triggers the worker Lambda
    bedrock-batch/<batch_id>/parts/<part>.responses.jsonl   <- written by the worker
    bedrock-batch/<batch_id>/parts/<part>.done              <- empty marker written by the worker

The S3 -> Lambda notification filters on prefix "bedrock-batch/" + suffix ".requests.jsonl" so the
worker's own writes (.responses.jsonl / .done) never re-trigger it.

Local (router) layout (app/router/batch.py, unrelated to this package other than shared batch_id):
    data/bedrock_batch/<batch_id>/requests.jsonl   (single file, appended to by the router)
    data/bedrock_batch/<batch_id>/img/<sha256>.jpg
    data/bedrock_batch/<batch_id>/responses/<part>.jsonl   (expected by app.router.batch_import)
"""
from __future__ import annotations

BUCKET = "fai-tce-team49-data"
REGION = "ap-south-1"
LAMBDA_NAME = "fai-tce-team49-bedrock-worker"
MAX_LINES_PER_PART = 50
REQUESTS_SUFFIX = ".requests.jsonl"
RESPONSES_SUFFIX = ".responses.jsonl"
DONE_SUFFIX = ".done"


def batch_prefix(batch_id: str) -> str:
    return f"bedrock-batch/{batch_id}"


def img_key(batch_id: str, s3_key: str) -> str:
    """s3_key is the relative key from a request line, e.g. 'img/<sha256>.jpg'."""
    return f"{batch_prefix(batch_id)}/{s3_key}"


def requests_key(batch_id: str, part: str) -> str:
    return f"{batch_prefix(batch_id)}/parts/{part}{REQUESTS_SUFFIX}"


def responses_key(batch_id: str, part: str) -> str:
    return f"{batch_prefix(batch_id)}/parts/{part}{RESPONSES_SUFFIX}"


def done_key(batch_id: str, part: str) -> str:
    return f"{batch_prefix(batch_id)}/parts/{part}{DONE_SUFFIX}"


def s3_client():
    import os

    import boto3

    profile = os.environ.get("AWS_PROFILE") or "fai-builder"
    return boto3.Session(profile_name=profile, region_name=REGION).client("s3")


def local_batch_dir(batch_id: str):
    """data/bedrock_batch/<batch_id> — matches app.router.core.data_dir() (env ROUTER_DATA_DIR or REPO_ROOT/data)."""
    import os
    from pathlib import Path

    root = Path(os.environ.get("ROUTER_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data"))
    return root / "bedrock_batch" / batch_id
