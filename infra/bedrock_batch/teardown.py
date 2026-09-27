"""Teardown for the D-042 Bedrock batch infra (`make aws-down`).

Deletes, in order: the S3 -> Lambda notification, the Lambda function
(fai-tce-team49-bedrock-worker), the lambda:InvokeFunction permission (implied by function deletion),
and every object under s3://fai-tce-team49-data/bedrock-batch/ (the batch working area). The bucket
itself is left in place (it may be reused by other jobs) unless --delete-bucket is passed.

Never run automatically; the user must invoke this explicitly:
    uv run python -m infra.bedrock_batch.teardown [--delete-bucket]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import BUCKET, LAMBDA_NAME, REGION


def _lambda_client():
    import os

    import boto3

    profile = os.environ.get("AWS_PROFILE") or "fai-builder"
    return boto3.Session(profile_name=profile, region_name=REGION).client("lambda")


def _s3_client():
    from . import s3_client
    return s3_client()


def remove_s3_trigger(lam) -> str:
    try:
        lam.get_function(FunctionName=LAMBDA_NAME)
    except lam.exceptions.ResourceNotFoundException:
        return "lambda already absent, nothing to detrigger"
    s3 = _s3_client()
    s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={})
    return "cleared bucket notification configuration"


def delete_lambda(lam) -> str:
    try:
        lam.delete_function(FunctionName=LAMBDA_NAME)
        return f"deleted lambda {LAMBDA_NAME}"
    except lam.exceptions.ResourceNotFoundException:
        return "lambda already absent"


def delete_batch_objects(delete_bucket: bool) -> str:
    s3 = _s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix="bedrock-batch/"):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objs:
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objs})
            n += len(objs)
    msg = f"deleted {n} objects under bedrock-batch/"
    if delete_bucket:
        # Bucket must be fully empty (not just bedrock-batch/) before this succeeds.
        remaining = s3.list_objects_v2(Bucket=BUCKET, MaxKeys=1).get("KeyCount", 0)
        if remaining:
            msg += f"; bucket {BUCKET} not empty, not deleted"
        else:
            s3.delete_bucket(Bucket=BUCKET)
            msg += f"; deleted empty bucket {BUCKET}"
    return msg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--delete-bucket", action="store_true",
                    help="also delete the bucket itself (only if empty after cleanup)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args(argv)

    if not args.yes:
        resp = input(f"This will delete Lambda {LAMBDA_NAME!r} and all objects under "
                     f"s3://{BUCKET}/bedrock-batch/ in {REGION}. Type 'yes' to continue: ")
        if resp.strip().lower() != "yes":
            print("aborted")
            return 1

    lam = _lambda_client()
    steps = [remove_s3_trigger(lam), delete_lambda(lam), delete_batch_objects(args.delete_bucket)]
    print(json.dumps({"steps": steps}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
