#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_object(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _evolution_active() -> bool:
    done=subprocess.run(
        ["systemctl","is-active","--quiet","factorio-ai-evolution.service"],
        check=False,
        timeout=5,
    )
    return done.returncode == 0


def _running_baseline_seeds(
    *,
    state_root: Path,
    protocol_id: str,
) -> list[dict[str, Any]]:
    root=state_root/"baseline_runs"/protocol_id
    if not root.exists():
        return []
    running=[]
    for manifest_path in sorted(root.glob("*/*/manifest.json")):
        try:
            manifest=_load_object(manifest_path)
        except (OSError,json.JSONDecodeError,TypeError):
            continue
        if manifest.get("status") != "running":
            continue
        running.append({
            "seed":manifest.get("seed"),
            "mode":manifest.get("mode"),
            "started_at":manifest.get("started_at"),
            "manifest":str(manifest_path),
        })
    return running


def build_launch_plan(
    *,
    seed: int,
    mode: str,
    release_root: Path,
    expected_commit: str,
    state_root: Path,
    protocol_path: Path,
) -> dict[str, Any]:
    build=_load_object(release_root/"BUILD_INFO.json")
    if build.get("dirty") is not False:
        raise ValueError("scientific release is not clean")
    if build.get("commit") != expected_commit:
        raise ValueError(
            f"scientific release commit {build.get('commit')} != expected {expected_commit}"
        )

    protocol=_load_object(protocol_path)
    protocol_commit=protocol.get("scientific_release_commit")
    if not isinstance(protocol_commit,str) or not protocol_commit:
        raise ValueError("protocol does not pin scientific_release_commit")
    if protocol_commit != expected_commit:
        raise ValueError(
            f"expected commit {expected_commit} != protocol commit {protocol_commit}"
        )
    protocol_root=protocol.get("scientific_release_root")
    if not isinstance(protocol_root,str) or not protocol_root:
        raise ValueError("protocol does not pin scientific_release_root")
    if Path(protocol_root).resolve() != release_root.resolve():
        raise ValueError(
            f"release root {release_root} != protocol root {protocol_root}"
        )
    seeds=protocol.get(f"{mode}_seeds")
    if not isinstance(seeds, list) or seed not in [int(value) for value in seeds]:
        raise ValueError(f"seed {seed} is not frozen for mode {mode}")

    protocol_id=str(protocol.get("schema_version") or "baseline")
    running=_running_baseline_seeds(
        state_root=state_root,
        protocol_id=protocol_id,
    )
    if running:
        labels=", ".join(
            f"{row.get('mode')}:{row.get('seed')}" for row in running
        )
        raise RuntimeError(
            f"refusing concurrent baseline launch; running seed(s): {labels}"
        )

    sandbox=state_root/"baseline_runs"/protocol_id/mode/str(seed)
    if sandbox.exists() and any(sandbox.iterdir()):
        raise FileExistsError(f"baseline seed already has evidence: {sandbox}")

    runner=release_root/"scripts"/"run_corrected_baseline_seed.py"
    if not runner.exists():
        raise FileNotFoundError(runner)

    command=[
        str(state_root/".venv-fle"/"bin"/"python"),
        str(runner),
        "--seed",str(seed),
        "--mode",mode,
        "--release-root",str(release_root),
        "--state-root",str(state_root),
        "--protocol",str(protocol_path),
    ]
    return {
        "schema_version":"corrected_baseline_detached_launch_v1",
        "seed":seed,
        "mode":mode,
        "protocol":protocol_id,
        "scientific_release":build,
        "expected_commit":expected_commit,
        "protocol_scientific_release_commit":protocol_commit,
        "release_root":str(release_root),
        "state_root":str(state_root),
        "sandbox":str(sandbox),
        "command":command,
    }


