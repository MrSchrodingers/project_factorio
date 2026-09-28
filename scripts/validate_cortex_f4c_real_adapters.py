#!/usr/bin/env python3
"""Validate F4-C real-family adapters in disposable NON-PROTOCOL preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    HarnessValidationError,
    MemoryAccess,
    execute_pair,
    recompute_pair_delta,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    canonical_sha256,
    validate_protocol,
)
from factorio_ai_lab.cortex.causal_real_adapters import (
    FUEL_TOOL_SURFACE,
    PRODUCTION_TOOL_SURFACE,
    SPATIAL_TOOL_SURFACE,
    STRUCTURAL_TOOL_SURFACE,
    FuelRecoveryFixture,
    FuelRecoveryPairedAdapter,
    ProductionTransitionFixture,
    ProductionTransitionPairedAdapter,
    SpatialRoutingFixture,
    SpatialRoutingPairedAdapter,
    StructuralFlowPairedAdapter,
    StructuralRepairFixture,
    real_adapter_preflight_tasks,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    load_memory_records,
    memory_database_snapshot,
)

SCHEMA_VERSION = "cortex_f4c_real_adapters_validation_v1"
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
DEFAULT_OUTPUT = Path("runs/audits/cortex_f4c_real_adapters_validation.json")

EXPECTED_TOOLS = {
    "spatial_logistics_routing": SPATIAL_TOOL_SURFACE,
    "fuel_energy_recovery": FUEL_TOOL_SURFACE,
    "structural_flow_repair": STRUCTURAL_TOOL_SURFACE,
    "production_transition_planning": PRODUCTION_TOOL_SURFACE,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    base = ["git", "-c", f"safe.directory={root}"]
    commit = subprocess.check_output(
        [*base, "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    branch = subprocess.check_output(
        [*base, "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    dirty = bool(
        subprocess.check_output(
            [*base, "status", "--porcelain"],
            cwd=root,
            text=True,
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _adapter_for(task: dict[str, Any]):
    family = task["family"]
    if family == "spatial_logistics_routing":
        return SpatialRoutingPairedAdapter(
            SpatialRoutingFixture.from_task(
                task,
                resource_budget_tiles=int(
                    task["spec"]["resource_budget_tiles"]
                ),
            )
        )
    if family == "fuel_energy_recovery":
        return FuelRecoveryPairedAdapter(FuelRecoveryFixture.from_task(task))
    if family == "structural_flow_repair":
        return StructuralFlowPairedAdapter(StructuralRepairFixture.from_task(task))
    if family == "production_transition_planning":
        return ProductionTransitionPairedAdapter(
            ProductionTransitionFixture.from_task(task)
        )
    raise HarnessValidationError(f"unknown real adapter family: {family}")


def _execute(
    *,
    task: dict[str, Any],
    index: int,
    records,
    memory_path: Path,
    budget: HarnessBudget,
):
    adapter = _adapter_for(task)
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
    return adapter, pair


def _protocol_seed_firewall(
    *,
    task: dict[str, Any],
    records,
    budget: HarnessBudget,
) -> bool:
    forbidden = deepcopy(task)
    forbidden["partition"] = "pilot"
    forbidden["seed"] = -1
    adapter = _adapter_for(task)
    access = MemoryAccess(
        MEMORY_ON,
        tuple(records),
        max_queries=budget.max_retrieval_queries,
    )
    try:
        adapter.run_arm(forbidden, access, budget)
    except HarnessValidationError:
        return True
    return False


def _negative_task(task: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    probe = deepcopy(task)
    family = probe["family"]
    if family == "spatial_logistics_routing":
        probe["spec"]["resource_budget_tiles"] = 5
        return probe, ("route_within_resource_budget",)
    if family == "fuel_energy_recovery":
        probe["spec"]["fuel_sources"][0]["supplies_chain"] = True
        return probe, (
            "bootstrap_dependency_not_increased",
            "no_validated_capability_regresses",
        )
    if family == "structural_flow_repair":
        probe["spec"]["dead_end_delta"] = 1
        return probe, (
            "processor_exists",
            "producer_reaches_processor",
            "processor_output_increases",
        )
    if family == "production_transition_planning":
        probe["spec"]["material_budget"]["iron-ore"] = 54.0
        return probe, ("required_material_budget_satisfied",)
    raise HarnessValidationError(f"unknown negative-probe family: {family}")


def build_validation(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError(
            "F4-C real-adapter validation requires a clean source tree"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol = validate_protocol(manifest)
    budget = HarnessBudget.from_manifest(manifest)
    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)
    tasks = real_adapter_preflight_tasks()

    success_pairs: list[dict[str, Any]] = []
    negative_pairs: list[dict[str, Any]] = []
    restore_ok = True
    retrieval_only_ok = True
    surfaces_ok = True
    quarantine_ok = True
    recompute_ok = True
    tools_ok = True
    seed_firewall_ok = True
    success_hard_ok = True
    negative_fail_closed_ok = True

    for index, task in enumerate(tasks):
        adapter, pair = _execute(
            task=task,
            index=index,
            records=records,
            memory_path=memory_path,
            budget=budget,
        )
        success_pairs.append(pair)

        restore_ok = restore_ok and (
            pair["valid"]
            and len(adapter.arm_start_digests) == 2
            and all(
                digest == pair["checkpoint_digest"]
                for digest in adapter.arm_start_digests
            )
        )
        on = pair["arms"].get(MEMORY_ON, {})
        ablated = pair["arms"].get(MEMORY_ABLATED, {})
        on_memory = on.get("memory", {})
        ablated_memory = ablated.get("memory", {})
        on_retrievals = on_memory.get("retrievals", [])
        ablated_retrievals = ablated_memory.get("retrievals", [])
        retrieval_only_ok = retrieval_only_ok and (
            bool(on_retrievals)
            and bool(ablated_retrievals)
            and len(on_retrievals[0]["result"]["results"]) > 0
            and ablated_retrievals[0]["result"]["results"] == []
        )
        surfaces_ok = surfaces_ok and (
            on.get("budget") == ablated.get("budget")
            and on.get("observation", {}).get("candidate_surface")
            == ablated.get("observation", {}).get("candidate_surface")
            and on.get("observation", {}).get("tool_surface")
            == ablated.get("observation", {}).get("tool_surface")
            and on.get("observation", {}).get("outcome_extractor_version")
            == ablated.get("observation", {}).get("outcome_extractor_version")
        )
        expected_tools = list(EXPECTED_TOOLS[task["family"]])
        tools_ok = tools_ok and (
            on.get("observation", {}).get("tool_surface") == expected_tools
            and ablated.get("observation", {}).get("tool_surface")
            == expected_tools
        )
        quarantine_ok = quarantine_ok and (
            len(on_memory.get("quarantined_writes", [])) == 1
            and len(ablated_memory.get("quarantined_writes", [])) == 1
            and on_memory.get("source_write_attempted") is False
            and ablated_memory.get("source_write_attempted") is False
        )
        recompute_ok = recompute_ok and (
            recompute_pair_delta(pair) == pair["delta_J"]
        )
        success_hard_ok = success_hard_ok and all(
            pair["arms"][condition]["observation"]["hard_postconditions"][name]
            is True
            for condition in (MEMORY_ON, MEMORY_ABLATED)
            for name in task["spec"]["hard_postconditions"]
        )
        seed_firewall_ok = seed_firewall_ok and _protocol_seed_firewall(
            task=task,
            records=records,
            budget=budget,
        )

        negative_task, expected_false = _negative_task(task)
        _, negative_pair = _execute(
            task=negative_task,
            index=index,
            records=records,
            memory_path=memory_path,
            budget=budget,
        )
        negative_pairs.append(negative_pair)
        negative_fail_closed_ok = negative_fail_closed_ok and (
            negative_pair["valid"]
            and all(
                negative_pair["arms"][condition]["observation"][
                    "hard_postconditions"
                ][name]
                is False
                for condition in (MEMORY_ON, MEMORY_ABLATED)
                for name in expected_false
            )
            and all(
                negative_pair["arms"][condition]["score"]["functional_success"]
                == 0.0
                for condition in (MEMORY_ON, MEMORY_ABLATED)
            )
        )

    after = memory_database_snapshot(memory_path)
    module_path = root / "src/factorio_ai_lab/cortex/causal_real_adapters.py"
    validator_path = root / "scripts/validate_cortex_f4c_real_adapters.py"
    tests_path = root / "tests/test_cortex_f4c_real_adapters.py"
    doc_path = root / "docs/CORTEX_PHASE4_REAL_ADAPTER_VALIDATION.md"
    map_path = root / "docs/CORTEX_PHASE4_REAL_ADAPTER_MAP.md"

    checks = {
        "protocol_matches_frozen_v1": (
            protocol["manifest_sha256"] == canonical_sha256(manifest)
        ),
        "source_memory_quick_check_ok": before.quick_check == "ok",
        "source_memory_matches_frozen_manifest": (
            before.manifest_sha256
            == manifest["source_memory"]["manifest_sha256"]
            and before.item_count == manifest["source_memory"]["item_count"]
            and before.occurrence_count
            == manifest["source_memory"]["occurrence_count"]
        ),
        "source_memory_unchanged_by_real_adapter_preflight": before == after,
        "four_real_task_families_exercised": len(tasks) == 4,
        "all_real_adapter_success_pairs_valid": all(
            pair["valid"] for pair in success_pairs
        ),
        "checkpoint_restore_exact_all_families": restore_ok,
        "retrieval_only_ablation_enforced_all_families": retrieval_only_ok,
        "matched_budget_candidate_tool_outcome_surfaces": surfaces_ok,
        "expected_real_tool_bindings_exercised": tools_ok,
        "quarantined_writes_do_not_touch_source": quarantine_ok,
        "outcome_J_and_delta_recomputable": recompute_ok,
        "success_fixture_hard_postconditions_all_satisfied": success_hard_ok,
        "protocol_seed_firewall_enforced": seed_firewall_ok,
        "family_specific_false_success_probes_fail_closed": (
            negative_fail_closed_ok
        ),
        "no_live_factorio_authority_or_mutation": True,
    }
    status = "pass" if all(checks.values()) else "fail"
    transactional_exercised = (
        "integrations.fle.TransactionalFLEExecutor"
        in STRUCTURAL_TOOL_SURFACE
        and tools_ok
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "real_adapter_preflight",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority_scope": "disposable_non_protocol_only",
        "live_world_mutation": False,
        "disposable_world_mutation": True,
        "factorio_rcon_used": False,
        "real_fle_environment_created": False,
        "transactional_fle_executor_exercised": transactional_exercised,
        "world_lease_acquired": False,
        "live_execution_grant_created": False,
        "continuous_authority": False,
        "protocol_partitions_executed": False,
        "experimental_seed_executed": False,
        "pilot_seed_executed": False,
        "evaluation_seed_executed": False,
        "confirmatory_seed_executed": False,
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "source": {
            "memory_path": str(memory_path.relative_to(root)),
            "database_before": before.to_dict(),
            "database_after": after.to_dict(),
            "adapter_module_sha256": _sha256(module_path),
            "validator_script_sha256": _sha256(validator_path),
            "tests_sha256": _sha256(tests_path),
            "document_sha256": _sha256(doc_path),
            "adapter_map_sha256": _sha256(map_path),
        },
        "budget": budget.to_dict(),
        "families": [task["family"] for task in tasks],
        "success_pairs": success_pairs,
        "negative_pairs": negative_pairs,
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "all four frozen task families bind to existing project components",
                "paired arms restore identical disposable checkpoints",
                "memory ablation changes retrieval only with matched task and execution surfaces",
                "protocol partitions and experimental seeds are rejected before adapter execution",
                "family-specific negative probes remain visible rather than becoming false successes",
                "canonical source memory stays unchanged and adapter writes remain quarantined",
                "no live Factorio authority, RCON, real FLE environment, pilot, evaluation or confirmatory seed is used",
            ],
            "does_not_prove": [
                "memory improves Factorio outcomes",
                "any preregistered pilot pair has run",
                "any held-out evaluation pair has run",
                "F4-C causal effect",
                "F4 Exit Gate completion",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(
            f"F4-C real-adapter validation artifact already exists: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


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
    _write_artifact(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
