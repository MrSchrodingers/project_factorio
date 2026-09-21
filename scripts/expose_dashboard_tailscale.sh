#!/usr/bin/env bash
set -euo pipefail

PORT="${FACTORIO_AI_DASHBOARD_PORT:-8765}"
TARGET="${FACTORIO_AI_DASHBOARD_TARGET:-127.0.0.1:$PORT}"

tailscale serve --bg --tcp="$PORT" "$TARGET"
tailscale serve status
