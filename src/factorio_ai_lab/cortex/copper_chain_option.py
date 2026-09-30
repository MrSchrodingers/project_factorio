"""Typed F5-C copper-chain Option.

The plan is inert until one expiring A2 grant is issued. It establishes a
persistent burner copper extraction cell plus a copper-smelting furnace while
requiring survival of all previously promoted F5 capabilities.
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
    COPPER_CHAIN_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

STONE_BOOTSTRAP=10
WOOD_BOOTSTRAP=2
CONSTRUCTION_IRON_PLATES=9
INITIAL_COAL_DRAW=8
IRON_RECOVERY_REFUEL=2
COAL_RECOVERY_REFUEL=1
IRON_FURNACE_REFUEL=1
COPPER_DRILL_FUEL=2
COPPER_FURNACE_FUEL=2
COPPER_ORE_DRAW=5
SURVIVAL_COAL_DRAW=4
SURVIVAL_IRON_ORE_DRAW=2
IRON_RECOVERY_WINDOW_SECONDS=45
IRON_SMELT_WINDOW_SECONDS=45
COPPER_EXTRACT_WINDOW_SECONDS=35
COPPER_SMELT_WINDOW_SECONDS=30
SURVIVAL_RECOVERY_WINDOW_SECONDS=20
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_copper_chain_option_kind"
REFUSAL_EXECUTE="f5c_copper_chain_execute_forbidden"
REFUSAL_PROVENANCE="f5c_copper_chain_provenance_mismatch"
REFUSAL_POSITIONS="f5c_copper_chain_positions_required"
REFUSAL_ROUTES="f5c_copper_chain_routes_required"


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
            "copper_extraction_live",
            "copper_smelting_live",
            "copper_plate_output_positive",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class CopperChainOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    positions: Mapping[str,tuple[float,float]]
    route_validations: Mapping[str,Mapping[str,Any]]

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
            "route_validations":{
                name:dict(value)
                for name,value in self.route_validations.items()
            },
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class CopperChainOptionResult:
    request: OptionRequest
    plan: CopperChainOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("copper-chain composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_copper_chain_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
    route_validations: Mapping[str,Mapping[str,Any]],
) -> CopperChainOptionResult:
    if option.kind is not OptionKind.ESTABLISH_COPPER_CHAIN:
        return CopperChainOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return CopperChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="copper-chain composition is inert; A2 is external",
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
        return CopperChainOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )

    required_positions=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine",
        "copper_extractor","copper_furnace","stone","wood",
    )
    concrete: dict[str,tuple[float,float]]={}
    for name in required_positions:
        value=_position(positions.get(name))
        if value is None:
            return CopperChainOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"copper chain requires frozen position {name}",
                    retriable=True,
                ),
            )
        concrete[name]=value
    for name in ("stone","wood","copper","furnace"):
        route=route_validations.get(name)
        if not isinstance(route,Mapping):
            return CopperChainOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_ROUTES,
                    detail=f"copper chain requires observed route {name}",
                    retriable=True,
                ),
            )

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
        "route_validations":{
            name:dict(value)
            for name,value in route_validations.items()
        },
        "stone_bootstrap":STONE_BOOTSTRAP,
        "wood_bootstrap":WOOD_BOOTSTRAP,
        "construction_iron_plates":CONSTRUCTION_IRON_PLATES,
        "initial_coal_draw":INITIAL_COAL_DRAW,
        "iron_recovery_refuel":IRON_RECOVERY_REFUEL,
        "coal_recovery_refuel":COAL_RECOVERY_REFUEL,
        "iron_furnace_refuel":IRON_FURNACE_REFUEL,
        "copper_drill_fuel":COPPER_DRILL_FUEL,
        "copper_furnace_fuel":COPPER_FURNACE_FUEL,
        "copper_ore_draw":COPPER_ORE_DRAW,
        "survival_coal_draw":SURVIVAL_COAL_DRAW,
        "survival_iron_ore_draw":SURVIVAL_IRON_ORE_DRAW,
        "iron_recovery_window_seconds":IRON_RECOVERY_WINDOW_SECONDS,
        "iron_smelt_window_seconds":IRON_SMELT_WINDOW_SECONDS,
        "copper_extract_window_seconds":COPPER_EXTRACT_WINDOW_SECONDS,
        "copper_smelt_window_seconds":COPPER_SMELT_WINDOW_SECONDS,
        "survival_recovery_window_seconds":SURVIVAL_RECOVERY_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.copper_chain",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=COPPER_CHAIN_CONTRACT_VERSION,
        operations=(
            StructuralOperation(op="establish_copper_chain",parameters=params),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=tuple(row.name for row in hard),
        preflight={
            "capability":"copper_chain",
            "positions":params["positions"],
            "route_validations":params["route_validations"],
            "persistent_copper_chain":True,
            "world_mutation":False,
            "external_resource_injection":False,
        },
    )
    termination=hard+execution_guard_conditions(prepared)
    return CopperChainOptionResult(
        request=option,
        plan=CopperChainOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:targets",
                    kind=OptionStepKind.PLANNER,
                    component="factorio_ai_lab.cortex.spatial_validation",
                    requires=("observed_world","resource_survey"),
                    provides=("validated_copper_chain_targets",),
                    details={
                        "routes":{
                            name:dict(value)
                            for name,value in route_validations.items()
                        }
                    },
                ),
                OptionStep(
                    step_id=f"{option.option_id}:prepare",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.structural_prepare",
                    requires=("validated_copper_chain_targets",),
                    provides=("prepared_copper_chain_v8",),
                    details={"contract_version":COPPER_CHAIN_CONTRACT_VERSION},
                ),
            ),
            preconditions=(
                ActionCondition(
                    name="previous_capabilities_promoted",
                    operator=ConditionOperator.EQUALS,
                    state=ConditionState.SATISFIED,
                    expected=True,
                    hard=True,
                ),
                ActionCondition(
                    name="external_resource_injection",
                    operator=ConditionOperator.EQUALS,
                    state=ConditionState.SATISFIED,
                    expected=False,
                    hard=True,
                ),
            ),
            predicted_effects=termination,
            termination_conditions=termination,
            positions=concrete,
            route_validations=route_validations,
        ),
    )
