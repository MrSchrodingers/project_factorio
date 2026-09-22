#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec env HOME=/home/ti PYTHONPATH="$ROOT/src"   FACTORIO_SERVER_ADDRESS=127.0.0.1 FACTORIO_SERVER_PORT=27000   "$ROOT/.venv-fle/bin/python" -m factorio_ai_lab.experiments.evolution_loop "$@"
