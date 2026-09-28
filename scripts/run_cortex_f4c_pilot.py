#!/usr/bin/env python3
"""Execute exactly one preregistered F4-C pilot pair.

Without --execute this command is read-only and prints the frozen plan.
With --execute it uses the deterministic protocol task-world runtime.  It never
opens RCON/FLE, never acquires a live Factorio lease, and never touches
evaluation or confirmatory partitions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import HarnessBudget, execute_pair
from factorio_ai_lab.cortex.causal_pilot_runtime import (
    ProtocolTaskWorldAdapter,
    pilot_arm_order,
    pilot_sequence_guard,
    pilot_task_from_manifest,
    runtime_contract,
)
from factorio_ai_lab.cortex.causal_protocol import (
    task_fingerprint,
    validate_protocol,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    load_memory_records,
    memory_database_snapshot,
)
from factorio_ai_lab.paths import code_revision

DEFAULT_ROOT = Path("/srv/factorio-ai-lab")
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
PAIR_ROOT = Path("runs/f4c_pilot")
RUNNER_AUDIT = Path("runs/audits/cortex_f4c_pilot_runner_validation.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _evolution_state() -> dict[str, str]:
    def one(*args: str) -> str:
        done = subprocess.run(
            ["systemctl", *args, "factorio-ai-evolution"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        text = (done.stdout or done.stderr or "").strip()
        return text.splitlines()[0] if text else f"returncode:{done.returncode}"

    return {"active": one("is-active"), "enabled": one("is-enabled")}


def build_plan(
    *,
    seed: int,
    root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    protocol = validate_protocol(manifest)
    task = pilot_task_from_manifest(manifest, seed)
    first, second = pilot_arm_order(manifest, task)
    return {
        "schema_version": "cortex_f4c_pilot_plan_v1",
        "protocol_id": manifest["protocol_id"],
        "protocol_manifest_sha256": protocol["manifest_sha256"],
        "protocol_manifest_file_sha256": _sha256(manifest_path),
        "seed": int(seed),
        "task": task,
        "task_fingerprint": task_fingerprint(task),
        "first_condition": first,
        "second_condition": second,
        "pilot_sequence": list(manifest["seed_partitions"]["pilot"]),
        "runtime_contract": runtime_contract(),
        "artifact_path": str(root / PAIR_ROOT / str(seed) / "pair.json"),
        "world_authority": "protocol_task_world_only",
        "live_factorio_world": False,
        "automatic_retry": False,
    }


def _assert_execute_preflight(
    *,
    root: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    revision = code_revision()
    if revision.get("dirty") is not False:
        raise RuntimeError("pilot execution requires a clean source tree")
    state = _load_json(root / "runs" / "cortex_phase_state.json")
    protocol_state = state.get("phase4_causal_protocol")
    if not isinstance(protocol_state, dict):
        raise TypeError("phase4 causal state unavailable")
    if protocol_state.get("execution_ready") is not True:
        raise RuntimeError("F4-C execution_ready is not true")
    runner = state.get("phase4_pilot_runner")
    if not isinstance(runner, dict) or runner.get("validated") is not True:
        raise RuntimeError("F4-C pilot runner is not validated")
    evolution = _evolution_state()
    if evolution != {"active": "inactive", "enabled": "disabled"}:
        raise RuntimeError(
            "pilot requires factorio-ai-evolution inactive+disabled"
        )
    runner_audit = root / RUNNER_AUDIT
    if not runner_audit.exists():
        raise RuntimeError("pilot runner audit is missing")
    sequence = pilot_sequence_guard(
        root,
        [int(value) for value in plan["pilot_sequence"]],
        int(plan["seed"]),
    )
    artifact = Path(str(plan["artifact_path"]))
    if artifact.exists():
        raise FileExistsError(
            f"pilot pair already has canonical evidence: {artifact}"
        )
    return {
        "code_revision": revision,
        "phase_checkpoint": state.get("phase4_checkpoint"),
        "execution_ready": True,
        "runner_validated": True,
        "evolution": evolution,
        "runner_audit_sha256": _sha256(runner_audit),
        "pilot_sequence_guard": sequence,
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def execute_plan(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    plan: dict[str, Any],
) -> Path:
    preflight = _assert_execute_preflight(root=root, plan=plan)
    manifest = _load_json(manifest_path)
    budget = HarnessBudget.from_manifest(manifest)
    records = load_memory_records(memory_path)
    task = dict(plan["task"])
    adapter = ProtocolTaskWorldAdapter(task)
    source_before = memory_database_snapshot(memory_path)
    started = datetime.now(UTC).isoformat()
    pair = execute_pair(
        task=task,
        first_condition=str(plan["first_condition"]),
        second_condition=str(plan["second_condition"]),
        adapter=adapter,
        memory_records=records,
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=budget,
    )
    source_after = memory_database_snapshot(memory_path)
    artifact = Path(str(plan["artifact_path"]))
    payload = {
        "schema_version": "cortex_f4c_pilot_pair_v1",
        "status": "completed",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "seed": plan["seed"],
        "task_id": task["task_id"],
        "family": task["family"],
        "task_fingerprint": plan["task_fingerprint"],
        "protocol_id": plan["protocol_id"],
        "protocol_manifest_sha256": plan["protocol_manifest_sha256"],
        "protocol_manifest_file_sha256": plan[
            "protocol_manifest_file_sha256"
        ],
        "code_revision": preflight["code_revision"],
        "preflight": preflight,
        "runtime_contract": plan["runtime_contract"],
        "first_condition": plan["first_condition"],
        "second_condition": plan["second_condition"],
        "automatic_retry": False,
        "live_factorio_world": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "source_memory_before": source_before.to_dict(),
        "source_memory_after": source_after.to_dict(),
        "pair": pair,
        "claim_boundary": {
            "pilot_only": True,
            "excluded_from_primary_f4c_inference": True,
            "evaluation_seed_executed": False,
            "confirmatory_seed_executed": False,
        },
    }
    _atomic_json(artifact, payload)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = (
        args.manifest
        if args.manifest.is_absolute()
        else root / args.manifest
    )
    memory_path = (
        args.memory if args.memory.is_absolute() else root / args.memory
    )
    plan = build_plan(seed=args.seed, root=root, manifest_path=manifest_path)
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    artifact = execute_plan(
        root=root,
        manifest_path=manifest_path,
        memory_path=memory_path,
        plan=plan,
    )
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
