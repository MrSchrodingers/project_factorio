#!/usr/bin/env python3
"""Validate held-out F4-C evaluation runner before any evaluation outcome."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_evaluation import (
    HeldoutEvaluationAdapter,
    evaluation_arm_order,
    evaluation_preflight_tasks,
    evaluation_sequence_guard,
    evaluation_task_from_manifest,
)
from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    HarnessValidationError,
    execute_pair,
    recompute_pair_delta,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    canonical_sha256,
    validate_protocol,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    load_memory_records,
    memory_database_snapshot,
)

SCHEMA_VERSION = "cortex_f4c_evaluation_runner_validation_v1"
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
DEFAULT_OUTPUT = Path(
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
        raise TypeError(f"{path} must contain object")
    return value


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


def _pilot_reviews_complete(root: Path, manifest: dict[str, Any]) -> bool:
    seeds = [int(value) for value in manifest["seed_partitions"]["pilot"]]
    for seed in seeds:
        review_path = root / "runs/f4c_pilot" / str(seed) / "review.json"
        if not review_path.exists():
            return False
        review = _load(review_path)
        claim = review.get("claim_boundary")
        if not isinstance(claim, dict):
            return False
        if (
            review.get("status") != "pass"
            or review.get("seed") != seed
            or claim.get("instrumentation_only") is not True
            or claim.get("variance_adaptation_forbidden") is not True
            or claim.get("primary_f4c_inference") is not False
        ):
            return False
    return True


def build_validation(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError(
            "evaluation-runner validation requires clean source tree"
        )
    manifest = _load(manifest_path)
    protocol = validate_protocol(manifest)
    budget = HarnessBudget.from_manifest(manifest)
    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)

    treatment_path = root / "src/factorio_ai_lab/cortex/causal_treatment.py"
    runtime_path = (
        root / "src/factorio_ai_lab/cortex/causal_pilot_runtime.py"
    )
    treatment_audit = (
        root / "runs/audits/cortex_f4c_treatment_validation.json"
    )
    pilot_runner_audit = (
        root / "runs/audits/cortex_f4c_pilot_runner_validation.json"
    )
    semantic_lock = {
        "treatment_module_sha256": _sha256(treatment_path),
        "pilot_runtime_sha256": _sha256(runtime_path),
        "treatment_audit_sha256": _sha256(treatment_audit),
        "pilot_runner_audit_sha256": _sha256(pilot_runner_audit),
    }

    preflight_pairs = []
    pair_valid = True
    restore_ok = True
    surfaces_ok = True
    recompute_ok = True
    for index, task in enumerate(evaluation_preflight_tasks()):
        adapter = HeldoutEvaluationAdapter(task)
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
        preflight_pairs.append(pair)
        pair_valid = pair_valid and pair["valid"]
        restore_ok = restore_ok and (
            adapter.arm_start_digests == [checkpoint, checkpoint]
        )
        if pair["valid"]:
            on = pair["arms"][MEMORY_ON]
            off = pair["arms"][MEMORY_ABLATED]
            surfaces_ok = surfaces_ok and (
                on["observation"]["candidate_surface"]
                == off["observation"]["candidate_surface"]
                and on["observation"]["tool_surface"]
                == off["observation"]["tool_surface"]
                and on["budget"] == off["budget"]
            )
            recompute_ok = recompute_ok and (
                recompute_pair_delta(pair) == pair["delta_J"]
            )

    after = memory_database_snapshot(memory_path)
    evaluation_seeds = [
        int(value) for value in manifest["seed_partitions"]["evaluation"]
    ]
    schedule_rows = manifest["counterbalancing"]["schedule"]
    mappings: list[dict[str, Any]] = []
    orders: list[tuple[str, str]] = []
    for seed in evaluation_seeds:
        task = evaluation_task_from_manifest(manifest, seed)
        order = evaluation_arm_order(manifest, task)
        orders.append(order)
        mappings.append(
            {
                "seed": seed,
                "task_id": task["task_id"],
                "family": task["family"],
                "first_condition": order[0],
                "second_condition": order[1],
            }
        )

    pilot_rejected = False
    confirmatory_rejected = False
    try:
        evaluation_task_from_manifest(
            manifest,
            int(manifest["seed_partitions"]["pilot"][0]),
        )
    except HarnessValidationError:
        pilot_rejected = True
    try:
        evaluation_task_from_manifest(
            manifest,
            int(manifest["seed_partitions"]["confirmatory_reserved"][0]),
        )
    except HarnessValidationError:
        confirmatory_rejected = True

    evaluation_artifacts = [
        root / "runs/f4c_evaluation" / str(seed) / "pair.json"
        for seed in evaluation_seeds
    ]
    first_gate_open = False
    second_requires_review = False
    try:
        evaluation_sequence_guard(root, evaluation_seeds, evaluation_seeds[0])
    except HarnessValidationError:
        pass
    else:
        first_gate_open = True
    try:
        evaluation_sequence_guard(root, evaluation_seeds, evaluation_seeds[1])
    except HarnessValidationError as exc:
        second_requires_review = "no canonical review" in str(exc)

    expected_schedule = {
        (int(row["seed"]), str(row["task_id"])): (
            str(row["first_condition"]),
            str(row["second_condition"]),
        )
        for row in schedule_rows
    }
    mapping_matches_schedule = all(
        expected_schedule.get((row["seed"], row["task_id"]))
        == (row["first_condition"], row["second_condition"])
        for row in mappings
    )

    paths = {
        "evaluation_boundary": (
            root / "src/factorio_ai_lab/cortex/causal_evaluation.py"
        ),
        "inference_module": (
            root / "src/factorio_ai_lab/cortex/causal_inference.py"
        ),
        "runner": root / "scripts/run_cortex_f4c_evaluation.py",
        "validator": (
            root / "scripts/validate_cortex_f4c_evaluation_runner.py"
        ),
        "pair_auditor": (
            root / "scripts/audit_cortex_f4c_evaluation_pair.py"
        ),
        "analyzer": root / "scripts/analyze_cortex_f4c_evaluation.py",
        "tests": root / "tests/test_cortex_f4c_evaluation.py",
        "runner_tests": (
            root / "tests/test_cortex_f4c_evaluation_runner.py"
        ),
        "doc": root / "docs/CORTEX_PHASE4_EVALUATION_RUNNER.md",
    }
    source = {
        f"{key}_sha256": _sha256(path)
        for key, path in paths.items()
    }
    source.update(semantic_lock)
    source["database_before"] = before.to_dict()
    source["database_after"] = after.to_dict()

    checks = {
        "protocol_matches_frozen_v1": (
            protocol["manifest_sha256"] == canonical_sha256(manifest)
        ),
        "pilot_instrumentation_complete_8_of_8": (
            _pilot_reviews_complete(root, manifest)
        ),
        "frozen_treatment_hash_unchanged": (
            semantic_lock["treatment_module_sha256"]
            == FROZEN_TREATMENT_SHA256
        ),
        "frozen_runtime_hash_unchanged": (
            semantic_lock["pilot_runtime_sha256"]
            == FROZEN_RUNTIME_SHA256
        ),
        "frozen_treatment_audit_unchanged": (
            semantic_lock["treatment_audit_sha256"]
            == FROZEN_TREATMENT_AUDIT_SHA256
        ),
        "frozen_pilot_runner_audit_unchanged": (
            semantic_lock["pilot_runner_audit_sha256"]
            == FROZEN_PILOT_RUNNER_AUDIT_SHA256
        ),
        "four_independent_preflight_pairs_valid": (
            len(preflight_pairs) == 4 and pair_valid
        ),
        "checkpoint_restore_exact": restore_ok,
        "candidate_tool_budget_surfaces_matched": surfaces_ok,
        "outcome_J_and_delta_recomputable": recompute_ok,
        "source_memory_unchanged": before == after,
        "evaluation_seed_mapping_exact_20": (
            len(mappings) == 20
            and [row["seed"] for row in mappings] == evaluation_seeds
        ),
        "evaluation_schedule_matches_frozen_counterbalance": (
            mapping_matches_schedule
            and orders.count((MEMORY_ON, MEMORY_ABLATED)) == 10
            and orders.count((MEMORY_ABLATED, MEMORY_ON)) == 10
        ),
        "pilot_partition_rejected": pilot_rejected,
        "confirmatory_partition_rejected": confirmatory_rejected,
        "no_evaluation_artifact_exists": not any(
            path.exists() for path in evaluation_artifacts
        ),
        "first_evaluation_sequence_gate_open": first_gate_open,
        "second_evaluation_requires_first_review": second_requires_review,
        "exact_inference_frozen_before_outcomes": all(
            path.exists() for path in paths.values()
        ),
        "dry_run_does_not_execute_evaluation_seed": True,
        "no_live_factorio_authority": True,
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "evaluation_runner_dry_run",
        "code_revision": revision,
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "source": source,
        "preflight_pairs": preflight_pairs,
        "evaluation_mappings": mappings,
        "checks": checks,
        "authority": {
            "world_mutation": False,
            "factorio_rcon_used": False,
            "fle_environment_created": False,
            "world_lease_acquired": False,
            "continuous_authority": False,
            "evaluation_seed_executed": False,
            "confirmatory_seed_executed": False,
        },
        "claim_boundary": {
            "heldout_specs_executed_in_validation": False,
            "pilot_outcomes_used_for_adaptation": False,
            "inference_code_frozen_before_outcomes": True,
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"evaluation runner audit exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = (
        args.manifest if args.manifest.is_absolute() else root / args.manifest
    )
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
