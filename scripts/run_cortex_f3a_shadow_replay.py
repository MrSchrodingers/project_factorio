#!/usr/bin/env python3
"""Produce the canonical F3-A executive SHADOW replay artifact.

The replay reads one *observed* legacy repair symptom from repairs.jsonl and
expands the counterfactual alternatives with the pure repair planner. It never
opens FLE, RCON, a WorldLease, an execution grant, or a transactional executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.executive import (
    BeliefState,
    ExecutiveGoal,
    GoalDiagnosis,
    GoalKind,
    GoalStack,
    ScoreTablePolicy,
    run_shadow_executive,
)
from factorio_ai_lab.learning.repair_loop import (
    CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
    DEFICIT_OUTPUT_UNPROCESSED,
    Deficit,
    Diagnosis,
    RepairObservation,
)

TARGET_SYMPTOM = (
    "producer_output_unprocessed:output_buffered_not_processed"
)
SCHEMA_VERSION = "cortex_f3a_executive_shadow_replay_v1"


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


def _observed_record(
    path: Path,
    *,
    symptom: str = TARGET_SYMPTOM,
) -> tuple[int, dict[str, Any]]:
    selected: tuple[int, dict[str, Any]] | None = None
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if row.get("symptom") != symptom:
            continue
        targets = row.get("targets")
        if not isinstance(targets, list) or not targets:
            continue
        selected = (index, row)
    if selected is None:
        raise RuntimeError(f"no observed repair row for symptom {symptom!r}")
    return selected


def build_replay(
    *,
    root: Path,
    repairs_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F3-A canonical replay requires a clean source tree")

    row_index, observed = _observed_record(repairs_path)
    targets = tuple(str(value) for value in observed["targets"])
    run_id = "cortex-f3a-shadow-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    goal = ExecutiveGoal(
        goal_id="restore-material-flow",
        objective="restore a producer-to-processor material path",
        kind=GoalKind.REPAIR,
        priority=10.0,
    )
    goal_stack = GoalStack((goal,))
    diagnosis = GoalDiagnosis(
        goal_id=goal.goal_id,
        diagnosis=Diagnosis(
            deficit=Deficit(
                kind=DEFICIT_OUTPUT_UNPROCESSED,
                source="historical_repair_ledger",
                severity=float(len(targets)),
                entities=targets,
                measurement={
                    "observed_run_id": observed.get("run_id"),
                    "observed_generation": observed.get("generation"),
                    "observed_stage": observed.get("stage"),
                    "observed_action_key": observed.get("action_key"),
                },
            ),
            cause=CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
            basis="observed_historical_symptom",
        ),
        basis="runs/repairs.jsonl observed repair row",
    )
    belief = BeliefState(
        belief_id=f"{run_id}:belief",
        source="runs/repairs.jsonl",
        metrics={
            "observed_generation": observed.get("generation"),
            "observed_executed": observed.get("executed"),
            "observed_reward": (observed.get("outcome") or {}).get("reward"),
        },
        unknowns=(
            "counterfactual alternatives were not executed in the observed run",
        ),
    )
    observation = RepairObservation(graph={})

    build_policy = ScoreTablePolicy(
        policy_id="f3a-shadow-prefer-build",
        scores={
            "placement:place_processing_for_buffered_output": 2.0,
            "rebuild:reroute_producer_logistics": 1.0,
        },
    )
    reroute_policy = ScoreTablePolicy(
        policy_id="f3a-shadow-prefer-reroute",
        scores={
            "placement:place_processing_for_buffered_output": 1.0,
            "rebuild:reroute_producer_logistics": 2.0,
        },
    )
    build_run = run_shadow_executive(
        run_id=f"{run_id}:build-policy",
        belief=belief,
        goal_stack=goal_stack,
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=build_policy,
    )
    reroute_run = run_shadow_executive(
        run_id=f"{run_id}:reroute-policy",
        belief=belief,
        goal_stack=goal_stack,
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=reroute_policy,
    )

    build_ids = [
        row.candidate_id for row in build_run.generation.candidates
    ]
    reroute_ids = [
        row.candidate_id for row in reroute_run.generation.candidates
    ]
    candidate_keys = [
        row.action.key for row in build_run.generation.candidates
    ]
    same_candidate_set = build_ids == reroute_ids
    different_choice = (
        build_run.decision.selected_candidate_id
        != reroute_run.decision.selected_candidate_id
    )
    predictions_predeclared = all(
        row.prediction.metric
        and row.prediction.direction
        for row in build_run.generation.candidates
    )
    feasible_count = sum(
        1 for row in build_run.feasibility if row.feasible
    )

    checks = {
        "observed_symptom_present": observed.get("symptom") == TARGET_SYMPTOM,
        "observed_targets_present": bool(targets),
        "candidate_count_at_least_two": len(candidate_keys) >= 2,
        "same_goal_same_candidate_set": same_candidate_set,
        "different_policy_different_choice": different_choice,
        "predictions_exist_before_action": predictions_predeclared,
        "all_candidates_hard_feasible": (
            feasible_count == len(candidate_keys) and bool(candidate_keys)
        ),
        "build_choice_is_placement": (
            build_run.decision.selected_action_key
            == "placement:place_processing_for_buffered_output"
        ),
        "reroute_choice_is_rebuild": (
            reroute_run.decision.selected_action_key
            == "rebuild:reroute_producer_logistics"
        ),
        "shadow_only": (
            build_run.authority.value == "shadow"
            and reroute_run.authority.value == "shadow"
        ),
        "world_mutation_false": (
            build_run.world_mutation is False
            and reroute_run.world_mutation is False
        ),
        "execute_authorized_false": (
            build_run.execute_authorized is False
            and reroute_run.execute_authorized is False
        ),
    }
    status = "pass" if all(checks.values()) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "observed_evidence": {
            "source": str(repairs_path.relative_to(root)),
            "source_sha256": _sha256(repairs_path),
            "row_index": row_index,
            "run_id": observed.get("run_id"),
            "generation": observed.get("generation"),
            "stage": observed.get("stage"),
            "symptom": observed.get("symptom"),
            "action_key": observed.get("action_key"),
            "executed": observed.get("executed"),
            "targets": list(targets),
            "outcome": observed.get("outcome"),
        },
        "counterfactual_expansion": {
            "source": (
                "factorio_ai_lab.learning.repair_loop.propose_actions"
            ),
            "observed_in_world": False,
            "candidate_ids": build_ids,
            "candidate_action_keys": candidate_keys,
            "candidate_count": len(candidate_keys),
        },
        "belief": belief.to_dict(),
        "goal_stack": goal_stack.to_dict(),
        "goal_diagnosis": diagnosis.to_dict(),
        "policy_replays": {
            "prefer_build": build_run.to_dict(),
            "prefer_reroute": reroute_run.to_dict(),
        },
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "typed belief and goal stack can drive a shadow executive decision",
                "one observed goal-linked symptom expands to multiple alternatives",
                "policy is separable from candidate generation",
                "different policies can select different alternatives over the same candidate set",
                "prediction is declared before any action",
            ],
            "does_not_prove": [
                "live executive authority",
                "sustained autonomy",
                "learned policy superiority",
                "counterfactual outcome correctness",
                "F3 Exit Gate completion",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--repairs",
        type=Path,
        default=Path("runs/repairs.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "runs/audits/cortex_f3a_executive_shadow_replay.json"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    repairs = (
        args.repairs
        if args.repairs.is_absolute()
        else root / args.repairs
    )
    output = (
        args.output
        if args.output.is_absolute()
        else root / args.output
    )
    payload = build_replay(
        root=root,
        repairs_path=repairs,
        revision=_git_revision(root),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(output)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
