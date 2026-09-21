#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec env PYTHONPATH="$ROOT/src"   "$ROOT/.venv-fle/bin/uvicorn"   factorio_ai_lab.dashboard.app:app   --host "${FACTORIO_AI_DASHBOARD_HOST:-127.0.0.1}"   --port "${FACTORIO_AI_DASHBOARD_PORT:-8765}"   --workers 1
