#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT/.venv/bin/python"
TMP_ROOT="/home/ti/.cache/factorio-ai-pip-tmp"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python environment not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$TMP_ROOT"
export TMPDIR="$TMP_ROOT"
export KERAS_BACKEND=torch

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install   --extra-index-url https://download.pytorch.org/whl/cpu   -r "$ROOT/configs/locks/ml-cpu-py313.txt"

"$PYTHON_BIN" - <<'PY'
import keras
import numpy
import pandas
import scipy
import sklearn
import torch

print("numpy", numpy.__version__)
print("scipy", scipy.__version__)
print("sklearn", sklearn.__version__)
print("pandas", pandas.__version__)
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("keras", keras.__version__, "backend", keras.backend.backend())
PY
