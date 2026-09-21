#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="$ROOT/runs/curriculum.lock"

mkdir -p "$ROOT/runs"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another curriculum runner is already active." >&2
  exit 75
fi

exec env   HOME=/home/ti   PYTHONPATH="$ROOT/src"   FACTORIO_SERVER_ADDRESS=127.0.0.1   FACTORIO_SERVER_PORT=27000   "$ROOT/.venv-fle/bin/python"   -m factorio_ai_lab.experiments.curriculum_runner   --seed 20260921   --placement-episodes 8   --baseline-settle 16   --trial-settle 8   --scale-settle 14   --smelt-settle 24   --logistics-settle 30   --belt-smelt-settle 32   --exploration 2.0
