"""Deterministic F5-C iron-smelting Option.

The Option is inert until the F5 control plane issues one A2 grant. It consumes
only endogenous iron ore and coal from already-promoted physical capabilities,
harvests stone from the live world to craft one persistent furnace, and requires
both incumbent capabilities to survive the transaction.
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
    IRON_SMELTING_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.placement import PlacementPlan

FURNACE="stone-furnace"
STONE_QUANTITY=5
IRON_ORE_DRAW=12
COAL_DRAW=6
FURNACE_COAL=2
IRON_REFUEL_COAL=1
COAL_REFUEL_COAL=1
WORKING_PROBE_SECONDS=5
SMELT_WINDOW_SECONDS=40
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_iron_smelting_option_kind"
REFUSAL_EXECUTE="f5c_iron_smelting_compose_execute_forbidden"
REFUSAL_PROVENANCE="f5c_iron_smelting_provenance_mismatch"
REFUSAL_PLACEMENT="f5c_iron_smelting_placement_required"
REFUSAL_INCUMBENT="f5c_iron_smelting_incumbents_required"
REFUSAL_STONE_ROUTE="f5c_iron_smelting_stone_route_required"


def _hard_postconditions() -> tuple[ActionCondition,...]:
    names=(
        "iron_ore_input_live",
        "furnace_operational",
        "iron_plate_output_positive",
        "iron_extraction_survives",
        "coal_self_sufficiency_survives",
    )
    return tuple(
        ActionCondition(
            name=name,
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        )
        for name in names
    )


def _validated_stone_route(
    route: Mapping[str,Any] | None,
) -> dict[str,Any] | None:
    if not isinstance(route,Mapping) or route.get("resource")!="stone":
        return None
    position=route.get("position")
    waypoints=route.get("validated_path_waypoints")
    quantity=route.get("quantity")
    if (
        not isinstance(position,Mapping)
        or not isinstance(position.get("x"),(int,float))
        or isinstance(position.get("x"),bool)
        or not isinstance(position.get("y"),(int,float))
        or isinstance(position.get("y"),bool)
        or not isinstance(waypoints,int)
        or isinstance(waypoints,bool)
        or waypoints<=0
        or not isinstance(quantity,int)
        or isinstance(quantity,bool)
        or quantity<STONE_QUANTITY
    ):
        return None
    return {
        "resource":"stone",
        "quantity":int(quantity),
        "position":{
            "x":float(position["x"]),
            "y":float(position["y"]),
        },
        "validated_path_waypoints":int(waypoints),
    }


@dataclass(frozen=True)
class IronSmeltingOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    furnace_position: tuple[float,float]
    incumbent_iron_extractor_position: tuple[float,float]
    incumbent_iron_buffer_position: tuple[float,float]
    incumbent_coal_extractor_position: tuple[float,float]
    incumbent_coal_buffer_position: tuple[float,float]
    stone_route: Mapping[str,Any]

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
            "preconditions":[row.to_dict() for row in self.preconditions],
            "predicted_effects":[row.to_dict() for row in self.predicted_effects],
            "termination_conditions":[
                row.to_dict() for row in self.termination_conditions
            ],
            "furnace_position":{
                "x":self.furnace_position[0],
                "y":self.furnace_position[1],
            },
            "incumbent_iron_extractor_position":{
                "x":self.incumbent_iron_extractor_position[0],
                "y":self.incumbent_iron_extractor_position[1],
            },
            "incumbent_iron_buffer_position":{
                "x":self.incumbent_iron_buffer_position[0],
                "y":self.incumbent_iron_buffer_position[1],
            },
            "incumbent_coal_extractor_position":{
                "x":self.incumbent_coal_extractor_position[0],
                "y":self.incumbent_coal_extractor_position[1],
            },
            "incumbent_coal_buffer_position":{
                "x":self.incumbent_coal_buffer_position[0],
                "y":self.incumbent_coal_buffer_position[1],
            },
            "stone_route":dict(self.stone_route),
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class IronSmeltingOptionResult:
    request: OptionRequest
    plan: IronSmeltingOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("iron smelting composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_iron_smelting_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    furnace_placement: PlacementPlan,
    stone_route: Mapping[str,Any] | None,
    incumbent_iron_extractor_position: tuple[float,float] | None,
    incumbent_iron_buffer_position: tuple[float,float] | None,
    incumbent_coal_extractor_position: tuple[float,float] | None,
    incumbent_coal_buffer_position: tuple[float,float] | None,
) -> IronSmeltingOptionResult:
    if option.kind is not OptionKind.ESTABLISH_IRON_SMELTING:
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="iron-smelting composition is inert; A2 is external",
            ),
        )
    if (
        option.provenance.code_revision
        !=action_request.provenance.code_revision
        or (
            option.provenance.run_id is not None
            and action_request.provenance.run_id is not None
            and option.provenance.run_id!=action_request.provenance.run_id
        )
    ):
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )
    incumbents=(
        incumbent_iron_extractor_position,
        incumbent_iron_buffer_position,
        incumbent_coal_extractor_position,
        incumbent_coal_buffer_position,
    )
    if any(value is None for value in incumbents):
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_INCUMBENT,
                detail="iron smelting requires promoted iron+coal positions",
            ),
        )
    if not furnace_placement.builds or furnace_placement.position is None:
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PLACEMENT,
                detail="no buildable persistent furnace placement",
                retriable=True,
            ),
        )
    stone=_validated_stone_route(stone_route)
    if stone is None:
        return IronSmeltingOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_STONE_ROUTE,
                detail="iron smelting requires frozen path-validated stone route",
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
    position=(
        float(furnace_placement.position[0]),
        float(furnace_placement.position[1]),
    )
    iron_extractor=incumbent_iron_extractor_position
    iron_buffer=incumbent_iron_buffer_position
    coal_extractor=incumbent_coal_extractor_position
    coal_buffer=incumbent_coal_buffer_position
    assert iron_extractor is not None
    assert iron_buffer is not None
    assert coal_extractor is not None
    assert coal_buffer is not None

    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.iron_smelting",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=IRON_SMELTING_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_iron_smelting",
                parameters={
                    "furnace_position":{"x":position[0],"y":position[1]},
                    "furnace_placement":furnace_placement.to_dict(),
                    "stone_route":stone,
                    "stone_quantity":STONE_QUANTITY,
                    "iron_ore_draw":IRON_ORE_DRAW,
                    "coal_draw":COAL_DRAW,
                    "furnace_coal":FURNACE_COAL,
                    "iron_refuel_coal":IRON_REFUEL_COAL,
                    "coal_refuel_coal":COAL_REFUEL_COAL,
                    "working_probe_seconds":WORKING_PROBE_SECONDS,
                    "smelt_window_seconds":SMELT_WINDOW_SECONDS,
                    "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
                    "incumbent_iron_extractor_position":{
                        "x":iron_extractor[0],"y":iron_extractor[1],
                    },
                    "incumbent_iron_buffer_position":{
                        "x":iron_buffer[0],"y":iron_buffer[1],
                    },
                    "incumbent_coal_extractor_position":{
                        "x":coal_extractor[0],"y":coal_extractor[1],
                    },
                    "incumbent_coal_buffer_position":{
                        "x":coal_buffer[0],"y":coal_buffer[1],
                    },
                },
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=tuple(row.name for row in hard),
        preflight={
            "capability":"iron_smelting",
            "furnace":"stone-furnace",
            "furnace_placement":furnace_placement.to_dict(),
            "furnace_position":{"x":position[0],"y":position[1]},
            "stone_route":stone,
            "incumbent_iron_extractor_position":{
                "x":iron_extractor[0],"y":iron_extractor[1],
            },
            "incumbent_iron_buffer_position":{
                "x":iron_buffer[0],"y":iron_buffer[1],
            },
            "incumbent_coal_extractor_position":{
                "x":coal_extractor[0],"y":coal_extractor[1],
            },
            "incumbent_coal_buffer_position":{
                "x":coal_buffer[0],"y":coal_buffer[1],
            },
            "world_mutation":False,
            "external_resource_injection":False,
        },
    )
    termination=hard+execution_guard_conditions(prepared)
    steps=(
        OptionStep(
            step_id=f"{option.option_id}:placement",
            kind=OptionStepKind.PLANNER,
            component="factorio_ai_lab.planning.placement",
            requires=("iron_extraction","coal_self_sufficiency"),
            provides=("persistent_furnace_position",),
            details={"placement":furnace_placement.to_dict()},
        ),
        OptionStep(
            step_id=f"{option.option_id}:endogenous-inputs",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.iron_smelting_option",
            requires=("persistent_furnace_position",),
            provides=("iron_ore_and_coal_inputs",),
            details={
                "iron_ore_draw":IRON_ORE_DRAW,
                "coal_draw":COAL_DRAW,
                "stone_route":stone,
            },
        ),
        OptionStep(
            step_id=f"{option.option_id}:prepare",
            kind=OptionStepKind.PREPARATION,
            component="factorio_ai_lab.cortex.structural_prepare",
            requires=("iron_ore_and_coal_inputs",),
            provides=("prepared_iron_smelting_v6",),
            details={"contract_version":IRON_SMELTING_CONTRACT_VERSION},
        ),
    )
    return IronSmeltingOptionResult(
        request=option,
        plan=IronSmeltingOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=steps,
            preconditions=(
                ActionCondition(
                    name="endogenous_iron_and_coal_available",
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
            furnace_position=position,
            incumbent_iron_extractor_position=(
                float(iron_extractor[0]),float(iron_extractor[1])
            ),
            incumbent_iron_buffer_position=(
                float(iron_buffer[0]),float(iron_buffer[1])
            ),
            incumbent_coal_extractor_position=(
                float(coal_extractor[0]),float(coal_extractor[1])
            ),
            incumbent_coal_buffer_position=(
                float(coal_buffer[0]),float(coal_buffer[1])
            ),
            stone_route=stone,
        ),
    )
