"""F4-C paired causal harness.

The harness owns experimental invariants, not task strategy. A concrete world
adapter receives the same frozen task and budget in both arms; the only
condition-specific object is MemoryAccess. This lets preflight validation
exercise checkpoint restore, arm isolation, retrieval-only ablation, budgets,
missingness and outcome extraction without creating a Factorio environment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from factorio_ai_lab.cortex.causal_protocol import MEMORY_ABLATED, MEMORY_ON
from factorio_ai_lab.cortex.memory_retrieval import (
    RETRIEVAL_POLICY_VERSION,
    MemoryDatabaseSnapshot,
    MemoryQuery,
    MemoryRecord,
    RetrievalResult,
    retrieve_memories,
)

SCHEMA_VERSION = "cortex_f4c_paired_harness_v1"
OUTCOME_EXTRACTOR_VERSION = "cortex_f4c_outcome_v1"
ABLATION_POLICY_VERSION = "cortex_f4c_empty_retrieval_ablation_v1"


class HarnessValidationError(ValueError):
    """Raised when a frozen harness invariant cannot be represented safely."""


@dataclass(frozen=True)
class HarnessBudget:
    max_decisions: int
    max_actions: int
    max_game_ticks: int
    max_wall_clock_seconds: float
    max_llm_calls: int
    max_retrieval_queries: int

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> HarnessBudget:
        raw = manifest.get("budgets")
        if not isinstance(raw, dict):
            raise HarnessValidationError("protocol budgets missing")
        budget = cls(
            max_decisions=int(raw["max_decisions"]),
            max_actions=int(raw["max_actions"]),
            max_game_ticks=int(raw["max_game_ticks"]),
            max_wall_clock_seconds=float(raw["max_wall_clock_seconds"]),
            max_llm_calls=int(raw["max_llm_calls"]),
            max_retrieval_queries=int(raw["max_retrieval_queries"]),
        )
        if any(value <= 0 for value in asdict(budget).values()):
            raise HarnessValidationError("all harness budgets must be positive")
        return budget

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True)
class ArmObservation:
    hard_postconditions: dict[str, bool | None]
    action_count: int | None
    observed_game_ticks: int | None
    invalid_or_refused_actions: int | None
    proposed_actions: int | None
    initially_unsatisfied: bool | None
    decisions: int | None
    wall_clock_seconds: float | None
    llm_calls: int | None
    candidate_surface: tuple[str, ...]
    tool_surface: tuple[str, ...]
    outcome_extractor_version: str = OUTCOME_EXTRACTOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidate_surface": list(self.candidate_surface),
            "tool_surface": list(self.tool_surface),
        }


@dataclass(frozen=True)
class ArmScore:
    valid: bool
    invalid_reasons: tuple[str, ...]
    functional_success: float | None
    goal_progress: float | None
    normalized_resource_cost: float | None
    invalid_action_rate: float | None
    j: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "invalid_reasons": list(self.invalid_reasons),
        }


@dataclass
class MemoryAccess:
    condition: str
    records: tuple[MemoryRecord, ...]
    max_queries: int
    query_count: int = 0
    retrievals: list[dict[str, Any]] = field(default_factory=list)
    quarantine: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.condition not in {MEMORY_ON, MEMORY_ABLATED}:
            raise HarnessValidationError(f"unknown memory condition: {self.condition}")
        if self.max_queries <= 0:
            raise HarnessValidationError("max_queries must be positive")

    def retrieve(self, query: MemoryQuery) -> RetrievalResult:
        self.query_count += 1
        if self.query_count > self.max_queries:
            raise HarnessValidationError("retrieval query budget exceeded")
        if self.condition == MEMORY_ON:
            result = retrieve_memories(self.records, query)
            mode = "frozen_f4b_snapshot"
        else:
            result = RetrievalResult(
                query=query,
                records_considered=len(self.records),
                records_compatible=0,
                results=(),
                policy_version=RETRIEVAL_POLICY_VERSION,
            )
            mode = "empty_retrieval_result"
        self.retrievals.append(
            {
                "condition": self.condition,
                "mode": mode,
                "ablation_policy_version": (
                    ABLATION_POLICY_VERSION
                    if self.condition == MEMORY_ABLATED
                    else None
                ),
                "result": result.to_dict(),
            }
        )
        return result

    def quarantine_write(self, payload: dict[str, Any]) -> None:
        self.quarantine.append(deepcopy(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "query_count": self.query_count,
            "retrievals": deepcopy(self.retrievals),
            "quarantined_writes": deepcopy(self.quarantine),
            "source_write_attempted": False,
        }


class PairedWorldAdapter(Protocol):
    """Task adapter contract used by the causal harness."""

    def capture_checkpoint(self) -> Any: ...
    def restore_checkpoint(self, checkpoint: Any) -> None: ...
    def state_digest(self) -> str: ...
    def run_arm(
        self,
        task: dict[str, Any],
        memory: MemoryAccess,
        budget: HarnessBudget,
    ) -> ArmObservation: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def checkpoint_digest(checkpoint: Any) -> str:
    to_raw = getattr(checkpoint, "to_raw", None)
    if callable(to_raw):
        raw = to_raw()
        if not isinstance(raw, str) or not raw.strip():
            raise HarnessValidationError("checkpoint.to_raw() returned invalid data")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        else:
            payload = _canonical_json(value)
    else:
        try:
            payload = _canonical_json(checkpoint)
        except (TypeError, ValueError) as exc:
            raise HarnessValidationError(
                "checkpoint must be JSON-serializable or expose to_raw()"
            ) from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score_observation(
    task: dict[str, Any],
    observation: ArmObservation,
    budget: HarnessBudget,
) -> ArmScore:
    reasons: list[str] = []
    spec = task.get("spec")
    expected = spec.get("hard_postconditions") if isinstance(spec, dict) else None
    if not isinstance(expected, list) or not expected:
        raise HarnessValidationError("task hard_postconditions missing")

    values: list[bool] = []
    for name in expected:
        value = observation.hard_postconditions.get(str(name))
        if value is None or not isinstance(value, bool):
            reasons.append(f"missing_postcondition:{name}")
        else:
            values.append(value)

    required_counts = {
        "action_count": observation.action_count,
        "observed_game_ticks": observation.observed_game_ticks,
        "invalid_or_refused_actions": observation.invalid_or_refused_actions,
        "proposed_actions": observation.proposed_actions,
        "decisions": observation.decisions,
        "wall_clock_seconds": observation.wall_clock_seconds,
        "llm_calls": observation.llm_calls,
    }
    for name, value in required_counts.items():
        if value is None:
            reasons.append(f"missing_primary_component:{name}")
        elif (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
        ):
            reasons.append(f"invalid_primary_component:{name}")

    if observation.initially_unsatisfied is None:
        reasons.append("missing_primary_component:initially_unsatisfied")
    elif not isinstance(observation.initially_unsatisfied, bool):
        reasons.append("invalid_primary_component:initially_unsatisfied")

    if observation.outcome_extractor_version != OUTCOME_EXTRACTOR_VERSION:
        reasons.append("outcome_extractor_version_mismatch")

    if reasons:
        return ArmScore(
            False,
            tuple(sorted(set(reasons))),
            None,
            None,
            None,
            None,
            None,
        )

    assert observation.action_count is not None
    assert observation.observed_game_ticks is not None
    assert observation.invalid_or_refused_actions is not None
    assert observation.proposed_actions is not None
    assert observation.decisions is not None
    assert observation.wall_clock_seconds is not None
    assert observation.llm_calls is not None
    assert observation.initially_unsatisfied is not None

    limits = (
        ("decisions", float(observation.decisions), float(budget.max_decisions)),
        ("actions", float(observation.action_count), float(budget.max_actions)),
        (
            "game_ticks",
            float(observation.observed_game_ticks),
            float(budget.max_game_ticks),
        ),
        (
            "wall_clock_seconds",
            float(observation.wall_clock_seconds),
            float(budget.max_wall_clock_seconds),
        ),
        ("llm_calls", float(observation.llm_calls), float(budget.max_llm_calls)),
    )
    for name, value, limit in limits:
        if value > limit:
            reasons.append(f"budget_overrun:{name}")

    if observation.invalid_or_refused_actions > observation.proposed_actions:
        reasons.append("invalid_actions_exceed_proposed_actions")
    if observation.action_count > observation.proposed_actions:
        reasons.append("action_count_exceeds_proposed_actions")
    if reasons:
        return ArmScore(
            False,
            tuple(sorted(set(reasons))),
            None,
            None,
            None,
            None,
            None,
        )

    satisfied = sum(bool(value) for value in values)
    total = len(expected)
    functional_success = 1.0 if satisfied == total else 0.0
    goal_progress = satisfied / total
    normalized_resource_cost = (
        0.5 * min(1.0, observation.action_count / budget.max_actions)
        + 0.5
        * min(1.0, observation.observed_game_ticks / budget.max_game_ticks)
    )
    if observation.proposed_actions == 0:
        invalid_action_rate = 1.0 if observation.initially_unsatisfied else 0.0
    else:
        invalid_action_rate = (
            observation.invalid_or_refused_actions / observation.proposed_actions
        )
    j = (
        0.55 * functional_success
        + 0.25 * goal_progress
        + 0.10 * (1.0 - normalized_resource_cost)
        + 0.10 * (1.0 - invalid_action_rate)
    )
    if not 0.0 <= j <= 1.0:
        raise HarnessValidationError(f"computed J outside [0,1]: {j}")
    return ArmScore(
        True,
        (),
        functional_success,
        goal_progress,
        normalized_resource_cost,
        invalid_action_rate,
        j,
    )


def _snapshot_dict(snapshot: MemoryDatabaseSnapshot) -> dict[str, Any]:
    return snapshot.to_dict()


def execute_pair(
    *,
    task: dict[str, Any],
    first_condition: str,
    second_condition: str,
    adapter: PairedWorldAdapter,
    memory_records: Iterable[MemoryRecord],
    memory_snapshot: Callable[[], MemoryDatabaseSnapshot],
    budget: HarnessBudget,
) -> dict[str, Any]:
    if {first_condition, second_condition} != {MEMORY_ON, MEMORY_ABLATED}:
        raise HarnessValidationError(
            "paired conditions must be MEMORY ON and MEMORY ABLATED"
        )

    records = tuple(memory_records)
    before = memory_snapshot()
    checkpoint = deepcopy(adapter.capture_checkpoint())
    checkpoint_sha = checkpoint_digest(checkpoint)
    technical_invalidities: list[str] = []
    arms: dict[str, dict[str, Any]] = {}

    for order, condition in enumerate((first_condition, second_condition), start=1):
        try:
            adapter.restore_checkpoint(deepcopy(checkpoint))
            restored_sha = adapter.state_digest()
        except Exception as exc:  # noqa: BLE001 - adapter boundary is fail-closed
            technical_invalidities.append(
                f"{condition}:checkpoint_restore_error:{type(exc).__name__}"
            )
            break
        if restored_sha != checkpoint_sha:
            technical_invalidities.append(
                f"{condition}:checkpoint_restore_mismatch"
            )
            break

        access = MemoryAccess(
            condition=condition,
            records=records,
            max_queries=budget.max_retrieval_queries,
        )
        try:
            observation = adapter.run_arm(deepcopy(task), access, budget)
            score = score_observation(task, observation, budget)
        except Exception as exc:  # noqa: BLE001 - adapter boundary is fail-closed
            technical_invalidities.append(
                f"{condition}:arm_execution_error:{type(exc).__name__}"
            )
            break
        if not score.valid:
            technical_invalidities.extend(
                f"{condition}:{reason}" for reason in score.invalid_reasons
            )

        source_after_arm = memory_snapshot()
        if source_after_arm != before:
            technical_invalidities.append(
                f"{condition}:source_memory_manifest_mismatch"
            )

        arms[condition] = {
            "order": order,
            "checkpoint_digest_at_start": restored_sha,
            "budget": budget.to_dict(),
            "memory": access.to_dict(),
            "observation": observation.to_dict(),
            "score": score.to_dict(),
        }

    after = memory_snapshot()
    if after != before:
        technical_invalidities.append("source_memory_manifest_mismatch")

    if len(arms) == 2:
        on = arms[MEMORY_ON]
        ablated = arms[MEMORY_ABLATED]
        if on["budget"] != ablated["budget"]:
            technical_invalidities.append("matched_budget_mismatch")
        for field_name in (
            "candidate_surface",
            "tool_surface",
            "outcome_extractor_version",
        ):
            if (
                on["observation"][field_name]
                != ablated["observation"][field_name]
            ):
                technical_invalidities.append(
                    f"cross_arm_surface_mismatch:{field_name}"
                )
        if (
            on["checkpoint_digest_at_start"] != checkpoint_sha
            or ablated["checkpoint_digest_at_start"] != checkpoint_sha
        ):
            technical_invalidities.append("checkpoint_restore_mismatch")

    technical_invalidities = sorted(set(technical_invalidities))
    valid = not technical_invalidities and len(arms) == 2
    delta_j = None
    if valid:
        j_on = arms[MEMORY_ON]["score"]["j"]
        j_ablated = arms[MEMORY_ABLATED]["score"]["j"]
        if j_on is None or j_ablated is None:
            valid = False
            technical_invalidities.append("missing_primary_component:J")
        else:
            delta_j = float(j_on) - float(j_ablated)

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task.get("task_id"),
        "task_fingerprint": hashlib.sha256(
            _canonical_json(task).encode("utf-8")
        ).hexdigest(),
        "first_condition": first_condition,
        "second_condition": second_condition,
        "checkpoint_digest": checkpoint_sha,
        "source_memory_before": _snapshot_dict(before),
        "source_memory_after": _snapshot_dict(after),
        "arms": arms,
        "technical_invalidities": technical_invalidities,
        "valid": valid,
        "delta_J": delta_j,
    }


def recompute_pair_delta(pair: dict[str, Any]) -> float | None:
    if not pair.get("valid"):
        return None
    arms = pair.get("arms")
    if not isinstance(arms, dict):
        raise HarnessValidationError("pair arms missing")
    try:
        j_on = float(arms[MEMORY_ON]["score"]["j"])
        j_ablated = float(arms[MEMORY_ABLATED]["score"]["j"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HarnessValidationError("pair J values missing") from exc
    return j_on - j_ablated
