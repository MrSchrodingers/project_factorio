#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="$ROOT/runs/curriculum.lock"

mkdir -p "$ROOT/runs"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another Factorio research runner is already active." >&2
  exit 75
fi

exec env   HOME=/home/ti   PYTHONPATH="$ROOT/src"   FACTORIO_SERVER_ADDRESS=127.0.0.1   FACTORIO_SERVER_PORT=27000   "$ROOT/.venv-fle/bin/python"   -m factorio_ai_lab.experiments.open_play_runner
