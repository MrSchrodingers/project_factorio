#!/usr/bin/env bash
set -euo pipefail

CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-$CODE_ROOT}"
VENV_FLE="${FACTORIO_AI_VENV_FLE:-$STATE_ROOT/.venv-fle}"

cd "$STATE_ROOT"
exec env   HOME=/home/ti   PYTHONPATH="$CODE_ROOT/src"   FACTORIO_AI_STATE_ROOT="$STATE_ROOT"   "$VENV_FLE/bin/uvicorn"   factorio_ai_lab.dashboard.app:app   --host "${FACTORIO_AI_DASHBOARD_HOST:-127.0.0.1}"   --port "${FACTORIO_AI_DASHBOARD_PORT:-8765}"   --workers 1
