#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOTSTRAP_VENV="$ROOT/.venv"
FLE_VENV="$ROOT/.venv-fle"
TMP_ROOT="$ROOT/.tmp"
UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}"

mkdir -p "$TMP_ROOT" "$UV_CACHE_DIR"
export TMPDIR="$TMP_ROOT"
export UV_CACHE_DIR
export UV_LINK_MODE=copy

if [ ! -x "$BOOTSTRAP_VENV/bin/python" ]; then
    python3 -m venv "$BOOTSTRAP_VENV"
fi

if [ ! -x "$BOOTSTRAP_VENV/bin/uv" ]; then
    "$BOOTSTRAP_VENV/bin/python" -m pip install --no-cache-dir "uv>=0.12,<1"
fi

UV="$BOOTSTRAP_VENV/bin/uv"
"$UV" python install 3.12

if [ ! -x "$FLE_VENV/bin/python" ]; then
    "$UV" venv --python 3.12 "$FLE_VENV"
fi

"$UV" pip install     --python "$FLE_VENV/bin/python"     "factorio-learning-environment==0.4.3"     "a2a-sdk>=0.3.26,<1"

PYTHONPATH="$ROOT/src" "$FLE_VENV/bin/python" - <<'PY'
import importlib.metadata as metadata
from fle.env.gym_env.registry import list_available_environments

print("factorio-learning-environment", metadata.version("factorio-learning-environment"))
print("a2a-sdk", metadata.version("a2a-sdk"))
print("registered_environments", len(list(list_available_environments())))
PY
