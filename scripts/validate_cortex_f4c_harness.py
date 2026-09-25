#!/usr/bin/env python3
"""Validate the F4-C paired harness without touching the Factorio world."""

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
    OUTCOME_EXTRACTOR_VERSION,
    ArmObservation,
    HarnessBudget,
    checkpoint_digest,
    execute_pair,
    recompute_pair_delta,
    score_observation,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    canonical_sha256,
    validate_protocol,
)
from factorio_ai_lab.cortex.memory import ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import (
    MemoryQuery,
    load_memory_records,
    memory_database_snapshot,
)

SCHEMA_VERSION = "cortex_f4c_harness_validation_v1"
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
DEFAULT_OUTPUT = Path("runs/audits/cortex_f4c_harness_validation.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    branch = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        cwd=root,
        text=True,
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-c", f"safe.directory={root}", "status", "--porcelain"],
            cwd=root,
            text=True,
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


class SyntheticWorld:
    """Deterministic stateful fixture; it never opens FLE or RCON."""

    def __init__(self) -> None:
        self.state = {"epoch": 17, "trace": [], "capabilities": ["fixture"]}
        self.restore_digests: list[str] = []
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        self.state = deepcopy(checkpoint)
        self.restore_digests.append(self.state_digest())

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def run_arm(self, task, memory, budget) -> ArmObservation:
        self.arm_start_digests.append(self.state_digest())
        spec = task["spec"]
        query = MemoryQuery(
            query_id=f"preflight:{task['family']}",
            text=" ".join(
                [task["family"], *map(str, spec.get("candidate_classes", []))]
            ),
            scope=ValidityScope(),
            limit=min(4, budget.max_retrieval_queries),
        )
        result = memory.retrieve(query)
        memory.quarantine_write(
            {
                "kind": "synthetic_preflight_note",
                "retrieved": len(result.results),
            }
        )
        self.state["trace"].append(
            {
                "family": task["family"],
                "retrieved": len(result.results),
            }
        )
        hard = {str(name): True for name in spec["hard_postconditions"]}
        return ArmObservation(
            hard_postconditions=hard,
            action_count=1,
            observed_game_ticks=60,
            invalid_or_refused_actions=0,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.01,
            llm_calls=0,
            candidate_surface=tuple(map(str, spec["candidate_classes"])),
            tool_surface=(
                "synthetic_deterministic_action",
                "synthetic_observer",
            ),
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )


def _fixture_tasks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = manifest["task_design"]["evaluation_tasks"]
    by_family: dict[str, dict[str, Any]] = {}
    for row in tasks:
        by_family.setdefault(row["family"], row)
    return [
        {
            "task_id": f"preflight:{family}",
            "partition": "synthetic_preflight",
            "family": family,
            "seed": None,
            "generator_version": row["generator_version"],
            "spec": deepcopy(row["spec"]),
        }
        for family, row in sorted(by_family.items())
    ]


def build_validation(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F4-C harness validation requires a clean source tree")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol = validate_protocol(manifest)
    budget = HarnessBudget.from_manifest(manifest)
    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)
    pairs = []
    restore_ok = True
    retrieval_only_ok = True
    recompute_ok = True
    same_surface_ok = True
    quarantine_ok = True

    for index, task in enumerate(_fixture_tasks(manifest)):
        first = MEMORY_ON if index % 2 == 0 else MEMORY_ABLATED
        second = MEMORY_ABLATED if first == MEMORY_ON else MEMORY_ON
        world = SyntheticWorld()
        expected_checkpoint = world.state_digest()
        pair = execute_pair(
            task=task,
            first_condition=first,
            second_condition=second,
            adapter=world,
            memory_records=records,
            memory_snapshot=lambda: memory_database_snapshot(memory_path),
            budget=budget,
        )
        pairs.append(pair)
        restore_ok = restore_ok and (
            pair["valid"]
            and len(world.arm_start_digests) == 2
            and all(
                digest == expected_checkpoint
                for digest in world.arm_start_digests
            )
        )
        on = pair["arms"].get(MEMORY_ON, {})
        ablated = pair["arms"].get(MEMORY_ABLATED, {})
        on_retrievals = on.get("memory", {}).get("retrievals", [])
        ablated_retrievals = ablated.get("memory", {}).get("retrievals", [])
        retrieval_only_ok = retrieval_only_ok and (
            bool(on_retrievals)
            and bool(ablated_retrievals)
            and len(on_retrievals[0]["result"]["results"]) > 0
            and ablated_retrievals[0]["result"]["results"] == []
            and on["observation"]["candidate_surface"]
            == ablated["observation"]["candidate_surface"]
            and on["observation"]["tool_surface"]
            == ablated["observation"]["tool_surface"]
        )
        same_surface_ok = same_surface_ok and (
            on.get("budget") == ablated.get("budget")
            and on["observation"]["outcome_extractor_version"]
            == ablated["observation"]["outcome_extractor_version"]
        )
        quarantine_ok = quarantine_ok and (
            len(on.get("memory", {}).get("quarantined_writes", [])) == 1
            and len(
                ablated.get("memory", {}).get("quarantined_writes", [])
            )
            == 1
            and on.get("memory", {}).get("source_write_attempted") is False
            and ablated.get("memory", {}).get("source_write_attempted")
            is False
        )
        recomputed = recompute_pair_delta(pair)
        recompute_ok = recompute_ok and recomputed == pair["delta_J"]

    after = memory_database_snapshot(memory_path)

    fixture = _fixture_tasks(manifest)[0]
    missing_hard = {
        str(name): True for name in fixture["spec"]["hard_postconditions"]
    }
    missing_hard[next(iter(missing_hard))] = None
    missing_score = score_observation(
        fixture,
        ArmObservation(
            hard_postconditions=missing_hard,
            action_count=1,
            observed_game_ticks=60,
            invalid_or_refused_actions=0,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.01,
            llm_calls=0,
            candidate_surface=tuple(fixture["spec"]["candidate_classes"]),
            tool_surface=(
                "synthetic_deterministic_action",
                "synthetic_observer",
            ),
        ),
        budget,
    )
    over_budget_score = score_observation(
        fixture,
        ArmObservation(
            hard_postconditions={
                str(name): True
                for name in fixture["spec"]["hard_postconditions"]
            },
            action_count=budget.max_actions + 1,
            observed_game_ticks=60,
            invalid_or_refused_actions=0,
            proposed_actions=budget.max_actions + 1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.01,
            llm_calls=0,
            candidate_surface=tuple(fixture["spec"]["candidate_classes"]),
            tool_surface=(
                "synthetic_deterministic_action",
                "synthetic_observer",
            ),
        ),
        budget,
    )

    harness_module = root / "src/factorio_ai_lab/cortex/causal_harness.py"
    validator_script = root / "scripts/validate_cortex_f4c_harness.py"
    tests_path = root / "tests/test_cortex_f4c_causal_harness.py"
    doc_path = root / "docs/CORTEX_PHASE4_CAUSAL_HARNESS.md"
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
        "source_memory_unchanged_by_preflight": before == after,
        "four_task_families_exercised": len(pairs) == 4,
        "all_synthetic_pairs_valid": all(pair["valid"] for pair in pairs),
        "checkpoint_restore_exact": restore_ok,
        "retrieval_only_ablation_enforced": retrieval_only_ok,
        "matched_budget_and_surface_enforced": same_surface_ok,
        "quarantined_writes_do_not_touch_source": quarantine_ok,
        "outcome_J_and_delta_recomputable": recompute_ok,
        "missing_primary_component_fails_closed": (
            missing_score.valid is False
            and any(
                "missing_postcondition" in item
                for item in missing_score.invalid_reasons
            )
        ),
        "budget_overrun_fails_closed": (
            over_budget_score.valid is False
            and "budget_overrun:actions"
            in over_budget_score.invalid_reasons
        ),
        "no_live_authority": True,
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "synthetic_preflight",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "experimental_seed_executed": False,
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "source": {
            "memory_path": str(memory_path.relative_to(root)),
            "database_before": before.to_dict(),
            "database_after": after.to_dict(),
            "harness_module_sha256": _sha256(harness_module),
            "validator_script_sha256": _sha256(validator_script),
            "tests_sha256": _sha256(tests_path),
            "document_sha256": _sha256(doc_path),
        },
        "budget": budget.to_dict(),
        "synthetic_pairs": pairs,
        "negative_probes": {
            "missing_primary_component": missing_score.to_dict(),
            "budget_overrun": over_budget_score.to_dict(),
        },
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "paired harness restores an identical checkpoint before both arms",
                "MEMORY ABLATED removes retrieval only while preserving task, tool and candidate surfaces",
                "both arms receive identical frozen budgets",
                "memory writes are quarantined and canonical source memory remains unchanged",
                "primary endpoint J and paired delta_J are recomputable",
                "missing primary data and budget overruns fail closed",
            ],
            "does_not_prove": [
                "memory improves Factorio task outcomes",
                "any pilot or held-out evaluation task has run",
                "real Factorio adapters for the four task families are validated",
                "F4-C causal claim",
                "F4 Exit Gate completion",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(
            f"F4-C harness validation artifact already exists: {path}"
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
