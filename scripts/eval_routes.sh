#!/usr/bin/env bash
# Route comparison (plan T6.2) on the 8 agent eval queries, using the agent's existing model-exclusion
# switch (AGENT_EXCLUDE_MODELS) - no routing code changes. Live free-tier calls (quota, $0).
#   routed           : as built (Bedrock excluded for the agent by default)
#   all-open         : open-weight only (Groq Qwen / gpt-oss); Gemini + Cohere excluded
#   all-proprietary  : Gemini + Cohere only; Groq/local excluded
set -uo pipefail
cd "$(dirname "$0")/.."
BR="bedrock-ministral-8b,bedrock-ministral-3b"
declare -A POL=(
  [routed]="$BR"
  [all-open]="$BR,gemini-flash-lite,gemini-flash-lite-31,gemini-flash,cohere-command-a,cohere-command-a-vision"
  [all-proprietary]="$BR,groq-qwen-vl,groq-gpt-oss,local-qwen"
)
for p in routed all-open all-proprietary; do
  echo "=== policy: $p"
  AGENT_EXCLUDE_MODELS="${POL[$p]}" PYTHONPATH=. uv run python -m eval.agent.run --out "data/eval/routes_${p}.json"
done
