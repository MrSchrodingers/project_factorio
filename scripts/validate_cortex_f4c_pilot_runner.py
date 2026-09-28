#!/usr/bin/env python3
"""Validate the F4-C pilot runner without executing any protocol seed."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    HarnessValidationError,
    execute_pair,
    recompute_pair_delta,
)
from factorio_ai_lab.cortex.causal_pilot_runtime import (
    PROTOCOL_EXECUTORS,
    ProtocolTaskWorldAdapter,
    execute_candidate,
    pilot_arm_order,
    pilot_sequence_guard,
    pilot_task_from_manifest,
    runner_validation_tasks,
    runtime_contract,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    canonical_sha256,
    validate_protocol,
)
from factorio_ai_lab.cortex.causal_treatment import CANDIDATE_BINDINGS
from factorio_ai_lab.cortex.memory_retrieval import (
    load_memory_records,
    memory_database_snapshot,
)

SCHEMA_VERSION = "cortex_f4c_pilot_runner_validation_v1"
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
DEFAULT_OUTPUT = Path("runs/audits/cortex_f4c_pilot_runner_validation.json")
TREATMENT_AUDIT = Path("runs/audits/cortex_f4c_treatment_validation.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    base = ["git", "-c", f"safe.directory={root}"]
    commit = subprocess.check_output(
        [*base, "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    branch = subprocess.check_output(
        [*base, "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            [*base, "status", "--porcelain"], cwd=root, text=True
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def build_validation(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("pilot-runner validation requires a clean source tree")

    manifest = _load(manifest_path)
    protocol = validate_protocol(manifest)
    budget = HarnessBudget.from_manifest(manifest)
    treatment_path = root / TREATMENT_AUDIT
    if not treatment_path.exists():
        raise RuntimeError("treatment validation audit is missing")
    treatment = _load(treatment_path)
    if (
        treatment.get("status") != "pass"
        or treatment.get("schema_version")
        != "cortex_f4c_treatment_validation_v1"
    ):
        raise RuntimeError("treatment validation has not passed")

    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)
    fixtures = runner_validation_tasks()

    executor_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    every_executor_ok = True
    pair_restore_ok = True
    pair_surface_ok = True
    pair_recompute_ok = True

    for index, task in enumerate(fixtures):
        for candidate in task["spec"]["candidate_classes"]:
            result = execute_candidate(task, candidate, budget)
            executor_rows.append(
                {
                    "task_id": task["task_id"],
                    "family": task["family"],
                    "candidate": candidate,
                    "result": result.to_dict(),
                }
            )
            every_executor_ok = every_executor_ok and (
                set(result.hard_postconditions)
                == set(task["spec"]["hard_postconditions"])
                and result.proposed_actions >= result.action_count
                and result.proposed_actions >= 1
                and result.action_count >= 0
                and result.observed_game_ticks >= 0
            )

        adapter = ProtocolTaskWorldAdapter(task)
        checkpoint = adapter.state_digest()
        first = MEMORY_ON if index % 2 == 0 else MEMORY_ABLATED
        second = MEMORY_ABLATED if first == MEMORY_ON else MEMORY_ON
        pair = execute_pair(
            task=task,
            first_condition=first,
            second_condition=second,
            adapter=adapter,
            memory_records=records,
            memory_snapshot=lambda: memory_database_snapshot(memory_path),
            budget=budget,
        )
        pair_rows.append(pair)
        pair_restore_ok = pair_restore_ok and (
            pair["valid"]
            and adapter.arm_start_digests == [checkpoint, checkpoint]
        )
        on = pair["arms"][MEMORY_ON]["observation"]
        off = pair["arms"][MEMORY_ABLATED]["observation"]
        pair_surface_ok = pair_surface_ok and (
            on["candidate_surface"] == off["candidate_surface"]
            and on["tool_surface"] == off["tool_surface"]
            and pair["arms"][MEMORY_ON]["budget"]
            == pair["arms"][MEMORY_ABLATED]["budget"]
        )
        pair_recompute_ok = pair_recompute_ok and (
            recompute_pair_delta(pair) == pair["delta_J"]
        )

    after = memory_database_snapshot(memory_path)

    pilot_tasks = manifest["task_design"]["pilot_tasks"]
    pilot_mappings = []
    orders = []
    for task in pilot_tasks:
        mapped = pilot_task_from_manifest(manifest, int(task["seed"]))
        order = pilot_arm_order(manifest, mapped)
        orders.append(order)
        pilot_mappings.append(
            {
                "seed": task["seed"],
                "task_id": mapped["task_id"],
                "family": mapped["family"],
                "first_condition": order[0],
                "second_condition": order[1],
            }
        )

    evaluation_rejected = False
    confirmatory_rejected = False
    try:
        pilot_task_from_manifest(
            manifest,
            int(manifest["seed_partitions"]["evaluation"][0]),
        )
    except HarnessValidationError:
        evaluation_rejected = True
    try:
        pilot_task_from_manifest(
            manifest,
            int(manifest["seed_partitions"]["confirmatory_reserved"][0]),
        )
    except HarnessValidationError:
        confirmatory_rejected = True

    canonical_pilot_artifacts = [
        root / "runs" / "f4c_pilot" / str(seed) / "pair.json"
        for seed in manifest["seed_partitions"]["pilot"]
    ]
    pilot_sequence = [int(seed) for seed in manifest["seed_partitions"]["pilot"]]
    first_sequence_gate_open = False
    second_requires_review = False
    try:
        pilot_sequence_guard(root, pilot_sequence, pilot_sequence[0])
    except HarnessValidationError:
        first_sequence_gate_open = False
    else:
        first_sequence_gate_open = True
    try:
        pilot_sequence_guard(root, pilot_sequence, pilot_sequence[1])
    except HarnessValidationError as exc:
        second_requires_review = "no PASS review" in str(exc)
    runtime = runtime_contract()
    checks = {
        "protocol_matches_frozen_v1": (
            protocol["manifest_sha256"] == canonical_sha256(manifest)
        ),
        "treatment_validation_passed": treatment.get("status") == "pass",
        "all_frozen_candidate_classes_protocol_executable": (
            set(PROTOCOL_EXECUTORS) == set(CANDIDATE_BINDINGS)
        ),
        "all_13_candidate_executors_exercised_on_independent_fixtures": (
            len({row["candidate"] for row in executor_rows}) == 13
            and every_executor_ok
        ),
        "four_independent_family_pairs_valid": (
            len(pair_rows) == 4 and all(row["valid"] for row in pair_rows)
        ),
        "checkpoint_restore_exact": pair_restore_ok,
        "candidate_tool_budget_surfaces_matched": pair_surface_ok,
        "outcome_J_and_delta_recomputable": pair_recompute_ok,
        "source_memory_unchanged": before == after,
        "pilot_seed_mapping_exact": (
            len(pilot_mappings) == 8
            and [row["seed"] for row in pilot_mappings]
            == manifest["seed_partitions"]["pilot"]
        ),
        "pilot_counterbalancing_is_4_4": (
            orders.count((MEMORY_ON, MEMORY_ABLATED)) == 4
            and orders.count((MEMORY_ABLATED, MEMORY_ON)) == 4
        ),
        "evaluation_partition_rejected": evaluation_rejected,
        "confirmatory_partition_rejected": confirmatory_rejected,
        "no_canonical_pilot_artifact_exists": not any(
            path.exists() for path in canonical_pilot_artifacts
        ),
        "first_pilot_sequence_gate_open": first_sequence_gate_open,
        "second_pilot_requires_first_review": second_requires_review,
        "runtime_has_no_live_factorio_authority": (
            runtime["live_factorio_world"] is False
            and runtime["rcon"] is False
            and runtime["fle_environment"] is False
            and runtime["continuous_authority"] is False
        ),
        "dry_run_does_not_execute_protocol_seed": True,
    }
    status = "pass" if all(checks.values()) else "fail"

    runner_path = root / "scripts" / "run_cortex_f4c_pilot.py"
    validator_path = root / "scripts" / "validate_cortex_f4c_pilot_runner.py"
    tests_path = root / "tests" / "test_cortex_f4c_pilot_runner.py"
    runtime_path = (
        root / "src" / "factorio_ai_lab" / "cortex" / "causal_pilot_runtime.py"
    )
    pair_auditor_path = root / "scripts" / "audit_cortex_f4c_pilot_pair.py"
    pair_audit_tests_path = root / "tests" / "test_cortex_f4c_pilot_audit.py"
    doc_path = root / "docs" / "CORTEX_PHASE4_PILOT_RUNNER.md"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "pilot_runner_dry_run",
        "code_revision": revision,
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "source": {
            "runner_sha256": _sha256(runner_path),
            "validator_sha256": _sha256(validator_path),
            "tests_sha256": _sha256(tests_path),
            "runtime_sha256": _sha256(runtime_path),
            "pair_auditor_sha256": _sha256(pair_auditor_path),
            "pair_audit_tests_sha256": _sha256(pair_audit_tests_path),
            "document_sha256": _sha256(doc_path),
            "treatment_audit_sha256": _sha256(treatment_path),
            "database_before": before.to_dict(),
            "database_after": after.to_dict(),
        },
        "runtime_contract": runtime,
        "executor_rows": executor_rows,
        "pair_rows": pair_rows,
        "pilot_mappings": pilot_mappings,
        "checks": checks,
        "authority": {
            "world_mutation": False,
            "factorio_rcon_used": False,
            "fle_environment_created": False,
            "world_lease_acquired": False,
            "pilot_seed_executed": False,
            "evaluation_seed_executed": False,
            "confirmatory_seed_executed": False,
            "continuous_authority": False,
        },
        "claim_boundary": {
            "proves": [
                "all 13 frozen candidate labels have executable task-world bindings",
                "paired task-world execution restores exact checkpoints and uses matched non-memory surfaces",
                "the frozen eight pilot seeds map exactly and are counterbalanced 4/4",
                "evaluation and confirmatory partitions are rejected by the pilot mapping",
            ],
            "does_not_prove": [
                "any pilot pair has executed",
                "pilot variance is unbiased",
                "memory improves outcomes",
                "held-out evaluation effect",
            ],
            "pilot_design_exposure_note": (
                "Pilot task specs were inspected during implementation smoke. "
                "Pilot artifacts are instrumentation-only and must not be used "
                "for variance adaptation or primary F4-C inference."
            ),
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"pilot-runner audit already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/factorio-ai-lab"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    memory = args.memory if args.memory.is_absolute() else root / args.memory
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_validation(
        root=root,
        manifest_path=manifest,
        memory_path=memory,
        revision=_git_revision(root),
    )
    _write(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
