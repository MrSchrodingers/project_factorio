"""Held-out F4-C evaluation boundary over frozen pilot semantics.

This module MUST NOT change the treatment policy or candidate executors.  It
adapts frozen evaluation tasks to the already validated protocol task-world
runtime by changing only the internal authority partition to runner_preflight.
The task passed to execute_pair remains the original frozen evaluation task, so
its fingerprint and seed identity are preserved in primary evidence.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    OUTCOME_EXTRACTOR_VERSION,
    ArmObservation,
    HarnessBudget,
    HarnessValidationError,
    MemoryAccess,
    checkpoint_digest,
)
from factorio_ai_lab.cortex.causal_pilot_runtime import (
    FAMILY_TOOL_SURFACES,
    PROTOCOL_EXECUTORS,
    execute_candidate,
    runner_validation_tasks,
)
from factorio_ai_lab.cortex.causal_treatment import (
    rank_candidates,
    task_query_text,
    treatment_retrieval_limit,
)
from factorio_ai_lab.cortex.memory import ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import MemoryQuery

EVALUATION_BOUNDARY_VERSION = "cortex_f4c_heldout_evaluation_boundary_v1"
ALLOWED_EVALUATION_PARTITIONS = frozenset(
    {"evaluation", "evaluation_preflight"}
)


def _validate_task(task: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    partition = task.get("partition")
    if partition not in ALLOWED_EVALUATION_PARTITIONS:
        raise HarnessValidationError(
            "evaluation boundary accepts evaluation or evaluation_preflight only"
        )
    if partition == "evaluation":
        seed = task.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise HarnessValidationError("evaluation task requires integer seed")
    elif task.get("seed") is not None:
        raise HarnessValidationError(
            "evaluation_preflight must not carry an experimental seed"
        )

    family = str(task.get("family") or "")
    if family not in FAMILY_TOOL_SURFACES:
        raise HarnessValidationError(f"unknown evaluation family: {family}")
    spec = task.get("spec")
    if not isinstance(spec, dict):
        raise HarnessValidationError("evaluation task spec missing")
    candidates = tuple(map(str, spec.get("candidate_classes") or ()))
    if not candidates:
        raise HarnessValidationError("evaluation candidate surface missing")
    if any(candidate not in PROTOCOL_EXECUTORS for candidate in candidates):
        raise HarnessValidationError(
            "evaluation candidate surface is not executable by frozen runtime"
        )
    return family, candidates


def _delegate_task(task: dict[str, Any]) -> dict[str, Any]:
    clone = deepcopy(task)
    clone["partition"] = "runner_preflight"
    clone["seed"] = None
    return clone


class HeldoutEvaluationAdapter:
    """Paired adapter preserving held-out task identity and frozen semantics."""

    def __init__(self, task: dict[str, Any]) -> None:
        family, _ = _validate_task(task)
        self.task = deepcopy(task)
        self.state: dict[str, Any] = {
            "boundary_version": EVALUATION_BOUNDARY_VERSION,
            "task_id": self.task.get("task_id"),
            "family": family,
            "arm_history": [],
        }
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if not isinstance(checkpoint, dict):
            raise HarnessValidationError("evaluation checkpoint must be mapping")
        if checkpoint.get("task_id") != self.task.get("task_id"):
            raise HarnessValidationError("evaluation checkpoint task mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def run_arm(
        self,
        task: dict[str, Any],
        memory: MemoryAccess,
        budget: HarnessBudget,
    ) -> ArmObservation:
        if task != self.task:
            raise HarnessValidationError(
                "evaluation adapter task differs from frozen task"
            )
        family, candidates = _validate_task(task)
        spec = task["spec"]
        expected_hard = tuple(map(str, spec.get("hard_postconditions") or ()))
        if not expected_hard:
            raise HarnessValidationError("hard-postcondition surface missing")

        self.arm_start_digests.append(self.state_digest())
        retrieval = memory.retrieve(
            MemoryQuery(
                query_id=f"evaluation:{task['task_id']}:{memory.condition}",
                text=task_query_text(task),
                scope=ValidityScope(),
                limit=treatment_retrieval_limit(candidates),
            )
        )
        decision = rank_candidates(
            candidates,
            retrieval.to_dict()["results"],
        )
        if decision.selected is None:
            raise HarnessValidationError("evaluation treatment selected nothing")

        result = execute_candidate(
            _delegate_task(task),
            decision.selected,
            budget,
        )
        if set(result.hard_postconditions) != set(expected_hard):
            raise HarnessValidationError(
                "evaluation hard-postcondition surface mismatch"
            )

        memory.quarantine_write(
            {
                "kind": "f4c_evaluation_arm_trace",
                "boundary_version": EVALUATION_BOUNDARY_VERSION,
                "task_id": task["task_id"],
                "condition": memory.condition,
                "treatment": decision.to_dict(),
                "candidate_result": result.to_dict(),
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "selected": decision.selected,
                "memory_influence": decision.memory_influence,
            }
        )
        return ArmObservation(
            hard_postconditions=dict(result.hard_postconditions),
            action_count=result.action_count,
            observed_game_ticks=result.observed_game_ticks,
            invalid_or_refused_actions=result.invalid_or_refused_actions,
            proposed_actions=result.proposed_actions,
            initially_unsatisfied=True,
            decisions=result.decisions,
            wall_clock_seconds=0.0,
            llm_calls=0,
            candidate_surface=candidates,
            tool_surface=FAMILY_TOOL_SURFACES[family],
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )


def evaluation_task_from_manifest(
    manifest: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    seeds = tuple(
        int(value)
        for value in manifest["seed_partitions"]["evaluation"]
    )
    if int(seed) not in seeds:
        raise HarnessValidationError(
            f"seed {seed} is not in the frozen evaluation partition"
        )
    tasks = manifest["task_design"]["evaluation_tasks"]
    matches = [task for task in tasks if int(task["seed"]) == int(seed)]
    if len(matches) != 1:
        raise HarnessValidationError(
            f"evaluation seed {seed} maps to {len(matches)} tasks"
        )
    task = deepcopy(matches[0])
    if task.get("partition") != "evaluation":
        raise HarnessValidationError("frozen evaluation task has wrong partition")
    return task


def evaluation_arm_order(
    manifest: dict[str, Any],
    task: dict[str, Any],
) -> tuple[str, str]:
    seed = int(task["seed"])
    task_id = str(task["task_id"])
    schedule = manifest["counterbalancing"]["schedule"]
    matches = [
        row
        for row in schedule
        if int(row["seed"]) == seed and row["task_id"] == task_id
    ]
    if len(matches) != 1:
        raise HarnessValidationError(
            "evaluation task has no unique frozen counterbalancing row"
        )
    row = matches[0]
    if row["family"] != task["family"]:
        raise HarnessValidationError(
            "evaluation counterbalancing family mismatch"
        )
    return str(row["first_condition"]), str(row["second_condition"])


def evaluation_preflight_tasks() -> tuple[dict[str, Any], ...]:
    rows = []
    for task in runner_validation_tasks():
        clone = deepcopy(task)
        clone["task_id"] = (
            "evaluation-preflight:"
            + str(task["family"])
            + ":independent-v1"
        )
        clone["partition"] = "evaluation_preflight"
        clone["seed"] = None
        rows.append(clone)
    return tuple(rows)


def evaluation_sequence_guard(
    root: Path,
    evaluation_sequence: tuple[int, ...] | list[int],
    seed: int,
) -> dict[str, Any]:
    """Require every earlier fixed seed to be reviewed, without replacement."""

    sequence = tuple(int(value) for value in evaluation_sequence)
    target = int(seed)
    if target not in sequence:
        raise HarnessValidationError(
            f"seed {target} is not in evaluation sequence"
        )
    index = sequence.index(target)
    prior_reviews: list[dict[str, Any]] = []
    for prior in sequence[:index]:
        review_path = (
            root / "runs" / "f4c_evaluation" / str(prior) / "review.json"
        )
        if not review_path.exists():
            raise HarnessValidationError(
                f"prior evaluation {prior} has no canonical review"
            )
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessValidationError(
                f"prior evaluation {prior} review is unreadable"
            ) from exc
        if (
            not isinstance(review, dict)
            or int(review.get("seed", -1)) != prior
            or review.get("status") not in {"pass", "technical_invalid"}
        ):
            raise HarnessValidationError(
                f"prior evaluation {prior} review blocks continuation"
            )
        prior_reviews.append(
            {
                "seed": prior,
                "status": review["status"],
                "review_path": str(review_path),
            }
        )

    unexpected_later = [
        later
        for later in sequence[index + 1 :]
        if (
            root
            / "runs"
            / "f4c_evaluation"
            / str(later)
            / "pair.json"
        ).exists()
    ]
    if unexpected_later:
        raise HarnessValidationError(
            "future evaluation evidence exists out of order: "
            + ",".join(map(str, unexpected_later))
        )
    return {
        "seed": target,
        "position": index + 1,
        "total": len(sequence),
        "prior_reviews": prior_reviews,
        "future_artifacts_absent": True,
    }
