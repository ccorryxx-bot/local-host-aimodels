#!/usr/bin/env bash
# One fixed, non-sensitive completion: proves the server answers and measures tok/s.
# Needs: LLAMA_API_KEY.  Optional: LLAMA_PORT.  Writes tps to $GITHUB_OUTPUT if set.
set -euo pipefail

: "${LLAMA_API_KEY:?LLAMA_API_KEY not set}"
PORT="${LLAMA_PORT:-8080}"

resp=$(curl -fsS --max-time 300 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Count from 1 to 30, separated by spaces."}],"max_tokens":64,"temperature":0}')

tokens=$(jq -er '.usage.completion_tokens' <<<"$resp")
text=$(jq -er '.choices[0].message.content' <<<"$resp")
if [ -z "$text" ] || [ "$tokens" -le 0 ]; then
  echo "::error::Empty completion"
  exit 1
fi

tps=$(jq -r '(.timings.predicted_per_second // 0) * 10 | round / 10' <<<"$resp")
echo "Smoke test OK: $tokens tokens, $tps tok/s"
[ -z "${GITHUB_OUTPUT:-}" ] || echo "tps=$tps" >> "$GITHUB_OUTPUT"
