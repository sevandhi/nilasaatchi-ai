"""Download a completed Bedrock batch's responses from S3 (D-042, step 4 of SPEC.md).

    python -m infra.bedrock_batch.download <batch_id>

Pulls every "<part>.responses.jsonl" object into data/bedrock_batch/<batch_id>/responses/<part>.jsonl
(the layout app.router.batch_import.import_batch expects). Needs a live SSO session.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import BUCKET, RESPONSES_SUFFIX, batch_prefix, local_batch_dir, s3_client


def download_batch(batch_id: str, client=None) -> dict:
    client = client if client is not None else s3_client()
    prefix = f"{batch_prefix(batch_id)}/parts/"
    out_dir = local_batch_dir(batch_id) / "responses"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(RESPONSES_SUFFIX):
                continue
            part = key[len(prefix):-len(RESPONSES_SUFFIX)]
            client.download_file(BUCKET, key, str(out_dir / f"{part}.jsonl"))
            n += 1
    return {"batch_id": batch_id, "responses_downloaded": n, "dir": str(out_dir)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("batch_id")
    args = ap.parse_args(argv)
    print(json.dumps(download_batch(args.batch_id), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
