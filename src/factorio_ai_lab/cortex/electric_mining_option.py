"""Typed F5-C electric-mining Option.

The Option preserves the seven promoted capabilities, produces the full red
science budget endogenously, researches Electric Mining Drill through the
native Factorio research queue, and proves a powered electric drill by direct
output into a deterministic chest. Composition is inert; A2 authority remains
external to this module.
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
    ELECTRIC_MINING_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

RESEARCH_SCIENCE_PACKS=25
SCIENCE_BATCH=26
IRON_PLATE_TARGET=82
COPPER_PLATE_TARGET=36
MIN_COAL_STOCK=12
COAL_STOCK_TARGET=38
COAL_AMPLIFICATION_CYCLES=6
COAL_CYCLE_SECONDS=30
EXTRACTION_COAL_DRAW=12
IRON_MINER_REFUEL=8
COPPER_MINER_REFUEL=4
ORE_RECOVERY_WINDOW_SECONDS=260
SMELT_WINDOW_SECONDS=300
RESEARCH_WINDOW_SECONDS=300
ELECTRIC_VALIDATION_WINDOW_SECONDS=25
MANUFACTURING_WINDOW_SECONDS=12
SURVIVAL_RECOVERY_WINDOW_SECONDS=20
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_electric_mining_option_kind"
REFUSAL_EXECUTE="f5c_electric_mining_execute_forbidden"
REFUSAL_PROVENANCE="f5c_electric_mining_provenance_mismatch"
REFUSAL_POSITIONS="f5c_electric_mining_positions_required"
REFUSAL_TREE="f5c_electric_mining_tree_required"


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
            "electric_drill_powered",
            "ore_output_positive",
            "power_survives_load",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class ElectricMiningOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    positions: Mapping[str,tuple[float,float]]
    bootstrap_tree: Mapping[str,Any]

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
            "bootstrap_tree":dict(self.bootstrap_tree),
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class ElectricMiningOptionResult:
    request: OptionRequest
    plan: ElectricMiningOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("electric-mining composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_electric_mining_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
    bootstrap_tree: Mapping[str,Any],
) -> ElectricMiningOptionResult:
    if option.kind is not OptionKind.ESTABLISH_ELECTRIC_MINING:
        return ElectricMiningOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return ElectricMiningOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="electric-mining composition is inert; A2 is external",
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
        return ElectricMiningOptionResult(
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
        "lab","assembler",
        "electric_pole","electric_drill","electric_buffer",
    )
    concrete: dict[str,tuple[float,float]]={}
    for name in required:
        value=_position(positions.get(name))
        if value is None:
            return ElectricMiningOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"electric mining requires frozen position {name}",
                    retriable=True,
                ),
            )
        concrete[name]=value

    tree_name=bootstrap_tree.get("name")
    tree_x=bootstrap_tree.get("x")
    tree_y=bootstrap_tree.get("y")
    tree_direction=bootstrap_tree.get("direction",0)
    if (
        not isinstance(tree_name,str)
        or not tree_name.startswith("tree-")
        or isinstance(tree_x,bool)
        or not isinstance(tree_x,(int,float))
        or isinstance(tree_y,bool)
        or not isinstance(tree_y,(int,float))
        or not isinstance(tree_direction,int)
        or isinstance(tree_direction,bool)
    ):
        return ElectricMiningOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_TREE,
                detail="electric mining requires one frozen endogenous tree",
                retriable=True,
            ),
        )
    tree={
        "name":tree_name,
        "x":float(tree_x),
        "y":float(tree_y),
        "direction":int(tree_direction),
    }

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
        "bootstrap_tree":tree,
        "research_science_packs":RESEARCH_SCIENCE_PACKS,
        "science_batch":SCIENCE_BATCH,
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
        "electric_validation_window_seconds":ELECTRIC_VALIDATION_WINDOW_SECONDS,
        "manufacturing_window_seconds":MANUFACTURING_WINDOW_SECONDS,
        "survival_recovery_window_seconds":SURVIVAL_RECOVERY_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.electric_mining",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=ELECTRIC_MINING_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_electric_mining",
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
            "natural_resource_transaction":"exact_tree_restore_on_rejection",
            "research_science_packs":RESEARCH_SCIENCE_PACKS,
        },
    )
    guards=execution_guard_conditions(prepared)
    return ElectricMiningOptionResult(
        request=option,
        plan=ElectricMiningOptionPlan(
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
                        "powered_manufacturing",
                    ),
                    provides=("prepared_electric_mining_v12",),
                    details={
                        "contract_version":ELECTRIC_MINING_CONTRACT_VERSION,
                        "research_science_packs":RESEARCH_SCIENCE_PACKS,
                        "natural_resource_transaction":True,
                    },
                ),
            ),
            preconditions=(),
            predicted_effects=hard,
            termination_conditions=hard+guards,
            positions=concrete,
            bootstrap_tree=tree,
        ),
    )
