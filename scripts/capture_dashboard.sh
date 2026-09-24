#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-/srv/factorio-ai-lab}"
CHROMIUM_BIN="${FACTORIO_AI_CHROMIUM_BIN:-chromium}"
CAPTURE_HOME="${FACTORIO_AI_CAPTURE_HOME:-/home/ti}"
TIMEOUT_SECONDS="${FACTORIO_AI_CAPTURE_TIMEOUT_SECONDS:-30}"
VIRTUAL_TIME_BUDGET="${FACTORIO_AI_CAPTURE_VIRTUAL_TIME_BUDGET:-0}"
OUT="${1:-$STATE_ROOT/runs/audits/dashboard.png}"
URL="${2:-http://127.0.0.1:8765/}"

mkdir -p "$STATE_ROOT/runs" "$(dirname "$OUT")"
BASE="$(mktemp -d "$STATE_ROOT/runs/chromium-capture.XXXXXX")"
PROFILE="$BASE/profile"
CACHE="$BASE/cache"
TMP="$BASE/tmp"
LOG="$BASE/chromium.log"
mkdir -p "$PROFILE" "$CACHE" "$TMP"
chmod 700 "$TMP"

cleanup() {
  local -a pids=()
  mapfile -t pids < <(pgrep -f -- "--user-data-dir=$PROFILE" 2>/dev/null || true)
  if [[ "${#pids[@]}" -gt 0 ]]; then
    kill -TERM "${pids[@]}" 2>/dev/null || true
    sleep 0.2
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null || true
      fi
    done
  fi
  rm -rf "$BASE"
}
trap cleanup EXIT INT TERM

CHROMIUM_ARGS=(
  --headless
  --no-sandbox
  --disable-gpu
  --disable-dev-shm-usage
  --disable-background-networking
  --disable-component-update
  --disable-sync
  --no-first-run
  --no-default-browser-check
  --user-data-dir="$PROFILE"
  --disk-cache-dir="$CACHE"
  --window-size=1600,900
)
if [[ "$VIRTUAL_TIME_BUDGET" =~ ^[0-9]+$ ]] && (( VIRTUAL_TIME_BUDGET > 0 )); then
  CHROMIUM_ARGS+=(--virtual-time-budget="$VIRTUAL_TIME_BUDGET")
fi

rm -f "$OUT"
set +e
timeout --signal=TERM --kill-after=2s "${TIMEOUT_SECONDS}s"   env HOME="$CAPTURE_HOME" TMPDIR="$TMP" XDG_RUNTIME_DIR="$TMP"   "$CHROMIUM_BIN" "${CHROMIUM_ARGS[@]}"   --screenshot="$OUT" "$URL" >"$LOG" 2>&1
rc=$?
set -e

if [[ ! -s "$OUT" ]]; then
  cat "$LOG" >&2 || true
  echo "dashboard capture failed: no screenshot produced (rc=$rc)" >&2
  exit "${rc:-1}"
fi

if [[ "$rc" -ne 0 && "$rc" -ne 124 && "$rc" -ne 137 ]]; then
  cat "$LOG" >&2 || true
  echo "dashboard capture failed after producing an artifact (rc=$rc)" >&2
  exit "$rc"
fi

if [[ "$rc" -ne 0 ]]; then
  echo "dashboard capture: browser exceeded timeout after screenshot; cleanup enforced" >&2
fi

printf "%s\n" "$OUT"
