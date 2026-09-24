#!/usr/bin/env bash
set -euo pipefail

CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-$CODE_ROOT}"
VENV_FLE="${FACTORIO_AI_VENV_FLE:-$STATE_ROOT/.venv-fle}"

cd "$STATE_ROOT"
exec env   HOME=/home/ti   PYTHONPATH="$CODE_ROOT/src"   FACTORIO_AI_STATE_ROOT="$STATE_ROOT"   FACTORIO_AI_REQUIRE_CLEAN_PROMOTION=1   FACTORIO_SERVER_ADDRESS=127.0.0.1   FACTORIO_SERVER_PORT=27000   "$VENV_FLE/bin/python"   -m factorio_ai_lab.experiments.evolution_loop "$@"
