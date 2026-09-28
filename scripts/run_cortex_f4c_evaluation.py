#!/usr/bin/env python3
"""Execute exactly one preregistered held-out F4-C evaluation pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_evaluation import (
    HeldoutEvaluationAdapter,
    evaluation_arm_order,
    evaluation_sequence_guard,
    evaluation_task_from_manifest,
)
from factorio_ai_lab.cortex.causal_harness import HarnessBudget, execute_pair
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
PAIR_ROOT = Path("runs/f4c_evaluation")
RUNNER_AUDIT = Path(
    "runs/audits/cortex_f4c_evaluation_runner_validation.json"
)

FROZEN_TREATMENT_SHA256 = (
    "d412ed9af1cfa75def10338a6dfbec97c60cf4ce792645b6d38747c3f47d3469"
)
FROZEN_RUNTIME_SHA256 = (
    "0d64659a8c932592c10b9af9476cfa3cac1af0e2a54ee5953febac295094e085"
)
FROZEN_TREATMENT_AUDIT_SHA256 = (
    "c833bd2a979da2370f3955f48df20f05fe7562efeede50e2e41b269375c63767"
)
FROZEN_PILOT_RUNNER_AUDIT_SHA256 = (
    "1e9f47159d9abcc896a632b49d86ed4ff18fc208f79d5f19f346a42b0ddc2dd8"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
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


def semantic_lock(root: Path) -> dict[str, str]:
    paths = {
        "treatment_module": (
            root / "src/factorio_ai_lab/cortex/causal_treatment.py"
        ),
        "pilot_runtime": (
            root / "src/factorio_ai_lab/cortex/causal_pilot_runtime.py"
        ),
        "treatment_audit": (
            root / "runs/audits/cortex_f4c_treatment_validation.json"
        ),
        "pilot_runner_audit": (
            root / "runs/audits/cortex_f4c_pilot_runner_validation.json"
        ),
    }
    hashes = {key: _sha256(path) for key, path in paths.items()}
    expected = {
        "treatment_module": FROZEN_TREATMENT_SHA256,
        "pilot_runtime": FROZEN_RUNTIME_SHA256,
        "treatment_audit": FROZEN_TREATMENT_AUDIT_SHA256,
        "pilot_runner_audit": FROZEN_PILOT_RUNNER_AUDIT_SHA256,
    }
    if hashes != expected:
        raise RuntimeError(
            "frozen F4-C treatment/runtime semantics changed after pilot"
        )
    return hashes


def build_plan(
    *,
    seed: int,
    root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    protocol = validate_protocol(manifest)
    task = evaluation_task_from_manifest(manifest, seed)
    first, second = evaluation_arm_order(manifest, task)
    return {
        "schema_version": "cortex_f4c_evaluation_plan_v1",
        "protocol_id": manifest["protocol_id"],
        "protocol_manifest_sha256": protocol["manifest_sha256"],
        "protocol_manifest_file_sha256": _sha256(manifest_path),
        "seed": int(seed),
        "task": task,
        "task_fingerprint": task_fingerprint(task),
        "first_condition": first,
        "second_condition": second,
        "evaluation_sequence": list(
            manifest["seed_partitions"]["evaluation"]
        ),
        "artifact_path": str(
            root / PAIR_ROOT / str(seed) / "pair.json"
        ),
        "world_authority": "heldout_protocol_task_world_only",
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
        raise RuntimeError("evaluation execution requires clean source tree")

    state = _load(root / "runs/cortex_phase_state.json")
    evaluation = state.get("phase4_evaluation_runner")
    if not isinstance(evaluation, dict) or evaluation.get("validated") is not True:
        raise RuntimeError("F4-C held-out evaluation runner is not validated")
    progress = state.get("phase4_evaluation_progress")
    if not isinstance(progress, dict):
        raise TypeError("F4-C held-out evaluation progress is unavailable")
    if progress.get("seed_launch_allowed") is not True:
        raise RuntimeError("F4-C held-out evaluation seed launch is blocked")
    if progress.get("next_seed") != int(plan["seed"]):
        raise RuntimeError(
            "requested evaluation seed is not the next frozen sequence seed"
        )

    evolution = _evolution_state()
    if evolution != {"active": "inactive", "enabled": "disabled"}:
        raise RuntimeError(
            "evaluation requires factorio-ai-evolution inactive+disabled"
        )

    runner_audit = root / RUNNER_AUDIT
    if not runner_audit.exists():
        raise RuntimeError("evaluation runner audit is missing")
    runner_payload = _load(runner_audit)
    runner_revision = runner_payload.get("code_revision")
    if not isinstance(runner_revision, dict):
        raise TypeError("evaluation runner audit revision is missing")
    if revision.get("commit") != runner_revision.get("commit"):
        raise RuntimeError(
            "evaluation source commit differs from frozen runner audit"
        )
    semantic_hashes = semantic_lock(root)
    sequence = evaluation_sequence_guard(
        root,
        [int(value) for value in plan["evaluation_sequence"]],
        int(plan["seed"]),
    )
    artifact = Path(str(plan["artifact_path"]))
    if artifact.exists():
        raise FileExistsError(
            f"evaluation pair already has canonical evidence: {artifact}"
        )
    return {
        "code_revision": revision,
        "phase_checkpoint": state.get("phase4_checkpoint"),
        "evaluation_runner_validated": True,
        "evolution": evolution,
        "runner_audit_sha256": _sha256(runner_audit),
        "semantic_hashes": semantic_hashes,
        "evaluation_sequence_guard": sequence,
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
    manifest = _load(manifest_path)
    budget = HarnessBudget.from_manifest(manifest)
    records = load_memory_records(memory_path)
    task = dict(plan["task"])
    adapter = HeldoutEvaluationAdapter(task)
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
        "schema_version": "cortex_f4c_evaluation_pair_v1",
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
        "first_condition": plan["first_condition"],
        "second_condition": plan["second_condition"],
        "automatic_retry": False,
        "live_factorio_world": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "continuous_authority": False,
        "source_memory_before": source_before.to_dict(),
        "source_memory_after": source_after.to_dict(),
        "pair": pair,
        "claim_boundary": {
            "evaluation_only": True,
            "primary_f4c_inference": True,
            "pilot_evidence_reused": False,
            "confirmatory_seed_executed": False,
            "live_factorio_world": False,
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
    manifest = (
        args.manifest if args.manifest.is_absolute() else root / args.manifest
    )
    memory = (
        args.memory if args.memory.is_absolute() else root / args.memory
    )
    plan = build_plan(
        seed=args.seed,
        root=root,
        manifest_path=manifest,
    )
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    artifact = execute_plan(
        root=root,
        manifest_path=manifest,
        memory_path=memory,
        plan=plan,
    )
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
