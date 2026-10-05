"""Typed one-shot maintenance Option for F5-D autonomy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionRequest,
    ConditionOperator,
    ConditionState,
    Refusal,
)
from factorio_ai_lab.cortex.options import (
    OptionKind,
    OptionRequest,
    OptionStep,
    OptionStepKind,
)
from factorio_ai_lab.cortex.structural_prepare import (
    AUTONOMOUS_MAINTENANCE_CONTRACT_VERSION,
    PURPOSE_REPAIR,
    PreparedStructuralAction,
    StructuralOperation,
)

REFUSAL_KIND="f5d_maintenance_option_kind"
REFUSAL_EXECUTE="f5d_maintenance_execute_forbidden"
REFUSAL_INPUT="f5d_maintenance_frozen_input_invalid"


@dataclass(frozen=True)
class F5DMaintenancePlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    termination_conditions: tuple[ActionCondition,...]
    frozen_inputs: Mapping[str,Any]

    @property
    def world_mutation(self) -> bool:
        return False

    @property
    def execute_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str,Any]:
        return {
            "request":self.request.to_dict(),
            "action_request":self.action_request.to_dict(),
            "prepared":self.prepared.to_dict(),
            "steps":[row.to_dict() for row in self.steps],
            "termination_conditions":[
                row.to_dict() for row in self.termination_conditions
            ],
            "frozen_inputs":dict(self.frozen_inputs),
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class F5DMaintenanceResult:
    request: OptionRequest
    plan: F5DMaintenancePlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("maintenance composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_autonomous_refuel_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    source: Mapping[str,Any],
    targets: Sequence[Mapping[str,Any]],
    dose: int=8,
    source_reserve: int=100,
) -> F5DMaintenanceResult:
    if option.kind is not OptionKind.AUTONOMOUS_MAINTENANCE:
        return F5DMaintenanceResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong maintenance Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return F5DMaintenanceResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="maintenance composition is inert; A2 is external",
            ),
        )
    if not isinstance(source,Mapping) or not targets:
        return F5DMaintenanceResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_INPUT,
                detail="maintenance requires frozen source and targets",
            ),
        )

    child=replace(
        action_request,
        provenance=replace(
            action_request.provenance,
            parent_action_id=option.option_id,
        ),
    )
    hard=ActionCondition(
        name="autonomous_refuel_succeeded",
        operator=ConditionOperator.EQUALS,
        state=ConditionState.UNKNOWN,
        expected=True,
        hard=True,
    )
    frozen={
        "source":dict(source),
        "targets":[dict(row) for row in targets],
        "dose":int(dose),
        "source_reserve":int(source_reserve),
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.f5d.autonomous_refuel",
        purpose=PURPOSE_REPAIR,
        contract_version=AUTONOMOUS_MAINTENANCE_CONTRACT_VERSION,
        operations=(
            StructuralOperation(op="autonomous_refuel",parameters=frozen),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[hard.to_dict()]},
            ),
        ),
        measurement_keys=("autonomous_refuel_succeeded","refuel_inserted"),
        preflight={
            "world_mutation":False,
            "execute_authorized":False,
            "external_resource_injection":False,
            "manual_logistics_by_agent":True,
            "learning_policy_grants_authority":False,
        },
    )
    return F5DMaintenanceResult(
        request=option,
        plan=F5DMaintenancePlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:refuel",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.f5d_maintenance_option",
                    requires=("endogenous_coal",),
                    provides=("fuel_recovered",),
                    details=frozen,
                ),
            ),
            termination_conditions=(hard,),
            frozen_inputs=frozen,
        ),
    )
