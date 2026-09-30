"""Typed F5-C automation-science Option.

This capability deliberately precedes powered manufacturing in the accepted
Factorio 2.0 capability-order amendment. The Option therefore uses endogenous
iron/copper chains and character crafting to produce two separate batches of
automation science packs. It does not build or require an assembling machine.
The produced packs remain in player inventory for the subsequent Automation
research frontier.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from factorio_ai_lab.cortex.structural_execute import execution_guard_conditions
from factorio_ai_lab.cortex.structural_prepare import (
    AUTOMATION_SCIENCE_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

TARGET_PACKS=10
BATCH_PACKS=5
IRON_PLATE_TARGET=20
COPPER_PLATE_TARGET=10
INITIAL_COAL_DRAW=8
IRON_INITIAL_REFUEL=3
COAL_INITIAL_REFUEL=1
COPPER_INITIAL_REFUEL=1
SURVIVAL_COAL_TARGET=6
SURVIVAL_IRON_ORE_DRAW=2
SURVIVAL_COPPER_ORE_DRAW=2
IRON_RECOVERY_WINDOW_SECONDS=90
IRON_SMELT_WINDOW_SECONDS=65
COPPER_SMELT_WINDOW_SECONDS=20
BATCH_GAP_SECONDS=2
SURVIVAL_RECOVERY_WINDOW_SECONDS=12
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_automation_science_option_kind"
REFUSAL_EXECUTE="f5c_automation_science_execute_forbidden"
REFUSAL_PROVENANCE="f5c_automation_science_provenance_mismatch"
REFUSAL_POSITIONS="f5c_automation_science_positions_required"


def _hard_postconditions() -> tuple[ActionCondition,...]:
    return tuple(
        ActionCondition(
            name=name,
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        )
        for name in (
            "automation_science_output_positive",
            "inputs_endogenous",
            "production_sustained",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class AutomationScienceOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    positions: Mapping[str,tuple[float,float]]

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
            "steps":[step.to_dict() for step in self.steps],
            "preconditions":[row.to_dict() for row in self.preconditions],
            "predicted_effects":[row.to_dict() for row in self.predicted_effects],
            "termination_conditions":[
                row.to_dict() for row in self.termination_conditions
            ],
            "positions":{
                name:{"x":pos[0],"y":pos[1]}
                for name,pos in self.positions.items()
            },
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class AutomationScienceOptionResult:
    request: OptionRequest
    plan: AutomationScienceOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError(
                "automation-science composition requires exactly plan/refusal"
            )

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_automation_science_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
) -> AutomationScienceOptionResult:
    if option.kind is not OptionKind.ESTABLISH_AUTOMATION_SCIENCE:
        return AutomationScienceOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_KIND,
                detail="wrong F5-C Option kind",
            ),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return AutomationScienceOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="automation-science composition is inert; A2 is external",
            ),
        )
    if (
        option.provenance.code_revision!=action_request.provenance.code_revision
        or (
            option.provenance.run_id is not None
            and action_request.provenance.run_id is not None
            and option.provenance.run_id!=action_request.provenance.run_id
        )
    ):
        return AutomationScienceOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )

    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
        "boiler","steam_engine",
        "copper_extractor","copper_buffer","copper_furnace",
    )
    concrete: dict[str,tuple[float,float]]={}
    for name in required:
        value=_position(positions.get(name))
        if value is None:
            return AutomationScienceOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"automation science requires frozen position {name}",
                    retriable=True,
                ),
            )
        concrete[name]=value

    child=replace(
        action_request,
        provenance=replace(
            action_request.provenance,
            parent_action_id=option.option_id,
        ),
    )
    hard=_hard_postconditions()
    params={
        "positions":{
            name:{"x":pos[0],"y":pos[1]}
            for name,pos in concrete.items()
        },
        "target_packs":TARGET_PACKS,
        "batch_packs":BATCH_PACKS,
        "iron_plate_target":IRON_PLATE_TARGET,
        "copper_plate_target":COPPER_PLATE_TARGET,
        "initial_coal_draw":INITIAL_COAL_DRAW,
        "iron_initial_refuel":IRON_INITIAL_REFUEL,
        "coal_initial_refuel":COAL_INITIAL_REFUEL,
        "copper_initial_refuel":COPPER_INITIAL_REFUEL,
        "survival_coal_target":SURVIVAL_COAL_TARGET,
        "survival_iron_ore_draw":SURVIVAL_IRON_ORE_DRAW,
        "survival_copper_ore_draw":SURVIVAL_COPPER_ORE_DRAW,
        "iron_recovery_window_seconds":IRON_RECOVERY_WINDOW_SECONDS,
        "iron_smelt_window_seconds":IRON_SMELT_WINDOW_SECONDS,
        "copper_smelt_window_seconds":COPPER_SMELT_WINDOW_SECONDS,
        "batch_gap_seconds":BATCH_GAP_SECONDS,
        "survival_recovery_window_seconds":SURVIVAL_RECOVERY_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.automation_science",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=AUTOMATION_SCIENCE_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_automation_science",
                parameters=params,
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=tuple(row.name for row in hard),
        preflight={
            "world_mutation":False,
            "execute_authorized":False,
            "external_resource_injection":False,
            "persistent_science_inventory":True,
            "uses_powered_manufacturing":False,
            "target_packs":TARGET_PACKS,
            "batch_packs":BATCH_PACKS,
        },
    )
    guard_conditions=execution_guard_conditions(prepared)
    return AutomationScienceOptionResult(
        request=option,
        plan=AutomationScienceOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:prepare",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.structural_prepare",
                    requires=("promoted_endogenous_material_chains",),
                    provides=("prepared_automation_science_v9",),
                    details={
                        "contract_version":AUTOMATION_SCIENCE_CONTRACT_VERSION,
                        "target_packs":TARGET_PACKS,
                        "batch_packs":BATCH_PACKS,
                    },
                ),
            ),
            preconditions=(),
            predicted_effects=hard,
            termination_conditions=hard+guard_conditions,
            positions=concrete,
        ),
    )
