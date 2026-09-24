#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${FACTORIO_AI_SOURCE_ROOT:-/srv/factorio-ai-lab}"
DASHBOARD_RUNTIME_ROOT="${FACTORIO_AI_DASHBOARD_RUNTIME_ROOT:-/srv/factorio-ai-dashboard-runtime}"
STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-/srv/factorio-ai-lab}"
REF="${1:-HEAD}"
TMP_PATH="${FACTORIO_AI_TMP_PATH:-/tmp}"
MIN_TMP_FREE_BYTES="${FACTORIO_AI_MIN_TMP_FREE_BYTES:-67108864}"

cd "$SOURCE_ROOT"

TMP_FREE_BYTES="$(df -PB1 "$TMP_PATH" | awk 'NR==2 {print $4}')"
if [[ ! "$TMP_FREE_BYTES" =~ ^[0-9]+$ ]]; then
  echo "refusing dashboard deploy: unable to measure temp space at $TMP_PATH" >&2
  exit 65
fi
if (( TMP_FREE_BYTES < MIN_TMP_FREE_BYTES )); then
  echo "refusing dashboard deploy: insufficient temp space at $TMP_PATH ($TMP_FREE_BYTES < $MIN_TMP_FREE_BYTES bytes)" >&2
  exit 65
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing dashboard deploy: source checkout is dirty" >&2
  exit 64
fi

COMMIT="$(git rev-parse "$REF^{commit}")"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
RELEASES="$DASHBOARD_RUNTIME_ROOT/releases"
RELEASE="$RELEASES/$COMMIT"
TMP="$RELEASES/.tmp-$COMMIT-$$"
CURRENT="$DASHBOARD_RUNTIME_ROOT/current"

mkdir -p "$RELEASES"

if [[ ! -d "$RELEASE" ]]; then
  rm -rf "$TMP"
  mkdir -p "$TMP"
  git archive "$COMMIT" | tar -x -C "$TMP"

  python3 - "$TMP/BUILD_INFO.json" "$COMMIT" "$BRANCH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path=Path(sys.argv[1])
payload={
    "schema_version":"factorio_ai_dashboard_build_v1",
    "commit":sys.argv[2],
    "branch":sys.argv[3],
    "dirty":False,
    "built_at":datetime.now(timezone.utc).isoformat(),
    "component":"dashboard",
}
path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

  ln -s "$STATE_ROOT/.venv-fle" "$TMP/.venv-fle"
  ln -s "$STATE_ROOT/.venv" "$TMP/.venv"
  chmod -R a-w "$TMP/src" "$TMP/scripts" "$TMP/ops" 2>/dev/null || true
  chown -R ti:devs "$TMP"
  mv "$TMP" "$RELEASE"
fi

ln -sfn "$RELEASE" "$DASHBOARD_RUNTIME_ROOT/current.next"
mv -Tf "$DASHBOARD_RUNTIME_ROOT/current.next" "$CURRENT"

python3 - "$STATE_ROOT/runs/dashboard_deployment.json" "$COMMIT" "$BRANCH" "$RELEASE" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path=Path(sys.argv[1])
path.parent.mkdir(parents=True,exist_ok=True)
payload={
    "schema_version":"factorio_ai_dashboard_deployment_v1",
    "commit":sys.argv[2],
    "branch":sys.argv[3],
    "release_path":sys.argv[4],
    "deployed_at":datetime.now(timezone.utc).isoformat(),
}
tmp=path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
tmp.replace(path)
PY
chown ti:devs "$STATE_ROOT/runs/dashboard_deployment.json"

PHASE_STATE_PY="$STATE_ROOT/.venv-fle/bin/python"
PHASE_STATE_SCRIPT="$SOURCE_ROOT/scripts/cortex_phase_state.py"
PHASE_PROTOCOL="$STATE_ROOT/configs/cortex_baseline_v1.json"
if [[ -x "$PHASE_STATE_PY" && -f "$PHASE_STATE_SCRIPT" ]]; then
  "$PHASE_STATE_PY" "$PHASE_STATE_SCRIPT"     --state-root "$STATE_ROOT"     --protocol "$PHASE_PROTOCOL"     --write >/dev/null
fi

echo "$COMMIT"
