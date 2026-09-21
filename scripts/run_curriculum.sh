#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec env   HOME=/home/ti   PYTHONPATH="$ROOT/src"   FACTORIO_SERVER_ADDRESS=127.0.0.1   FACTORIO_SERVER_PORT=27000   "$ROOT/.venv-fle/bin/python"   -m factorio_ai_lab.experiments.curriculum_runner   --seed 20260921   --placement-episodes 8   --baseline-settle 16   --trial-settle 8   --scale-settle 14   --smelt-settle 24   --exploration 2.0
