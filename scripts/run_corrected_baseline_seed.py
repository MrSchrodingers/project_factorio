#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


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


def build_plan(
    *,
    seed: int,
    mode: str,
    protocol_path: Path,
    release_root: Path,
    state_root: Path,
) -> dict[str, Any]:
    protocol = _load_json(protocol_path)
    key = f"{mode}_seeds"
    if key not in protocol:
        raise ValueError(f"unknown baseline mode: {mode}")
    allowed = [int(value) for value in protocol[key]]
    if seed not in allowed:
        raise ValueError(f"seed {seed} is not frozen for mode {mode}")

    build = _load_json(release_root / "BUILD_INFO.json")
    if build.get("dirty") is not False or not build.get("commit"):
        raise ValueError("release is not a known clean build")

    protocol_id = str(protocol.get("schema_version") or "baseline")
    sandbox = state_root / "baseline_runs" / protocol_id / mode / str(seed)
    command = [
        str(state_root / ".venv-fle" / "bin" / "python"),
        "-m",
        "factorio_ai_lab.experiments.curriculum_runner",
        "--seed",
        str(seed),
    ]
    return {
        "schema_version": "corrected_baseline_seed_plan_v1",
        "protocol": protocol_id,
        "protocol_path": str(protocol_path),
        "mode": mode,
        "seed": seed,
        "release": build,
        "release_root": str(release_root),
        "sandbox_state_root": str(sandbox),
        "command": command,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def run_plan(plan: dict[str, Any], *, state_root: Path) -> Path:
    if _evolution_active():
        raise RuntimeError("factorio-ai-evolution.service must remain inactive during baseline")

    sandbox = Path(str(plan["sandbox_state_root"]))
    if sandbox.exists() and any(sandbox.iterdir()):
        raise FileExistsError(f"baseline seed already has evidence: {sandbox}")
    sandbox.mkdir(parents=True, exist_ok=True)

    manifest = dict(plan)
    manifest["started_at"] = datetime.now(UTC).isoformat()
    manifest["status"] = "running"
    _write_json(sandbox / "manifest.json", manifest)

    env = os.environ.copy()
    env.update(
        {
            "HOME": "/home/ti",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(Path(str(plan["release_root"])) / "src"),
            "FACTORIO_AI_STATE_ROOT": str(sandbox),
            "FACTORIO_AI_REQUIRE_CLEAN_PROMOTION": "1",
            "FACTORIO_SERVER_ADDRESS": "127.0.0.1",
            "FACTORIO_SERVER_PORT": "27000",
        }
    )

    log_path = sandbox / "stdout.log"
    with log_path.open("w", encoding="utf-8") as handle:
        done = subprocess.run(
            [str(value) for value in plan["command"]],
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )

    reports = sorted((sandbox / "runs" / "generation_reports").glob("*.json"))
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    manifest["returncode"] = done.returncode
    manifest["status"] = "completed" if done.returncode == 0 and reports else "failed"
    manifest["generation_report"] = str(reports[-1]) if reports else None
    _write_json(sandbox / "manifest.json", manifest)

    if done.returncode != 0:
        raise RuntimeError(f"baseline seed failed with exit code {done.returncode}: {log_path}")
    if not reports:
        raise RuntimeError(f"baseline seed produced no generation report: {log_path}")

    shutil.copy2(reports[-1], sandbox / "result.json")
    return sandbox / "result.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--mode", choices=("exploratory", "confirmatory"), required=True)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("/srv/factorio-ai-lab/configs/cortex_baseline_v1.json"),
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        default=Path("/srv/factorio-ai-runtime/current"),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    plan = build_plan(
        seed=args.seed,
        mode=args.mode,
        protocol_path=args.protocol.resolve(),
        release_root=args.release_root.resolve(),
        state_root=args.state_root.resolve(),
    )
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    result = run_plan(plan, state_root=args.state_root.resolve())
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
