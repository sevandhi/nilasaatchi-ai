"""Upload a collected Bedrock batch to S3 (D-042, step 2 of SPEC.md).

    python -m infra.bedrock_batch.upload <batch_id>

Reads data/bedrock_batch/<batch_id>/requests.jsonl (written by the router in
ROUTER_BEDROCK_MODE=collect) and img/<sha256>.jpg, splits requests into parts of <= 50 lines,
uploads img/ first (so the worker never races ahead of an image it needs), then uploads each
requests part LAST (that PutObject triggers the worker Lambda via the S3 notification on suffix
".requests.jsonl"). Needs a live SSO session (profile fai-builder).
"""
from __future__ import annotations

import argparse
import json
import sys

from . import BUCKET, MAX_LINES_PER_PART, img_key, local_batch_dir, requests_key, s3_client


def _read_requests(batch_id: str) -> list[str]:
    f = local_batch_dir(batch_id) / "requests.jsonl"
    if not f.exists():
        raise SystemExit(f"no local batch dir: {f}")
    return [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _part_name(i: int) -> str:
    return f"part-{i:04d}"


def _existing_keys(client, prefix: str) -> set[str]:
    keys = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def upload_batch(batch_id: str, dry_run: bool = False, client=None) -> dict:
    bdir = local_batch_dir(batch_id)
    lines = _read_requests(batch_id)
    parts = [lines[i:i + MAX_LINES_PER_PART] for i in range(0, len(lines), MAX_LINES_PER_PART)]
    client = client if client is not None else (None if dry_run else s3_client())

    # 1. images first
    img_dir = bdir / "img"
    local_imgs = sorted(img_dir.glob("*.jpg")) if img_dir.exists() else []
    existing_img_keys = (_existing_keys(client, f"bedrock-batch/{batch_id}/img/")
                        if (client is not None and local_imgs) else set())
    n_img_uploaded = 0
    for p in local_imgs:
        key = img_key(batch_id, f"img/{p.name}")
        if key in existing_img_keys:
            continue
        if client is not None:
            client.upload_file(str(p), BUCKET, key)
        n_img_uploaded += 1

    # 2. request parts last (each PutObject triggers the worker)
    part_names = []
    for i, part_lines in enumerate(parts):
        part = _part_name(i)
        part_names.append(part)
        body = "\n".join(part_lines) + "\n"
        if client is not None:
            client.put_object(Bucket=BUCKET, Key=requests_key(batch_id, part), Body=body.encode("utf-8"),
                              ContentType="application/x-ndjson")

    manifest = {"batch_id": batch_id, "n_requests": len(lines), "parts": part_names,
                "n_images_uploaded": n_img_uploaded}
    if not dry_run:
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "upload_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("batch_id")
    ap.add_argument("--dry-run", action="store_true", help="compute parts/images without uploading")
    args = ap.parse_args(argv)
    summary = upload_batch(args.batch_id, dry_run=args.dry_run)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
