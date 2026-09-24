#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from factorio_ai_lab.paths import STATE_ROOT, code_revision

SELECTION_FILES = (
    "evolution_champion.json",
    "lifelong_champion_state.json",
    "lifelong_champion_state.json.inheritance.json",
    "lifelong_champion_state.json.meta.json",
    "open_play_validated_champion.json",
    "open_play_robustness_state.json",
    "open_play_strategy.json",
    "niche_archive.json",
    "evolution_loop_state.json",
)


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


def plan(state_root: Path) -> list[Path]:
    runs = state_root / "runs"
    return [runs / name for name in SELECTION_FILES if (runs / name).exists()]


def apply_reset(state_root: Path, *, reason: str) -> dict[str, object]:
    if _evolution_active():
        raise RuntimeError("factorio-ai-evolution.service must be inactive")

    runs = state_root / "runs"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    retirement = state_root / "backups" / f"selection-reset-{stamp}"
    retirement.mkdir(parents=True, exist_ok=False)

    champion_path = runs / "evolution_champion.json"
    champion = {}
    if champion_path.exists():
        try:
            champion = json.loads(champion_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            champion = {"read_error": True}

    moved: list[str] = []
    for source in plan(state_root):
        target = retirement / source.name
        shutil.move(str(source), target)
        moved.append(source.name)

    record = {
        "schema_version": "factorio_ai_selection_reset_v1",
        "reset_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "code_revision": code_revision(),
        "retired_champion": {
            "generation": champion.get("generation") if isinstance(champion, dict) else None,
            "run_id": champion.get("run_id") if isinstance(champion, dict) else None,
            "selected_at": champion.get("selected_at") if isinstance(champion, dict) else None,
        },
        "retirement_path": str(retirement),
        "moved_files": moved,
        "preserved": [
            "evolution_history.jsonl",
            "evolution_loop_history.jsonl",
            "open_play_validation_history.jsonl",
            "generation_reports/",
            "research/",
            "telemetry/",
            "knowledge.jsonl",
            "counterexamples.jsonl",
            "repairs.jsonl",
            "datasets/",
            "models/",
        ],
    }
    (runs / "baseline_reset.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--reason",
        default=(
            "retire pre-Cortex selection state contaminated by pre-factory-graph "
            "instrumentation and establish a corrected baseline"
        ),
    )
    args = parser.parse_args()

    state_root = args.state_root.expanduser().resolve()
    targets = plan(state_root)
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "state_root": str(state_root),
                    "targets": [str(path) for path in targets],
                    "evolution_active": _evolution_active(),
                },
                indent=2,
            )
        )
        return 0

    record = apply_reset(state_root, reason=args.reason)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
