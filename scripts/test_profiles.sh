#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== core/FLE profile =="
PYTHONPATH=src .venv-fle/bin/python -m pytest -q \
  --ignore=tests/test_torch_spatial_policy.py \
  --ignore=tests/test_torch_world_model.py

echo "== ML/PyTorch profile =="
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_torch_spatial_policy.py \
  tests/test_torch_world_model.py

echo "== static checks =="
.venv/bin/python -m ruff check src tests scripts/audit_numeric_defaults.py
.venv-fle/bin/python -m compileall -q src
.venv/bin/python -m compileall -q src
git diff --check
