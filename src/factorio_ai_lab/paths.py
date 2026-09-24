from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

# Code and mutable experiment state are deliberately separate concepts.
#
# In a source checkout they resolve to the same directory for developer
# compatibility. Production releases set FACTORIO_AI_STATE_ROOT to the
# persistent state directory while CODE_ROOT remains the immutable release
# extracted from a clean Git commit.
CODE_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(
    os.environ.get("FACTORIO_AI_STATE_ROOT", str(CODE_ROOT))
).expanduser().resolve()

RUNS_DIR = STATE_ROOT / "runs"
DATA_DIR = STATE_ROOT / "data"
MODELS_DIR = STATE_ROOT / "models"
CONFIGS_DIR = STATE_ROOT / "configs"
BUILD_INFO_PATH = CODE_ROOT / "BUILD_INFO.json"


def read_build_info(*, code_root: Path | None = None) -> dict[str, Any] | None:
    """Read immutable release provenance, if this is a deployed release.

    Source checkouts normally have no BUILD_INFO.json and fall back to Git.
    Runtime releases are created from git archive and therefore have no
    .git directory by design; BUILD_INFO.json is authoritative there.
    """
    base = Path(code_root) if code_root is not None else CODE_ROOT
    path = base / "BUILD_INFO.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def code_revision(*, root: Path | None = None) -> dict[str, Any]:
    """Return exact code provenance without ever claiming unknown is clean."""
    directory = Path(root) if root is not None else CODE_ROOT
    build = read_build_info(code_root=directory)
    if build is not None:
        commit = build.get("commit")
        dirty = build.get("dirty")
        return {
            "commit": str(commit) if commit else None,
            "branch": build.get("branch"),
            "dirty": dirty if isinstance(dirty, bool) else None,
            "reason": None if commit and dirty is False else "invalid build provenance",
            "source": "build_info",
            "built_at": build.get("built_at"),
            "release_path": str(directory),
        }

    def _git(*args: str) -> tuple[str | None, str | None]:
        try:
            done = subprocess.run(
                ["git", "-c", f"safe.directory={directory}", "-C", str(directory), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"git unavailable: {type(exc).__name__}"
        if done.returncode != 0:
            reason = (done.stderr or "").strip().splitlines()
            return None, reason[0] if reason else "git refused"
        return done.stdout.strip(), None

    commit, reason = _git("rev-parse", "HEAD")
    if commit is None:
        return {
            "commit": None,
            "branch": None,
            "dirty": None,
            "reason": reason or "no repository at this path",
            "source": "git",
        }

    status, status_reason = _git("status", "--porcelain")
    branch, branch_reason = _git("rev-parse", "--abbrev-ref", "HEAD")
    return {
        "commit": commit,
        "branch": branch,
        "dirty": None if status is None else bool(status),
        "reason": status_reason or branch_reason,
        "source": "git",
    }


def revision_is_promotable(revision: dict[str, Any]) -> bool:
    """Only a known, clean revision may mutate champion state."""
    return bool(revision.get("commit")) and revision.get("dirty") is False



def clean_promotion_required() -> bool:
    """Whether this process is authorized to mutate champion state."""
    value = os.environ.get("FACTORIO_AI_REQUIRE_CLEAN_PROMOTION", "0")
    return value.strip().lower() in {"1", "true", "yes", "on"}
