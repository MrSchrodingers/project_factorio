"""Typed F5-C powered-manufacturing Option.

The Option consumes the persistent automation-science buffer through a real
powered Lab, researches Automation through Factorio's native research queue,
then builds one assembling-machine-1 and proves two independent manufactured
outputs.  Research completion is never mutated directly.
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
    POWERED_MANUFACTURING_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

SCIENCE_PACKS=10
IRON_PLATE_TARGET=66
COPPER_PLATE_TARGET=24
MIN_COAL_STOCK=11
COAL_STOCK_TARGET=30
COAL_AMPLIFICATION_CYCLES=4
COAL_CYCLE_SECONDS=30
EXTRACTION_COAL_DRAW=10
IRON_MINER_REFUEL=8
COPPER_MINER_REFUEL=2
ORE_RECOVERY_WINDOW_SECONDS=200
SMELT_WINDOW_SECONDS=220
RESEARCH_WINDOW_SECONDS=120
MANUFACTURING_WINDOW_SECONDS=12
SURVIVAL_RECOVERY_WINDOW_SECONDS=15
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_powered_manufacturing_option_kind"
REFUSAL_EXECUTE="f5c_powered_manufacturing_execute_forbidden"
REFUSAL_PROVENANCE="f5c_powered_manufacturing_provenance_mismatch"
REFUSAL_POSITIONS="f5c_powered_manufacturing_positions_required"


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
            "assembler_powered",
            "iron_gear_output_positive",
            "electronic_circuit_output_positive",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class PoweredManufacturingOptionPlan:
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
class PoweredManufacturingOptionResult:
    request: OptionRequest
    plan: PoweredManufacturingOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError(
                "powered-manufacturing composition requires exactly plan/refusal"
            )

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_powered_manufacturing_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
) -> PoweredManufacturingOptionResult:
    if option.kind is not OptionKind.ESTABLISH_POWERED_MANUFACTURING:
        return PoweredManufacturingOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return PoweredManufacturingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="powered-manufacturing composition is inert; A2 is external",
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
        return PoweredManufacturingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )

    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
        "boiler","steam_engine","power_pole",
        "copper_extractor","copper_buffer","copper_furnace",
    )
    concrete: dict[str,tuple[float,float]]={}
    for name in required:
        value=_position(positions.get(name))
        if value is None:
            return PoweredManufacturingOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"powered manufacturing requires frozen position {name}",
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
        "science_packs":SCIENCE_PACKS,
        "iron_plate_target":IRON_PLATE_TARGET,
        "copper_plate_target":COPPER_PLATE_TARGET,
        "min_coal_stock":MIN_COAL_STOCK,
        "coal_stock_target":COAL_STOCK_TARGET,
        "coal_amplification_cycles":COAL_AMPLIFICATION_CYCLES,
        "coal_cycle_seconds":COAL_CYCLE_SECONDS,
        "extraction_coal_draw":EXTRACTION_COAL_DRAW,
        "iron_miner_refuel":IRON_MINER_REFUEL,
        "copper_miner_refuel":COPPER_MINER_REFUEL,
        "ore_recovery_window_seconds":ORE_RECOVERY_WINDOW_SECONDS,
        "smelt_window_seconds":SMELT_WINDOW_SECONDS,
        "research_window_seconds":RESEARCH_WINDOW_SECONDS,
        "manufacturing_window_seconds":MANUFACTURING_WINDOW_SECONDS,
        "survival_recovery_window_seconds":SURVIVAL_RECOVERY_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.powered_manufacturing",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=POWERED_MANUFACTURING_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_powered_manufacturing",
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
            "native_research_queue":True,
            "direct_research_completion_mutation":False,
            "science_packs_consumed_from_promoted_buffer":SCIENCE_PACKS,
        },
    )
    guards=execution_guard_conditions(prepared)
    return PoweredManufacturingOptionResult(
        request=option,
        plan=PoweredManufacturingOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:prepare",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.structural_prepare",
                    requires=(
                        "iron_extraction","coal_self_sufficiency","iron_smelting",
                        "steam_power","copper_chain","automation_science",
                    ),
                    provides=("prepared_powered_manufacturing_v10",),
                    details={
                        "contract_version":POWERED_MANUFACTURING_CONTRACT_VERSION,
                        "science_packs":SCIENCE_PACKS,
                    },
                ),
            ),
            preconditions=(),
            predicted_effects=hard,
            termination_conditions=hard+guards,
            positions=concrete,
        ),
    )
