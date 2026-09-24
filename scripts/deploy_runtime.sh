#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${FACTORIO_AI_SOURCE_ROOT:-/srv/factorio-ai-lab}"
RUNTIME_ROOT="${FACTORIO_AI_RUNTIME_ROOT:-/srv/factorio-ai-runtime}"
STATE_ROOT="${FACTORIO_AI_STATE_ROOT:-/srv/factorio-ai-lab}"
REF="${1:-HEAD}"

cd "$SOURCE_ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing deploy: source checkout is dirty" >&2
  exit 64
fi

COMMIT="$(git rev-parse "$REF^{commit}")"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
RELEASES="$RUNTIME_ROOT/releases"
RELEASE="$RELEASES/$COMMIT"
TMP="$RELEASES/.tmp-$COMMIT-$$"
CURRENT="$RUNTIME_ROOT/current"

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
    "schema_version":"factorio_ai_build_v1",
    "commit":sys.argv[2],
    "branch":sys.argv[3],
    "dirty":False,
    "built_at":datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
PY

  ln -s "$STATE_ROOT/.venv-fle" "$TMP/.venv-fle"
  ln -s "$STATE_ROOT/.venv" "$TMP/.venv"
  chmod -R a-w "$TMP/src" "$TMP/scripts" "$TMP/ops" 2>/dev/null || true
  chown -R ti:devs "$TMP"
  mv "$TMP" "$RELEASE"
fi

ln -sfn "$RELEASE" "$RUNTIME_ROOT/current.next"
mv -Tf "$RUNTIME_ROOT/current.next" "$CURRENT"

python3 - "$STATE_ROOT/runs/deployment.json" "$COMMIT" "$BRANCH" "$RELEASE" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path=Path(sys.argv[1])
path.parent.mkdir(parents=True,exist_ok=True)
payload={
    "schema_version":"factorio_ai_deployment_v1",
    "commit":sys.argv[2],
    "branch":sys.argv[3],
    "release_path":sys.argv[4],
    "deployed_at":datetime.now(timezone.utc).isoformat(),
}
tmp=path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
tmp.replace(path)
PY
chown ti:devs "$STATE_ROOT/runs/deployment.json"

echo "$COMMIT"
