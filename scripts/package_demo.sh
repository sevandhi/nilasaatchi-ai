#!/usr/bin/env bash
# Build a self-contained demo bundle to copy to another machine:
#   dist/nilasaatchi-demo/            code (no Dataset/, Documents/, ref/, .env, venvs, node_modules)
#   dist/nilasaatchi-demo/bundle/     nilasaatchi.dump (database) + data.tar.gz (runtime data only)
#   dist/updated_demo.zip             the same, as one file to share (ZIP_NAME=… to rename)
# On the new machine: unzip nilasaatchi-demo.zip && cd nilasaatchi-demo && bash scripts/demo_setup.sh
# The bundle contains real land records (owner names masked in the UI): share it only within the team.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist/nilasaatchi-demo
say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
rm -rf "$OUT"; mkdir -p "$OUT/bundle"

say "Code"
tar -cf - --exclude=./Dataset --exclude=./Documents --exclude=./ref --exclude=./data --exclude=./dist \
  --exclude=./.env --exclude=./.venv --exclude=./web/node_modules --exclude=./web/dist --exclude=./web/.env \
  --exclude='__pycache__' --exclude=./web/test-results --exclude=./web/playwright-report --exclude=./.pytest_cache \
  --exclude=./.ruff_cache --exclude=./.git --exclude=./.hypothesis . | tar -xf - -C "$OUT"

say "Database dump"
docker compose exec -T db pg_dump -U nila -d nilasaatchi -Fc -Z 6 > "$OUT/bundle/nilasaatchi.dump"
ls -lh "$OUT/bundle/nilasaatchi.dump" | awk '{print "   " $5}'

say "Runtime data (page images, satellite tables + chips, model, logs the UI reads, demo upload files)"
tar -czf "$OUT/bundle/data.tar.gz" \
  data/pages data/models data/extract data/eval data/runs data/doctor.json data/aws_spend.json \
  data/router_log.sqlite data/quota.sqlite data/demo_uploads $( [ -d data/summaries ] && echo data/summaries ) \
  $(ls data/s2/*.parquet data/s2/*.json data/s2/*.jsonl 2>/dev/null) data/s2/chips data/s2/panels data/s2/labels data/s2/controls
ls -lh "$OUT/bundle/data.tar.gz" | awk '{print "   " $5}'

say "Single-file bundle (zip)"
ZIP=${ZIP_NAME:-updated_demo.zip}
rm -f "dist/$ZIP"
(cd dist && zip -qr -y "$ZIP" nilasaatchi-demo -n .gz:.dump)   # already-compressed parts stored as-is
ls -lh "dist/$ZIP" | awk -v z="$ZIP" '{print "   dist/" z "  " $5}'
echo "Share it; on the new machine: unzip $ZIP && cd nilasaatchi-demo && bash scripts/demo_setup.sh (Windows: SETUP-WINDOWS.cmd) && bash scripts/demo_run.sh"
