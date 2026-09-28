#!/usr/bin/env bash
# Start llama-server on localhost and wait until it is healthy.
# Needs: LLAMA_DIR, LLAMA_API_KEY.  Optional: MODELS_DIR, LLAMA_PORT.
# Server output goes to a file, never to the public Actions log (repo is public).
# Usage: start_llama.sh [model_id]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/config/models.json"
: "${LLAMA_DIR:?LLAMA_DIR not set}"
: "${LLAMA_API_KEY:?LLAMA_API_KEY not set}"
ID="${1:-$(jq -er .default "$CFG")}"
MODELS_DIR="${MODELS_DIR:-$HOME/models}"
PORT="${LLAMA_PORT:-8080}"
TMP="${RUNNER_TEMP:-/tmp}"
LOG="$TMP/llama-server.log"

file=$(jq -er ".models[\"$ID\"].file" "$CFG")
ctx=$(jq -er ".models[\"$ID\"].ctx" "$CFG")
model="$MODELS_DIR/$file"
[ -f "$model" ] || { echo "::error::Model file missing: $file"; exit 1; }

# Key via file (not argv) so it does not show up in `ps`.
( umask 077; printf '%s' "$LLAMA_API_KEY" > "$TMP/llama.key" )

export LD_LIBRARY_PATH="$LLAMA_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
nohup "$LLAMA_DIR/llama-server" \
  -m "$model" \
  --host 127.0.0.1 --port "$PORT" \
  -c "$ctx" -t "$(nproc)" -np 1 --jinja \
  --api-key-file "$TMP/llama.key" \
  --no-webui --no-slots \
  >"$LOG" 2>&1 &
pid=$!
echo "$pid" > "$TMP/llama-server.pid"
echo "llama-server started (pid $pid), waiting for health (max 6 min)"

for _ in $(seq 1 120); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "::error::llama-server exited during startup. Last log lines:"
    tail -n 30 "$LOG"
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "llama-server healthy"
    exit 0
  fi
  sleep 3
done

echo "::error::llama-server not healthy after 6 minutes. Last log lines:"
tail -n 30 "$LOG"
exit 1
