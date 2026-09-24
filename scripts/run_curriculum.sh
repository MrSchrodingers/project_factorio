#!/usr/bin/env bash
set -euo pipefail

CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-$CODE_ROOT}"
VENV_FLE="${FACTORIO_AI_VENV_FLE:-$STATE_ROOT/.venv-fle}"
LOCK_FILE="$STATE_ROOT/runs/curriculum.lock"

mkdir -p "$STATE_ROOT/runs"
cd "$STATE_ROOT"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another curriculum runner is already active." >&2
  exit 75
fi

exec env   HOME=/home/ti   PYTHONPATH="$CODE_ROOT/src"   FACTORIO_AI_STATE_ROOT="$STATE_ROOT"   FACTORIO_AI_REQUIRE_CLEAN_PROMOTION=1   FACTORIO_SERVER_ADDRESS=127.0.0.1   FACTORIO_SERVER_PORT=27000   "$VENV_FLE/bin/python"   -m factorio_ai_lab.experiments.curriculum_runner   --seed 20260921   --placement-episodes 8   --baseline-settle 16   --trial-settle 8   --scale-settle 14   --smelt-settle 24   --logistics-settle 30   --belt-smelt-settle 32   --exploration 2.0
