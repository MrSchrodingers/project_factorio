"""Deterministic F5-C steam-power Option.

The plan is inert until one expiring A2 grant is issued. Steam-power recipes
are unlocked through Factorio 2.0's native craft-item research triggers, never
by mutating technology state directly. Copper is only a bounded manual
bootstrap for electronics; no persistent copper extraction capability is
created or promoted.
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
    PURPOSE_INFRASTRUCTURE,
    STEAM_POWER_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)

IRON_TRIGGER_ORE_DRAW=56
COPPER_TRIGGER_ORE=10
STONE_BOOTSTRAP=5
WOOD_BOOTSTRAP=1
INITIAL_COAL_DRAW=16
IRON_INITIAL_REFUEL=1
COAL_INITIAL_REFUEL=1
IRON_FURNACE_TRIGGER_COAL=5
COPPER_FURNACE_COAL=1
BOILER_COAL=8
PIPE_TOPUP_COAL=1
PIPE_SMELT_SECONDS_PER_PLATE=4
PIPE_MIN_TOPUP_WINDOW_SECONDS=10
SURVIVAL_COAL_DRAW=3
IRON_SURVIVAL_ORE_DRAW=2
IRON_TRIGGER_WINDOW_SECONDS=190
COPPER_TRIGGER_WINDOW_SECONDS=40
POWER_WINDOW_SECONDS=15
SURVIVAL_WINDOW_SECONDS=20

REFUSAL_KIND="f5c_steam_power_option_kind"
REFUSAL_EXECUTE="f5c_steam_power_compose_execute_forbidden"
REFUSAL_PROVENANCE="f5c_steam_power_provenance_mismatch"
REFUSAL_INCUMBENT="f5c_steam_power_incumbents_required"
REFUSAL_TARGETS="f5c_steam_power_targets_required"


def _hard_postconditions() -> tuple[ActionCondition,...]:
    names=(
        "water_source_valid",
        "endogenous_fuel_reachable",
        "steam_generated",
        "electrical_production_positive",
        "electric_consumer_supplied",
        "iron_extraction_survives",
        "coal_self_sufficiency_survives",
        "iron_smelting_survives",
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


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class SteamPowerOptionPlan:
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
class SteamPowerOptionResult:
    request: OptionRequest
    plan: SteamPowerOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("steam-power composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_steam_power_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    incumbent_iron_extractor_position: tuple[float,float] | None,
    incumbent_iron_buffer_position: tuple[float,float] | None,
    incumbent_iron_furnace_position: tuple[float,float] | None,
    incumbent_coal_extractor_position: tuple[float,float] | None,
    incumbent_coal_buffer_position: tuple[float,float] | None,
    stone_position: tuple[float,float] | None,
    copper_position: tuple[float,float] | None,
    wood_position: tuple[float,float] | None,
    water_position: tuple[float,float] | None,
    route_validations: Mapping[str,Mapping[str,Any]],
) -> SteamPowerOptionResult:
    if option.kind is not OptionKind.ESTABLISH_STEAM_POWER:
        return SteamPowerOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return SteamPowerOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="steam-power composition is inert; A2 is external",
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
        return SteamPowerOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )

    positions={
        "iron_extractor":_position(incumbent_iron_extractor_position),
        "iron_buffer":_position(incumbent_iron_buffer_position),
        "iron_furnace":_position(incumbent_iron_furnace_position),
        "coal_extractor":_position(incumbent_coal_extractor_position),
        "coal_buffer":_position(incumbent_coal_buffer_position),
        "stone":_position(stone_position),
        "copper":_position(copper_position),
        "wood":_position(wood_position),
        "water":_position(water_position),
    }
    if any(positions[name] is None for name in (
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
    )):
        return SteamPowerOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_INCUMBENT,
                detail="steam power requires promoted iron+coal+smelting positions",
            ),
        )
    if any(positions[name] is None for name in ("stone","copper","wood","water")):
        return SteamPowerOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_TARGETS,
                detail="steam power requires frozen endogenous bootstrap targets",
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
    concrete={name:value for name,value in positions.items() if value is not None}
    params={
        "positions":{
            name:{"x":pos[0],"y":pos[1]}
            for name,pos in concrete.items()
        },
        "route_validations":{
            name:dict(value)
            for name,value in route_validations.items()
        },
        "iron_trigger_ore_draw":IRON_TRIGGER_ORE_DRAW,
        "copper_trigger_ore":COPPER_TRIGGER_ORE,
        "stone_bootstrap":STONE_BOOTSTRAP,
        "wood_bootstrap":WOOD_BOOTSTRAP,
        "initial_coal_draw":INITIAL_COAL_DRAW,
        "iron_initial_refuel":IRON_INITIAL_REFUEL,
        "coal_initial_refuel":COAL_INITIAL_REFUEL,
        "iron_furnace_trigger_coal":IRON_FURNACE_TRIGGER_COAL,
        "copper_furnace_coal":COPPER_FURNACE_COAL,
        "boiler_coal":BOILER_COAL,
        "pipe_topup_coal":PIPE_TOPUP_COAL,
        "pipe_smelt_seconds_per_plate":PIPE_SMELT_SECONDS_PER_PLATE,
        "pipe_min_topup_window_seconds":PIPE_MIN_TOPUP_WINDOW_SECONDS,
        "survival_coal_draw":SURVIVAL_COAL_DRAW,
        "iron_survival_ore_draw":IRON_SURVIVAL_ORE_DRAW,
        "iron_trigger_window_seconds":IRON_TRIGGER_WINDOW_SECONDS,
        "copper_trigger_window_seconds":COPPER_TRIGGER_WINDOW_SECONDS,
        "power_window_seconds":POWER_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.steam_power",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=STEAM_POWER_CONTRACT_VERSION,
        operations=(
            StructuralOperation(op="establish_steam_power",parameters=params),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=tuple(row.name for row in hard),
        preflight={
            "capability":"steam_power",
            "positions":params["positions"],
            "route_validations":params["route_validations"],
            "native_research_triggers":{
                "steam-power":{"type":"craft-item","item":"iron-plate","count":50},
                "electronics":{"type":"craft-item","item":"copper-plate","count":10},
            },
            "persistent_copper_chain":False,
            "world_mutation":False,
            "external_resource_injection":False,
        },
    )
    termination=hard+execution_guard_conditions(prepared)
    steps=(
        OptionStep(
            step_id=f"{option.option_id}:bootstrap",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.steam_power_option",
            requires=("iron_smelting","coal_self_sufficiency"),
            provides=("steam_and_electronics_recipes",),
            details={
                "iron_trigger_ore":IRON_TRIGGER_ORE_DRAW,
                "copper_trigger_ore":COPPER_TRIGGER_ORE,
                "persistent_copper_chain":False,
            },
        ),
        OptionStep(
            step_id=f"{option.option_id}:prepare",
            kind=OptionStepKind.PREPARATION,
            component="factorio_ai_lab.cortex.structural_prepare",
            requires=("steam_and_electronics_recipes",),
            provides=("prepared_steam_power_v7",),
            details={"contract_version":STEAM_POWER_CONTRACT_VERSION},
        ),
    )
    return SteamPowerOptionResult(
        request=option,
        plan=SteamPowerOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=steps,
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
