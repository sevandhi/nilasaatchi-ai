#!/usr/bin/env bash
# Stage B retry wrapper (D-049): re-run on network drops; stop on done / budget / real SSO expiry.
cd "$(dirname "$0")/.." && export PATH=$HOME/.local/bin:$PATH
W=${1:-10}
for i in $(seq 1 20); do
  make extract-drain RUN=1 WORKERS=$W >> data/extract/stageb_run.log 2>&1; rc=$?
  r=$(python3 -c "import json;print(json.load(open('data/extract/batch_state.json')).get('stop_reason') or 'DONE')")
  [ $rc -ne 0 ] && [ "$r" = "DONE" ] && r="crashed (exit $rc)"
  echo "$(date +%H:%M) attempt $i: $r"
  case "$r" in DONE*|*budget*|*spend*) break;; esac
  aws --profile fai-builder sts get-caller-identity >/dev/null 2>&1 || { echo "real SSO expiry"; break; }
  sleep 60
done
cat data/extract/batch_state.json
find data/extract/pages -name "*.json" | wc -l
