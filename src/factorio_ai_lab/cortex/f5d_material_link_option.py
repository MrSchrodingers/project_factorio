"""Typed one-shot material-link Option for F5-D autonomy."""

from __future__ import annotations

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
from factorio_ai_lab.cortex.f5d_material_link import MaterialLinkPlan
from factorio_ai_lab.cortex.options import (
    OptionKind,
    OptionRequest,
    OptionStep,
    OptionStepKind,
)
from factorio_ai_lab.cortex.structural_prepare import (
    AUTONOMOUS_MATERIAL_LINK_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

REFUSAL_KIND="f5d_material_link_option_kind"
REFUSAL_EXECUTE="f5d_material_link_execute_forbidden"


@dataclass(frozen=True)
class F5DMaterialLinkOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    termination_conditions: tuple[ActionCondition,...]
    frozen_plan: MaterialLinkPlan

    def to_dict(self) -> dict[str,Any]:
        return {
            "request":self.request.to_dict(),
            "action_request":self.action_request.to_dict(),
            "prepared":self.prepared.to_dict(),
            "steps":[row.to_dict() for row in self.steps],
            "termination_conditions":[
                row.to_dict() for row in self.termination_conditions
            ],
            "frozen_plan":self.frozen_plan.to_dict(),
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class F5DMaterialLinkOptionResult:
    request: OptionRequest
    plan: F5DMaterialLinkOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("material-link composition requires plan or refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_material_link_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    link: MaterialLinkPlan,
) -> F5DMaterialLinkOptionResult:
    if option.kind is not OptionKind.AUTONOMOUS_MAINTENANCE:
        return F5DMaterialLinkOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong material-link Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return F5DMaterialLinkOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="material-link composition is inert; A2 is external",
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
        name="autonomous_material_link_succeeded",
        operator=ConditionOperator.EQUALS,
        state=ConditionState.UNKNOWN,
        expected=True,
        hard=True,
    )
    baseline=ActionCondition(
        name="promoted_baseline_entities_preserved",
        operator=ConditionOperator.EQUALS,
        state=ConditionState.UNKNOWN,
        expected=True,
        hard=True,
    )
    frozen=link.to_dict()
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.f5d.autonomous_material_link",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=AUTONOMOUS_MATERIAL_LINK_CONTRACT_VERSION,
        operations=(
            StructuralOperation(op="autonomous_material_link",parameters=frozen),
            StructuralOperation(
                op="verify_postconditions",
                parameters={
                    "conditions":[hard.to_dict(),baseline.to_dict()],
                },
            ),
        ),
        measurement_keys=(
            "autonomous_material_link_succeeded",
            "material_link_output_after",
            "material_link_source_after",
            "promoted_baseline_entities_preserved",
        ),
        preflight={
            "world_mutation":False,
            "execute_authorized":False,
            "external_resource_injection":False,
            "manual_logistics_by_agent":True,
            "learning_policy_grants_authority":False,
            "persistent_logistics_created":True,
            "baseline_entities":[
                dict(row) for row in link.baseline_entities
            ],
        },
    )
    return F5DMaterialLinkOptionResult(
        request=option,
        plan=F5DMaterialLinkOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:material-link",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.f5d_material_link_option",
                    requires=("endogenous_construction_materials",),
                    provides=("persistent_material_flow",),
                    details=frozen,
                ),
            ),
            termination_conditions=(hard,baseline),
            frozen_plan=link,
        ),
    )
