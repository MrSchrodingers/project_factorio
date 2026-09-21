#!/usr/bin/env bash
set -euo pipefail

MODEL="${FACTORIO_AI_MODEL:-/home/ti/models/qwen3-4b/Qwen3-4B-Q4_K_M.gguf}"
LLAMA_SERVER="${LLAMA_SERVER:-/home/ti/src/llama.cpp/build/bin/llama-server}"

if [ ! -x "$LLAMA_SERVER" ]; then
  echo "llama-server not found: $LLAMA_SERVER" >&2
  exit 1
fi
if [ ! -f "$MODEL" ]; then
  echo "model not found: $MODEL" >&2
  exit 1
fi

exec "$LLAMA_SERVER"   --model "$MODEL"   --alias qwen3-4b   --host 127.0.0.1   --port 18081   --ctx-size "${FACTORIO_AI_LLM_CTX:-8192}"   --threads "${FACTORIO_AI_LLM_THREADS:-12}"   --threads-batch "${FACTORIO_AI_LLM_THREADS_BATCH:-12}"   --parallel 1   --jinja   --no-webui
