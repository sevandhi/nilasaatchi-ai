"""Status of a Bedrock batch running in S3/Lambda (D-042).

    python -m infra.bedrock_batch.status <batch_id>

Lists parts uploaded vs parts done (worker marker) and, for done parts, tallies ok/error response
lines (a small GET per done part; batches are small so this stays cheap). Needs a live SSO session.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import BUCKET, DONE_SUFFIX, REQUESTS_SUFFIX, RESPONSES_SUFFIX, batch_prefix, s3_client


def _list_suffix(client, batch_id: str, suffix: str) -> set[str]:
    prefix = f"{batch_prefix(batch_id)}/parts/"
    out = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(suffix):
                base = key[len(prefix):-len(suffix)]
                out.add(base)
    return out


def batch_status(batch_id: str, client=None) -> dict:
    client = client if client is not None else s3_client()
    parts = _list_suffix(client, batch_id, REQUESTS_SUFFIX)
    done = _list_suffix(client, batch_id, DONE_SUFFIX)
    pending = sorted(parts - done)
    ok = errors = 0
    error_samples: list[str] = []
    for part in sorted(done):
        key = f"{batch_prefix(batch_id)}/parts/{part}{RESPONSES_SUFFIX}"
        body = client.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")
        for ln in body.splitlines():
            if not ln.strip():
                continue
            row = json.loads(ln)
            if row.get("ok"):
                ok += 1
            else:
                errors += 1
                if len(error_samples) < 10:
                    error_samples.append(f"{row.get('key')}: {row.get('error')}")
    return {"batch_id": batch_id, "parts_total": len(parts), "parts_done": len(done),
            "parts_pending": pending, "responses_ok": ok, "responses_error": errors,
            "error_samples": error_samples}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("batch_id")
    args = ap.parse_args(argv)
    s = batch_status(args.batch_id)
    print(json.dumps(s, indent=1))
    return 0 if not s["parts_pending"] else 1


if __name__ == "__main__":
    sys.exit(main())