def _sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024 * 1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_global_isolation_snapshot(
    *,
    state_root: Path,
    seed: int,
) -> Path:
    runs=state_root/"runs"
    targets=(
        "evolution_history.jsonl",
        "evolution_loop_history.jsonl",
        "open_play_validation_history.jsonl",
        "knowledge.jsonl",
        "counterexamples.jsonl",
        "repairs.jsonl",
        "baseline_reset.json",
    )
    path=runs/"audits"/f"baseline_{seed}_global_isolation_before.json"
    if path.exists():
        raise FileExistsError(f"isolation snapshot already exists: {path}")
    champion_exists=(runs/"evolution_champion.json").exists()
    if champion_exists:
        raise RuntimeError("global evolution_champion.json must remain absent during F1-B")
    payload={
        "schema_version":"baseline_global_isolation_snapshot_v1",
        "captured_at":datetime.now(UTC).isoformat(),
        "seed":seed,
        "evolution_champion_exists":champion_exists,
        "files":{},
    }
    for rel in targets:
        target=runs/rel
        payload["files"][rel]={
            "exists":target.exists(),
            "sha256":_sha256(target) if target.exists() else None,
            "bytes":target.stat().st_size if target.exists() else None,
            "mtime_ns":target.stat().st_mtime_ns if target.exists() else None,
        }
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload,indent=2,sort_keys=True)+"\n",
        encoding="utf-8",
    )
    temp.replace(path)
    return path


def launch(plan: dict[str, Any]) -> dict[str, Any]:
    if _evolution_active():
        raise RuntimeError("factorio-ai-evolution.service must remain inactive during baseline")

    state_root=Path(str(plan["state_root"]))
    isolation_snapshot=capture_global_isolation_snapshot(
        state_root=state_root,
        seed=int(plan["seed"]),
    )
    launch_dir=state_root/"runs"/"launchers"
    launch_dir.mkdir(parents=True,exist_ok=True)
    stem=f"baseline-{plan['mode']}-{plan['seed']}"
    log_path=launch_dir/f"{stem}.log"
    record_path=launch_dir/f"{stem}.json"
    if record_path.exists():
        raise FileExistsError(f"launcher record already exists: {record_path}")

    env=os.environ.copy()
    env.update({
        "HOME":"/home/ti",
        "PYTHONUNBUFFERED":"1",
        "PYTHONPATH":str(Path(str(plan["release_root"]))/"src"),
        "FACTORIO_AI_STATE_ROOT":str(state_root),
        "FACTORIO_AI_REQUIRE_CLEAN_PROMOTION":"1",
        "FACTORIO_SERVER_ADDRESS":"127.0.0.1",
        "FACTORIO_SERVER_PORT":"27000",
    })

    started_at=datetime.now(UTC).isoformat()
    with log_path.open("ab",buffering=0) as handle:
        proc=subprocess.Popen(
            [str(value) for value in plan["command"]],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    record={
        **plan,
        "started_at":started_at,
        "launcher_pid":proc.pid,
        "log_path":str(log_path),
        "record_path":str(record_path),
        "isolation_snapshot":str(isolation_snapshot),
    }
    record_path.write_text(
        json.dumps(record,indent=2,sort_keys=True)+"\n",
        encoding="utf-8",
    )

    sandbox=Path(str(plan["sandbox"]))
    manifest=sandbox/"manifest.json"
    deadline=time.monotonic()+5.0
    while time.monotonic() < deadline:
        if manifest.exists():
            record["manifest_path"]=str(manifest)
            record["launch_state"]="manifest_observed"
            record_path.write_text(
                json.dumps(record,indent=2,sort_keys=True)+"\n",
                encoding="utf-8",
            )
            return record
        code=proc.poll()
        if code is not None:
            record["launch_state"]="exited_before_manifest"
            record["returncode"]=code
            record_path.write_text(
                json.dumps(record,indent=2,sort_keys=True)+"\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                f"baseline launcher exited before manifest with code {code}: {log_path}"
            )
        time.sleep(0.1)

    record["launch_state"]="process_alive_manifest_pending"
    record_path.write_text(
        json.dumps(record,indent=2,sort_keys=True)+"\n",
        encoding="utf-8",
    )
    return record


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--seed",type=int,required=True)
    parser.add_argument("--mode",choices=("exploratory","confirmatory"),required=True)
    parser.add_argument("--release-root",type=Path,required=True)
    parser.add_argument("--expected-commit",required=True)
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("/srv/factorio-ai-lab/configs/cortex_baseline_v1.json"),
    )
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()

    plan=build_launch_plan(
        seed=args.seed,
        mode=args.mode,
        release_root=args.release_root.resolve(),
        expected_commit=args.expected_commit,
        state_root=args.state_root.resolve(),
        protocol_path=args.protocol.resolve(),
    )
    if args.dry_run:
        print(json.dumps(plan,indent=2,sort_keys=True))
        return 0

    record=launch(plan)
    print(json.dumps(record,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
