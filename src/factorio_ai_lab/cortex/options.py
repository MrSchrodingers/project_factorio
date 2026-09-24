"""Typed temporally extended options for the Cortex control plane.

F2-G2 introduces option planning only. Options compose existing pure planners
and dependency completers; they do not execute Factorio or grant persistent
authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionProvenance,
    ActionRequest,
    EvidenceRef,
    Refusal,
)
from factorio_ai_lab.cortex.delivery_actuator_dependency import (
    DeliveryActuatorDependency,
    complete_delivery_actuator_dependency,
)
from factorio_ai_lab.cortex.functional_dependency import (
    FuelDependency,
    complete_structural_dependencies,
)
from factorio_ai_lab.cortex.structural import (
    ProcessingBranch,
    plan_processing_for_buffered_output,
)
from factorio_ai_lab.cortex.structural_execute import (
    execution_guard_conditions,
)
from factorio_ai_lab.cortex.structural_prepare import (
    PreparedStructuralAction,
    prepare_structural_branch,
)
from factorio_ai_lab.planning.delivery import DEFAULT_BELT_BUDGET
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.placement import ResourceSurvey
from factorio_ai_lab.planning.resupply import FuelSource
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

REFUSAL_OPTION_EXECUTE_NOT_AUTHORIZED = "option_execute_not_authorized_f2g2"
REFUSAL_OPTION_NO_BRANCH = "option_no_processing_branch"
REFUSAL_OPTION_BRANCH_AMBIGUOUS = "option_processing_branch_ambiguous"
REFUSAL_OPTION_PROVENANCE_MISMATCH = "option_provenance_mismatch"


class OptionKind(StrEnum):
    """Stable temporally extended behaviors available to the Cortex."""

    ESTABLISH_PROCESSING_CHAIN = "establish_processing_chain"


class OptionStepKind(StrEnum):
    """Causal role of one child in an option plan."""

    PLANNER = "planner"
    PREPARATION = "preparation"
    DEPENDENCY = "dependency"


@dataclass(frozen=True)
class OptionBudget:
    """Game-tick budget without confusing requested and observed time."""

    requested_ticks: int
    observed_ticks: int | None = None
    observed_source: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.requested_ticks, int)
            or isinstance(self.requested_ticks, bool)
            or self.requested_ticks <= 0
        ):
            raise ValueError("requested_ticks must be a positive integer")
        if self.observed_ticks is not None:
            if (
                not isinstance(self.observed_ticks, int)
                or isinstance(self.observed_ticks, bool)
                or self.observed_ticks < 0
            ):
                raise ValueError("observed_ticks must be a non-negative integer")
            if not self.observed_source:
                raise ValueError("observed ticks require observed_source")
        elif self.observed_source is not None:
            raise ValueError("observed_source requires observed_ticks")

    @property
    def requested_seconds(self) -> float:
        return float(self.requested_ticks) / TICKS_PER_SECOND

    @property
    def observed_seconds(self) -> float | None:
        if self.observed_ticks is None:
            return None
        return float(self.observed_ticks) / TICKS_PER_SECOND

    @property
    def effective_ticks(self) -> int | None:
        """Only observed time is effective evidence; request is not observation."""

        return self.observed_ticks

    @property
    def planning_ticks(self) -> int:
        """Energy horizon: never shorter than request, extended by observation."""

        if self.observed_ticks is None:
            return self.requested_ticks
        return max(self.requested_ticks, self.observed_ticks)

    @property
    def planning_seconds(self) -> float:
        return float(self.planning_ticks) / TICKS_PER_SECOND

    @property
    def planning_source(self) -> str:
        if self.observed_ticks is None:
            return "requested_budget"
        if self.observed_ticks > self.requested_ticks:
            return "observed_game_ticks"
        return "requested_budget_floor"

    @property
    def sustainability_evaluable(self) -> bool:
        return self.observed_ticks is not None

    @property
    def budget_overrun(self) -> bool | None:
        if self.observed_ticks is None:
            return None
        return self.observed_ticks > self.requested_ticks

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_ticks": self.requested_ticks,
            "requested_seconds": self.requested_seconds,
            "observed_ticks": self.observed_ticks,
            "observed_seconds": self.observed_seconds,
            "observed_source": self.observed_source,
            "effective_ticks": self.effective_ticks,
            "planning_ticks": self.planning_ticks,
            "planning_seconds": self.planning_seconds,
            "planning_source": self.planning_source,
            "sustainability_evaluable": self.sustainability_evaluable,
            "budget_overrun": self.budget_overrun,
        }


@dataclass(frozen=True)
class OptionRequest:
    """One typed temporally extended behavior before composition."""

    option_id: str
    kind: OptionKind
    goal: str
    provenance: ActionProvenance
    budget: OptionBudget
    authority: ActionAuthority = ActionAuthority.SHADOW
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.option_id.strip():
            raise ValueError("option_id must be non-empty")
        if not self.goal.strip():
            raise ValueError("option goal must be non-empty")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "kind": self.kind.value,
            "goal": self.goal,
            "authority": self.authority.value,
            "provenance": self.provenance.to_dict(),
            "budget": self.budget.to_dict(),
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True)
class OptionStep:
    """One causal child used to compose an option."""

    step_id: str
    kind: OptionStepKind
    component: str
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.step_id.strip():
            raise ValueError("option step_id must be non-empty")
        if not self.component.strip():
            raise ValueError("option component must be non-empty")
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "provides", tuple(self.provides))
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind.value,
            "component": self.component,
            "requires": list(self.requires),
            "provides": list(self.provides),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProcessingChainOptionPlan:
    """Composed, inert plan for one functional processing chain."""

    request: OptionRequest
    action_request: ActionRequest
    branch: ProcessingBranch
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep, ...]
    preconditions: tuple[ActionCondition, ...]
    predicted_effects: tuple[ActionCondition, ...]
    termination_conditions: tuple[ActionCondition, ...]
    processor_fuel: FuelDependency | None = None
    actuator_dependency: DeliveryActuatorDependency | None = None

    @property
    def world_mutation(self) -> bool:
        return False

    @property
    def execute_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "action_request": self.action_request.to_dict(),
            "branch": self.branch.to_dict(),
            "prepared": self.prepared.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "preconditions": [condition.to_dict() for condition in self.preconditions],
            "predicted_effects": [
                condition.to_dict() for condition in self.predicted_effects
            ],
            "termination_conditions": [
                condition.to_dict() for condition in self.termination_conditions
            ],
            "processor_fuel": (
                None if self.processor_fuel is None else self.processor_fuel.to_dict()
            ),
            "actuator_dependency": (
                None
                if self.actuator_dependency is None
                else self.actuator_dependency.to_dict()
            ),
            "world_mutation": False,
            "execute_authorized": False,
        }


@dataclass(frozen=True)
class ProcessingChainOptionResult:
    """Exactly one composed option plan or one named refusal."""

    request: OptionRequest
    plan: ProcessingChainOptionPlan | None = None
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.refusal is None):
            raise ValueError("option composition requires exactly one of plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_processing_chain_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    graph: Mapping[str, Any],
    world_entities: Sequence[Mapping[str, Any]],
    catalog: RuntimeFactorioCatalog,
    inventory: Mapping[str, Any],
    electric_power_available: bool | None,
    footprints: Mapping[str, tuple[int, int]] | None = None,
    resources: ResourceSurvey | None = None,
    fuel_sources_by_item: Mapping[str, Sequence[FuelSource]] | None = None,
    placement_reach: int = 8,
    belt_budget: int = DEFAULT_BELT_BUDGET,
    fuel_margin: float = 1.25,
) -> ProcessingChainOptionResult:
    """Compose one functional processing-chain option without world mutation."""

    if option.kind is not OptionKind.ESTABLISH_PROCESSING_CHAIN:
        return ProcessingChainOptionResult(
            request=option,
            refusal=Refusal(
                code="option_kind_unsupported",
                detail=f"unsupported option kind {option.kind.value!r}",
            ),
        )
    option_run = option.provenance.run_id
    action_run = action_request.provenance.run_id
    if (
        option.provenance.code_revision != action_request.provenance.code_revision
        or (
            option_run is not None
            and action_run is not None
            and option_run != action_run
        )
    ):
        return ProcessingChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_OPTION_PROVENANCE_MISMATCH,
                detail="option/action provenance lineage does not match",
                evidence=action_request.evidence,
            ),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return ProcessingChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_OPTION_EXECUTE_NOT_AUTHORIZED,
                detail="F2-G2 composes options in shadow/proposal only",
            ),
        )

    child_request = replace(
        action_request,
        provenance=replace(
            action_request.provenance,
            parent_action_id=option.option_id,
        ),
    )

    structural = plan_processing_for_buffered_output(
        child_request,
        graph=graph,
        world_entities=world_entities,
        catalog=catalog,
        available=inventory,
        footprints=footprints,
        resources=resources,
        placement_reach=placement_reach,
        belt_budget=belt_budget,
    )
    if not structural.branches:
        detail = "; ".join(refusal.code for refusal in structural.refusals)
        return ProcessingChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_OPTION_NO_BRANCH,
                detail="no structural processing branch available"
                + (f" ({detail})" if detail else ""),
                retriable=True,
                evidence=child_request.evidence,
            ),
        )
    if len(structural.branches) != 1:
        return ProcessingChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_OPTION_BRANCH_AMBIGUOUS,
                detail=(
                    "option composer will not choose among multiple material branches: "
                    + ", ".join(sorted(branch.material for branch in structural.branches))
                ),
                retriable=False,
                evidence=action_request.evidence,
            ),
        )

    branch = structural.branches[0]
    preparation = prepare_structural_branch(branch)
    if not preparation.ready or preparation.prepared is None:
        assert preparation.refusal is not None
        return ProcessingChainOptionResult(
            request=option,
            refusal=preparation.refusal,
        )

    horizon_s = option.budget.planning_seconds
    functional = complete_structural_dependencies(
        preparation.prepared,
        catalog=catalog,
        inventory=inventory,
        fuel_sources_by_item=fuel_sources_by_item,
        horizon_s=horizon_s,
        margin=fuel_margin,
    )
    if not functional.ready or functional.prepared is None:
        assert functional.refusal is not None
        return ProcessingChainOptionResult(
            request=option,
            refusal=functional.refusal,
        )

    delivery = complete_delivery_actuator_dependency(
        functional.prepared,
        catalog=catalog,
        inventory=inventory,
        electric_power_available=electric_power_available,
        fuel_sources_by_item=fuel_sources_by_item,
        horizon_s=horizon_s,
        margin=fuel_margin,
    )
    if not delivery.ready or delivery.prepared is None:
        assert delivery.refusal is not None
        return ProcessingChainOptionResult(
            request=option,
            refusal=delivery.refusal,
        )

    steps = (
        OptionStep(
            step_id=f"{option.option_id}:structural-plan",
            kind=OptionStepKind.PLANNER,
            component="factorio_ai_lab.cortex.structural",
            requires=tuple(action_request.requires),
            provides=("processing_branch",),
            details={"material": branch.material, "product": branch.product},
        ),
        OptionStep(
            step_id=f"{option.option_id}:prepare",
            kind=OptionStepKind.PREPARATION,
            component="factorio_ai_lab.cortex.structural_prepare",
            requires=("processing_branch",),
            provides=("prepared_structural_action_v1",),
            details={"contract_version": preparation.prepared.contract_version},
        ),
        OptionStep(
            step_id=f"{option.option_id}:processor-energy",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.functional_dependency",
            requires=("prepared_structural_action_v1",),
            provides=("prepared_structural_action_v2",),
            details={
                "contract_version": functional.prepared.contract_version,
                "planning_horizon_ticks": option.budget.planning_ticks,
                "planning_horizon_source": option.budget.planning_source,
            },
        ),
        OptionStep(
            step_id=f"{option.option_id}:delivery-actuator-energy",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.delivery_actuator_dependency",
            requires=("prepared_structural_action_v2",),
            provides=("prepared_structural_action_v3",),
            details={
                "contract_version": delivery.prepared.contract_version,
                "planning_horizon_ticks": option.budget.planning_ticks,
                "planning_horizon_source": option.budget.planning_source,
            },
        ),
    )

    termination_conditions = (
        tuple(branch.postconditions)
        + execution_guard_conditions()
    )

    return ProcessingChainOptionResult(
        request=option,
        plan=ProcessingChainOptionPlan(
            request=option,
            action_request=child_request,
            branch=branch,
            prepared=delivery.prepared,
            steps=steps,
            preconditions=branch.preconditions,
            predicted_effects=termination_conditions,
            termination_conditions=termination_conditions,
            processor_fuel=functional.dependency,
            actuator_dependency=delivery.dependency,
        ),
    )
