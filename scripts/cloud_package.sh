#!/usr/bin/env bash
# Build dist/cloud/lambda.zip for the read-only cloud demo (D-067): python3.12 x86_64 Lambda.
# Contents: fastapi + mangum + duckdb (manylinux wheels), app/cloud, and the read-only web build as static/.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist/cloud; BUILD=$OUT/build
rm -rf "$BUILD" "$OUT/lambda.zip"; mkdir -p "$BUILD"

echo "==> dependencies (manylinux x86_64, cp312)"
uv pip install --quiet --target "$BUILD" --python-platform x86_64-manylinux2014 --python-version 3.12 \
  --only-binary=:all: fastapi mangum duckdb
rm -rf "$BUILD"/boto3 "$BUILD"/botocore "$BUILD"/*.dist-info/RECORD 2>/dev/null || true  # boto3 is in the Lambda runtime

echo "==> app code"
mkdir -p "$BUILD/app"
cp app/__init__.py "$BUILD/app/"
rsync -a --exclude="__pycache__" --exclude="ENDPOINTS.md" app/cloud app/common "$BUILD/app/"

echo "==> read-only web build (static/)"
(cd web && npm run --silent build:cloud >/dev/null)
rm -rf "$BUILD/app/cloud/static"; cp -r web/dist "$BUILD/app/cloud/static"
(cd web && npm run --silent build >/dev/null)   # restore the normal local build in web/dist

find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +
(cd "$BUILD" && zip -qr9 ../lambda.zip .)
echo "unzipped: $(du -sh "$BUILD" | cut -f1)   zip: $(du -sh "$OUT/lambda.zip" | cut -f1)"
