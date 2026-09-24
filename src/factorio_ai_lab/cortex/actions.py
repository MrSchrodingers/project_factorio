"""Typed action ontology for the Cortex control plane.

The Cortex never executes an opaque "do something" string.  Every candidate
action is represented as an ActionRequest carrying provenance, evidence,
preconditions, predicted postconditions and the resources it requires/provides.

F2 begins in shadow mode.  These schemas therefore have to represent useful
actions before the system is allowed to mutate the world through them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from factorio_ai_lab.evidence import EvidenceStatus


class ActionFamily(StrEnum):
    """Stable action families exposed by the universal control plane."""

    PLACEMENT = "placement"
    DELIVERY = "delivery"
    RESUPPLY = "resupply"
    REBUILD = "rebuild"
    CRAFT = "craft"
    RESEARCH = "research"
    DEPENDENCY_PLAN = "dependency_plan"


class ActionAuthority(StrEnum):
    """How far a request may progress toward world mutation."""

    SHADOW = "shadow"
    PROPOSAL = "proposal"
    EXECUTE = "execute"


class ActionStatus(StrEnum):
    """Outcome of one pass through the UniversalExecutor."""

    SHADOWED = "shadowed"
    PROPOSED = "proposed"
    REFUSED = "refused"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class ConditionState(StrEnum):
    """Measured state of one pre/postcondition."""

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


class ConditionOperator(StrEnum):
    """How the world is expected to relate to a condition target."""

    EXISTS = "exists"
    EQUALS = "equals"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    INCREASE = "increase"
    DECREASE = "decrease"
    UNCHANGED = "unchanged"


REFUSAL_AUTHORITY_SHADOW = "authority_shadow_only"
REFUSAL_AUTHORITY_PROPOSAL = "authority_proposal_only"
REFUSAL_NO_BINDING = "no_action_binding"
REFUSAL_NO_HANDLER = "binding_has_no_execute_handler"
REFUSAL_PRECONDITION = "hard_precondition_not_satisfied"
REFUSAL_HANDLER_EXCEPTION = "action_handler_exception"


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to evidence that licensed or measured an action.

    A reference deliberately does not copy the payload itself.  It points to
    the source/path and records its epistemic status at decision time, so later
    replay can distinguish "observed zero" from "the probe never ran".
    """

    source: str
    path: str
    status: EvidenceStatus
    reason: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("evidence source must be non-empty")
        if not self.path.strip():
            raise ValueError("evidence path must be non-empty")
        if self.status in {EvidenceStatus.MISSING, EvidenceStatus.INVALID} and not self.reason:
            raise ValueError(f"{self.status.value} evidence reference requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "status": self.status.value,
            "reason": self.reason,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ActionCondition:
    """One explicit condition over a measured or predicted world quantity."""

    name: str
    operator: ConditionOperator
    state: ConditionState = ConditionState.UNKNOWN
    expected: Any = None
    hard: bool = True
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("condition name must be non-empty")

    @property
    def satisfied(self) -> bool:
        return self.state is ConditionState.SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operator": self.operator.value,
            "state": self.state.value,
            "expected": self.expected,
            "hard": self.hard,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True)
class ActionProvenance:
    """Who produced a request and which code/runtime lineage it belongs to."""

    requested_by: str
    source_component: str
    code_revision: str
    run_id: str | None = None
    generation: int | None = None
    parent_action_id: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("requested_by", self.requested_by),
            ("source_component", self.source_component),
            ("code_revision", self.code_revision),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_by": self.requested_by,
            "source_component": self.source_component,
            "code_revision": self.code_revision,
            "run_id": self.run_id,
            "generation": self.generation,
            "parent_action_id": self.parent_action_id,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class Refusal:
    """Named reason an action was not allowed to mutate the world."""

    code: str
    detail: str
    retriable: bool = False
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("refusal code must be non-empty")
        if not self.detail.strip():
            raise ValueError("refusal detail must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "retriable": self.retriable,
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True)
class ActionRequest:
    """One typed candidate action before authority is applied."""

    action_id: str
    family: ActionFamily
    intent: str
    provenance: ActionProvenance
    arguments: Mapping[str, Any] = field(default_factory=dict)
    targets: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    preconditions: tuple[ActionCondition, ...] = ()
    postconditions: tuple[ActionCondition, ...] = ()

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("action_id must be non-empty")
        if not self.intent.strip():
            raise ValueError("intent must be non-empty")
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "provides", tuple(self.provides))

    @property
    def blocking_preconditions(self) -> tuple[ActionCondition, ...]:
        return tuple(
            condition
            for condition in self.preconditions
            if condition.hard and not condition.satisfied
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "family": self.family.value,
            "intent": self.intent,
            "arguments": dict(self.arguments),
            "targets": list(self.targets),
            "requires": list(self.requires),
            "provides": list(self.provides),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "preconditions": [condition.to_dict() for condition in self.preconditions],
            "postconditions": [condition.to_dict() for condition in self.postconditions],
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class ActionResult:
    """Auditable result returned by the UniversalExecutor."""

    action_id: str
    family: ActionFamily
    intent: str
    status: ActionStatus
    authority: ActionAuthority
    changed_world: bool = False
    binding: str | None = None
    refusal: Refusal | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    postconditions: tuple[ActionCondition, ...] = ()
    measurements: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurements", dict(self.measurements))
        if self.status in {
            ActionStatus.SHADOWED,
            ActionStatus.PROPOSED,
            ActionStatus.REFUSED,
            ActionStatus.REJECTED,
            ActionStatus.FAILED,
        } and self.changed_world:
            raise ValueError(f"{self.status.value} result cannot claim world mutation")
        if self.status is ActionStatus.ACCEPTED:
            if self.authority is not ActionAuthority.EXECUTE:
                raise ValueError("accepted result requires execute authority")
            failed = [
                condition.name
                for condition in self.postconditions
                if condition.hard and not condition.satisfied
            ]
            if failed:
                raise ValueError(
                    "accepted result requires satisfied hard postconditions: "
                    + ", ".join(failed)
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "family": self.family.value,
            "intent": self.intent,
            "status": self.status.value,
            "authority": self.authority.value,
            "changed_world": self.changed_world,
            "binding": self.binding,
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "evidence": [ref.to_dict() for ref in self.evidence],
            "postconditions": [condition.to_dict() for condition in self.postconditions],
            "measurements": dict(self.measurements),
        }
