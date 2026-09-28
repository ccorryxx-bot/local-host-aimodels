#!/usr/bin/env bash
# Download a model listed in config/models.json (skips if a valid copy exists).
# Usage: fetch_models.sh [model_id]      Env: MODELS_DIR (default $HOME/models)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/config/models.json"
ID="${1:-$(jq -er .default "$CFG")}"
MODELS_DIR="${MODELS_DIR:-$HOME/models}"

repo=$(jq -er ".models[\"$ID\"].repo" "$CFG")
file=$(jq -er ".models[\"$ID\"].file" "$CFG")
min=$(jq -er ".models[\"$ID\"].min_bytes" "$CFG")
dest="$MODELS_DIR/$file"
mkdir -p "$MODELS_DIR"

# A model is valid if it is big enough and starts with the GGUF magic bytes.
valid() {
  [ -f "$dest" ] || return 1
  [ "$(stat -c %s "$dest")" -ge "$min" ] || return 1
  [ "$(head -c 4 "$dest")" = "GGUF" ]
}

if valid; then
  echo "Model already present: $file"
  exit 0
fi

url="https://huggingface.co/$repo/resolve/main/$file"
echo "Downloading $file from $repo"
rm -f "$dest" "$dest.part"
curl -fsSL --retry 5 --retry-delay 5 --retry-all-errors --connect-timeout 30 -o "$dest.part" "$url"
mv "$dest.part" "$dest"

if ! valid; then
  echo "::error::Downloaded model failed validation (size or GGUF header)"
  rm -f "$dest"
  exit 1
fi
echo "Model ready: $file ($(du -h "$dest" | cut -f1))"
