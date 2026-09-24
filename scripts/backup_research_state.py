#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from factorio_ai_lab.paths import STATE_ROOT, code_revision


def _evolution_active() -> bool:
    try:
        done = subprocess.run(
            ["systemctl", "is-active", "--quiet", "factorio-ai-evolution.service"],
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(state_root: Path, output_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_root / f"research-state-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)

    runs = state_root / "runs"
    if runs.exists():
        shutil.copytree(runs, destination / "runs")

    manifest = {
        "schema_version": "factorio_ai_state_backup_v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "state_root": str(state_root),
        "code_revision": code_revision(),
        "files": {},
    }
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(destination))] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--allow-active",
        action="store_true",
        help="allow a non-quiescent copy; never use for confirmatory reset",
    )
    args = parser.parse_args()

    if _evolution_active() and not args.allow_active:
        raise SystemExit("refusing backup: factorio-ai-evolution.service is active")

    state_root = args.state_root.expanduser().resolve()
    output_root = (
        args.output_root.expanduser().resolve()
        if args.output_root is not None
        else state_root / "backups"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    destination = create_backup(state_root, output_root)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
