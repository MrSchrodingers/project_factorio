"""F3 executive shadow kernel.

This module makes decision structure explicit without introducing live
authority. It turns one goal-linked diagnosis into a candidate set, applies a
fail-closed hard-feasibility pass, asks an interchangeable policy to score the
surviving alternatives, and records the selected prediction before any
execution could occur.

F3-A is intentionally SHADOW-only. It imports no runtime executor and exposes
no method that can mutate Factorio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol

from factorio_ai_lab.cortex.actions import ActionAuthority, EvidenceRef
from factorio_ai_lab.learning.repair_loop import (
    Diagnosis,
    Prediction,
    RepairAction,
    RepairObservation,
    propose_actions,
    symptom_key,
)


class GoalKind(StrEnum):
    """Kinds of intent the executive may eventually coordinate."""

    REPAIR = "repair"
    ENGINEERING = "engineering"
    RESEARCH = "research"


class FeasibilityState(StrEnum):
    """Hard feasibility verdict for a candidate."""

    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


@dataclass(frozen=True)
class BeliefState:
    """Typed, bounded state presented to one executive decision.

    resource_availability is three-valued: True means available, False means
    unavailable, and None/missing means unknown. Unknown hard requirements
    fail closed in F3-A.
    """

    belief_id: str
    source: str
    tick: int | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    resource_availability: Mapping[str, bool | None] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.belief_id.strip():
            raise ValueError("belief_id must be non-empty")
        if not self.source.strip():
            raise ValueError("belief source must be non-empty")
        if self.tick is not None and self.tick < 0:
            raise ValueError("belief tick cannot be negative")
        object.__setattr__(self, "metrics", dict(self.metrics))
        object.__setattr__(
            self,
            "resource_availability",
            dict(self.resource_availability),
        )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "unknowns", tuple(self.unknowns))

    def to_dict(self) -> dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "source": self.source,
            "tick": self.tick,
            "metrics": dict(self.metrics),
            "resource_availability": dict(self.resource_availability),
            "evidence": [row.to_dict() for row in self.evidence],
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True)
class ExecutiveGoal:
    """One explicit objective frame on the goal stack."""

    goal_id: str
    objective: str
    kind: GoalKind
    priority: float = 0.0
    parent_goal_id: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal_id.strip():
            raise ValueError("goal_id must be non-empty")
        if not self.objective.strip():
            raise ValueError("goal objective must be non-empty")
        if not isfinite(float(self.priority)):
            raise ValueError("goal priority must be finite")
        if self.parent_goal_id is not None and not self.parent_goal_id.strip():
            raise ValueError("parent_goal_id must be non-empty when provided")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "objective": self.objective,
            "kind": self.kind.value,
            "priority": self.priority,
            "parent_goal_id": self.parent_goal_id,
            "evidence": [row.to_dict() for row in self.evidence],
        }


@dataclass(frozen=True)
class GoalStack:
    """Immutable goal stack; the last frame is the active objective."""

    goals: tuple[ExecutiveGoal, ...]

    def __post_init__(self) -> None:
        if not self.goals:
            raise ValueError("goal stack must contain at least one goal")
        object.__setattr__(self, "goals", tuple(self.goals))
        ids = [row.goal_id for row in self.goals]
        if len(ids) != len(set(ids)):
            raise ValueError("goal ids must be unique inside one stack")
        seen: set[str] = set()
        for goal in self.goals:
            if (
                goal.parent_goal_id is not None
                and goal.parent_goal_id not in seen
            ):
                raise ValueError(
                    "goal parent must precede the child inside the stack"
                )
            seen.add(goal.goal_id)

    @property
    def active(self) -> ExecutiveGoal:
        return self.goals[-1]

    def push(self, goal: ExecutiveGoal) -> GoalStack:
        return GoalStack(self.goals + (goal,))

    def without_active(self) -> GoalStack | None:
        if len(self.goals) == 1:
            return None
        return GoalStack(self.goals[:-1])

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_goal_id": self.active.goal_id,
            "goals": [row.to_dict() for row in self.goals],
        }


@dataclass(frozen=True)
class GoalDiagnosis:
    """One diagnosed deficit explicitly attached to an executive goal."""

    goal_id: str
    diagnosis: Diagnosis
    basis: str
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal_id.strip():
            raise ValueError("diagnosed goal_id must be non-empty")
        if not self.basis.strip():
            raise ValueError("goal diagnosis basis must be non-empty")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    @property
    def symptom(self) -> str:
        return symptom_key(self.diagnosis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "diagnosis": self.diagnosis.to_dict(),
            "symptom": self.symptom,
            "basis": self.basis,
            "evidence": [row.to_dict() for row in self.evidence],
        }


@dataclass(frozen=True)
class ExecutiveCandidate:
    """One alternative kept visible until policy selection."""

    candidate_id: str
    goal_id: str
    symptom: str
    action: RepairAction
    origin: str = "repair_loop"

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must be non-empty")
        if not self.goal_id.strip():
            raise ValueError("candidate goal_id must be non-empty")
        if not self.symptom.strip():
            raise ValueError("candidate symptom must be non-empty")
        if not self.origin.strip():
            raise ValueError("candidate origin must be non-empty")

    @property
    def prediction(self) -> Prediction:
        return self.action.prediction

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "goal_id": self.goal_id,
            "symptom": self.symptom,
            "action": self.action.to_dict(),
            "prediction_before_action": self.prediction.to_dict(),
            "origin": self.origin,
        }


@dataclass(frozen=True)
class CandidateGenerationResult:
    """All alternatives for one diagnosed goal, or one named refusal."""

    goal_id: str
    symptom: str
    candidates: tuple[ExecutiveCandidate, ...] = ()
    refusal: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not self.goal_id.strip() or not self.symptom.strip():
            raise ValueError("candidate generation requires goal_id and symptom")
        if bool(self.candidates) == bool(self.refusal):
            raise ValueError(
                "candidate generation requires exactly candidates or refusal"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "symptom": self.symptom,
            "candidates": [row.to_dict() for row in self.candidates],
            "refusal": self.refusal,
        }


def generate_repair_candidates(
    goal: ExecutiveGoal,
    diagnosis: GoalDiagnosis,
    observation: RepairObservation,
) -> CandidateGenerationResult:
    """Expand every repair alternative instead of choosing one inside the planner."""

    if diagnosis.goal_id != goal.goal_id:
        raise ValueError("goal diagnosis does not belong to active goal")
    proposal = propose_actions(diagnosis.diagnosis, observation)
    if proposal.refusal is not None or not proposal.candidates:
        return CandidateGenerationResult(
            goal_id=goal.goal_id,
            symptom=diagnosis.symptom,
            refusal=proposal.refusal or "no_candidates",
        )
    candidates = tuple(
        ExecutiveCandidate(
            candidate_id=(
                f"{goal.goal_id}:candidate:{index}:{action.tool}:{action.intent}"
            ),
            goal_id=goal.goal_id,
            symptom=diagnosis.symptom,
            action=action,
        )
        for index, action in enumerate(proposal.candidates)
    )
    return CandidateGenerationResult(
        goal_id=goal.goal_id,
        symptom=diagnosis.symptom,
        candidates=candidates,
    )


@dataclass(frozen=True)
class FeasibilityAssessment:
    """Fail-closed hard-feasibility verdict for one candidate."""

    candidate_id: str
    state: FeasibilityState
    checked_requirements: Mapping[str, bool | None] = field(default_factory=dict)
    hard_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("feasibility candidate_id must be non-empty")
        object.__setattr__(
            self,
            "checked_requirements",
            dict(self.checked_requirements),
        )
        object.__setattr__(self, "hard_failures", tuple(self.hard_failures))
        if self.state is FeasibilityState.FEASIBLE and self.hard_failures:
            raise ValueError("feasible assessment cannot have hard failures")
        if self.state is FeasibilityState.INFEASIBLE and not self.hard_failures:
            raise ValueError("infeasible assessment requires a hard failure")

    @property
    def feasible(self) -> bool:
        return self.state is FeasibilityState.FEASIBLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "state": self.state.value,
            "feasible": self.feasible,
            "checked_requirements": dict(self.checked_requirements),
            "hard_failures": list(self.hard_failures),
        }


def assess_hard_feasibility(
    candidate: ExecutiveCandidate,
    belief: BeliefState,
) -> FeasibilityAssessment:
    """Reject unavailable and unknown hard requirements."""

    checked: dict[str, bool | None] = {}
    failures: list[str] = []
    for requirement in candidate.action.requires:
        availability = belief.resource_availability.get(requirement)
        checked[requirement] = availability
        if availability is True:
            continue
        if availability is False:
            failures.append(f"resource_unavailable:{requirement}")
        else:
            failures.append(f"resource_unknown:{requirement}")
    state = (
        FeasibilityState.INFEASIBLE
        if failures
        else FeasibilityState.FEASIBLE
    )
    return FeasibilityAssessment(
        candidate_id=candidate.candidate_id,
        state=state,
        checked_requirements=checked,
        hard_failures=tuple(failures),
    )


class ChoicePolicy(Protocol):
    """Interface between executive state and rule/learned policies."""

    policy_id: str

    def score(
        self,
        *,
        goal: ExecutiveGoal,
        belief: BeliefState,
        candidate: ExecutiveCandidate,
    ) -> float | None:
        """Return a comparable score or None when the policy cannot score."""


@dataclass(frozen=True)
class ScoreTablePolicy:
    """Deterministic policy used as the F3-A controllable shadow baseline."""

    policy_id: str
    scores: Mapping[str, float]
    default_score: float | None = None

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        clean = {str(key): float(value) for key, value in self.scores.items()}
        if any(not isfinite(value) for value in clean.values()):
            raise ValueError("policy scores must be finite")
        if self.default_score is not None and not isfinite(
            float(self.default_score)
        ):
            raise ValueError("default policy score must be finite")
        object.__setattr__(self, "scores", clean)

    def score(
        self,
        *,
        goal: ExecutiveGoal,
        belief: BeliefState,
        candidate: ExecutiveCandidate,
    ) -> float | None:
        del goal, belief
        if candidate.candidate_id in self.scores:
            return float(self.scores[candidate.candidate_id])
        if candidate.action.key in self.scores:
            return float(self.scores[candidate.action.key])
        return self.default_score


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    action_key: str
    feasible: bool
    score: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action_key": self.action_key,
            "feasible": self.feasible,
            "score": self.score,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ChoiceDecision:
    """Observable result of policy selection in SHADOW."""

    policy_id: str
    selected_candidate_id: str | None
    selected_action_key: str | None
    prediction_before_action: Prediction | None
    scores: tuple[CandidateScore, ...]
    refusal: str | None = None
    authority: ActionAuthority = ActionAuthority.SHADOW

    def __post_init__(self) -> None:
        object.__setattr__(self, "scores", tuple(self.scores))
        if self.authority is not ActionAuthority.SHADOW:
            raise ValueError("F3-A choice decisions are SHADOW-only")
        selected = self.selected_candidate_id is not None
        if selected != (self.selected_action_key is not None):
            raise ValueError("selected candidate/action fields must agree")
        if selected != (self.prediction_before_action is not None):
            raise ValueError("selected choice requires a pre-action prediction")
        if selected == bool(self.refusal):
            raise ValueError("choice requires exactly selection or refusal")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "authority": self.authority.value,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_action_key": self.selected_action_key,
            "prediction_before_action": (
                None
                if self.prediction_before_action is None
                else self.prediction_before_action.to_dict()
            ),
            "scores": [row.to_dict() for row in self.scores],
            "refusal": self.refusal,
            "world_mutation": False,
            "execute_authorized": False,
        }


def choose_shadow_candidate(
    *,
    goal: ExecutiveGoal,
    belief: BeliefState,
    candidates: tuple[ExecutiveCandidate, ...],
    feasibility: tuple[FeasibilityAssessment, ...],
    policy: ChoicePolicy,
) -> ChoiceDecision:
    """Score feasible candidates and expose the whole ranking."""

    by_id = {row.candidate_id: row for row in feasibility}
    if set(by_id) != {row.candidate_id for row in candidates}:
        raise ValueError("feasibility rows must cover the exact candidate set")

    scored: list[CandidateScore] = []
    eligible: list[tuple[float, int, ExecutiveCandidate]] = []
    for index, candidate in enumerate(candidates):
        assessment = by_id[candidate.candidate_id]
        if not assessment.feasible:
            scored.append(
                CandidateScore(
                    candidate_id=candidate.candidate_id,
                    action_key=candidate.action.key,
                    feasible=False,
                    score=None,
                    reason="hard_feasibility_rejected",
                )
            )
            continue
        score = policy.score(
            goal=goal,
            belief=belief,
            candidate=candidate,
        )
        scored.append(
            CandidateScore(
                candidate_id=candidate.candidate_id,
                action_key=candidate.action.key,
                feasible=True,
                score=score,
                reason="policy_scored" if score is not None else "policy_unscored",
            )
        )
        if score is not None:
            eligible.append((float(score), -index, candidate))

    if not eligible:
        return ChoiceDecision(
            policy_id=policy.policy_id,
            selected_candidate_id=None,
            selected_action_key=None,
            prediction_before_action=None,
            scores=tuple(scored),
            refusal="no_feasible_scored_candidate",
        )

    _, _, selected = max(eligible, key=lambda row: (row[0], row[1]))
    return ChoiceDecision(
        policy_id=policy.policy_id,
        selected_candidate_id=selected.candidate_id,
        selected_action_key=selected.action.key,
        prediction_before_action=selected.prediction,
        scores=tuple(scored),
    )


@dataclass(frozen=True)
class ExecutiveShadowRun:
    """One complete F3-A choice episode with no world-mutation surface."""

    run_id: str
    belief: BeliefState
    goal_stack: GoalStack
    goal_diagnosis: GoalDiagnosis
    generation: CandidateGenerationResult
    feasibility: tuple[FeasibilityAssessment, ...]
    decision: ChoiceDecision
    authority: ActionAuthority = ActionAuthority.SHADOW

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("executive shadow run_id must be non-empty")
        object.__setattr__(self, "feasibility", tuple(self.feasibility))
        if self.authority is not ActionAuthority.SHADOW:
            raise ValueError("F3-A executive run is SHADOW-only")
        if self.goal_stack.active.goal_id != self.goal_diagnosis.goal_id:
            raise ValueError("diagnosis must belong to active goal")
        if self.generation.goal_id != self.goal_stack.active.goal_id:
            raise ValueError("candidate set must belong to active goal")

    @property
    def world_mutation(self) -> bool:
        return False

    @property
    def execute_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "cortex_f3a_executive_shadow_run_v1",
            "run_id": self.run_id,
            "authority": self.authority.value,
            "belief": self.belief.to_dict(),
            "goal_stack": self.goal_stack.to_dict(),
            "goal_diagnosis": self.goal_diagnosis.to_dict(),
            "generation": self.generation.to_dict(),
            "feasibility": [row.to_dict() for row in self.feasibility],
            "decision": self.decision.to_dict(),
            "world_mutation": False,
            "execute_authorized": False,
            "continuous_authority": False,
        }


def run_shadow_executive(
    *,
    run_id: str,
    belief: BeliefState,
    goal_stack: GoalStack,
    goal_diagnosis: GoalDiagnosis,
    observation: RepairObservation,
    policy: ChoicePolicy,
) -> ExecutiveShadowRun:
    """Run the complete F3-A executive kernel without execution authority."""

    goal = goal_stack.active
    generation = generate_repair_candidates(
        goal,
        goal_diagnosis,
        observation,
    )
    if not generation.candidates:
        feasibility: tuple[FeasibilityAssessment, ...] = ()
        decision = ChoiceDecision(
            policy_id=policy.policy_id,
            selected_candidate_id=None,
            selected_action_key=None,
            prediction_before_action=None,
            scores=(),
            refusal=generation.refusal or "candidate_generation_refused",
        )
    else:
        feasibility = tuple(
            assess_hard_feasibility(candidate, belief)
            for candidate in generation.candidates
        )
        decision = choose_shadow_candidate(
            goal=goal,
            belief=belief,
            candidates=generation.candidates,
            feasibility=feasibility,
            policy=policy,
        )
    return ExecutiveShadowRun(
        run_id=run_id,
        belief=belief,
        goal_stack=goal_stack,
        goal_diagnosis=goal_diagnosis,
        generation=generation,
        feasibility=feasibility,
        decision=decision,
    )
