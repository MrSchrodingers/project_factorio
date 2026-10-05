"""Typed F5-C logistic-science Option.

The Option consumes only promoted endogenous production, researches Logistic
Science Pack through the native research queue, revalidates every promoted
capability, and proves two temporally separated logistic-science outputs.
Composition is inert; bounded A2 authority remains external.
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
    LOGISTIC_SCIENCE_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)

RESEARCH_SCIENCE_PACKS=75
SCIENCE_BATCH=77
IRON_PLATE_TARGET=190
COPPER_PLATE_TARGET=95
ELECTRIC_COAL_MIN=30
ELECTRIC_COAL_TARGET=160
ELECTRIC_COAL_ACCUMULATION_SECONDS=280
BOOTSTRAP_BOILER_REFUEL=10
IRON_MINER_REFUEL=30
COPPER_MINER_REFUEL=15
IRON_FURNACE_REFUEL=15
COPPER_FURNACE_REFUEL=8
BOILER_REFUEL=70
ORE_RECOVERY_WINDOW_SECONDS=800
SMELT_BATCH_SECONDS=160
SMELT_BATCH_COUNT=4
RESEARCH_WINDOW_SECONDS=480
POWERED_MANUFACTURING_WINDOW_SECONDS=12
SURVIVAL_RECOVERY_WINDOW_SECONDS=20
SURVIVAL_WINDOW_SECONDS=20
LOGISTIC_OUTPUT_WINDOW_SECONDS=14
SUSTAINABILITY_SOAK_SECONDS=14
DEFAULT_OPTION_SECONDS=2310

REFUSAL_KIND="f5c_logistic_science_option_kind"
REFUSAL_EXECUTE="f5c_logistic_science_execute_forbidden"
REFUSAL_PROVENANCE="f5c_logistic_science_provenance_mismatch"
REFUSAL_POSITIONS="f5c_logistic_science_positions_required"


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
            "logistic_science_output_positive",
            "inputs_endogenous",
            "all_promoted_capabilities_alive",
            "sustainability_soak_passed",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class LogisticScienceOptionPlan:
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
            "termination_conditions":[row.to_dict() for row in self.termination_conditions],
            "positions":{
                name:{"x":pos[0],"y":pos[1]}
                for name,pos in self.positions.items()
            },
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class LogisticScienceOptionResult:
    request: OptionRequest
    plan: LogisticScienceOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("logistic-science composition requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_logistic_science_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
) -> LogisticScienceOptionResult:
    if option.kind is not OptionKind.ESTABLISH_LOGISTIC_SCIENCE:
        return LogisticScienceOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong F5-C Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return LogisticScienceOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="logistic-science composition is inert; A2 is external",
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
        return LogisticScienceOptionResult(
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
            return LogisticScienceOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"logistic science requires frozen position {name}",
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
        "research_science_packs":RESEARCH_SCIENCE_PACKS,
        "science_batch":SCIENCE_BATCH,
        "iron_plate_target":IRON_PLATE_TARGET,
        "copper_plate_target":COPPER_PLATE_TARGET,
        "electric_coal_min":ELECTRIC_COAL_MIN,
        "electric_coal_target":ELECTRIC_COAL_TARGET,
        "electric_coal_accumulation_seconds":ELECTRIC_COAL_ACCUMULATION_SECONDS,
        "bootstrap_boiler_refuel":BOOTSTRAP_BOILER_REFUEL,
        "iron_miner_refuel":IRON_MINER_REFUEL,
        "copper_miner_refuel":COPPER_MINER_REFUEL,
        "iron_furnace_refuel":IRON_FURNACE_REFUEL,
        "copper_furnace_refuel":COPPER_FURNACE_REFUEL,
        "boiler_refuel":BOILER_REFUEL,
        "ore_recovery_window_seconds":ORE_RECOVERY_WINDOW_SECONDS,
        "smelt_batch_seconds":SMELT_BATCH_SECONDS,
        "smelt_batch_count":SMELT_BATCH_COUNT,
        "research_window_seconds":RESEARCH_WINDOW_SECONDS,
        "powered_manufacturing_window_seconds":POWERED_MANUFACTURING_WINDOW_SECONDS,
        "survival_recovery_window_seconds":SURVIVAL_RECOVERY_WINDOW_SECONDS,
        "survival_window_seconds":SURVIVAL_WINDOW_SECONDS,
        "logistic_output_window_seconds":LOGISTIC_OUTPUT_WINDOW_SECONDS,
        "sustainability_soak_seconds":SUSTAINABILITY_SOAK_SECONDS,
    }
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.logistic_science",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=LOGISTIC_SCIENCE_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_logistic_science",
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
            "research_science_packs":RESEARCH_SCIENCE_PACKS,
            "sustainability_soak_seconds":SUSTAINABILITY_SOAK_SECONDS,
        },
    )
    guards=execution_guard_conditions(prepared)
    return LogisticScienceOptionResult(
        request=option,
        plan=LogisticScienceOptionPlan(
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
                        "powered_manufacturing","electric_mining",
                    ),
                    provides=("prepared_logistic_science_v13",),
                    details={
                        "contract_version":LOGISTIC_SCIENCE_CONTRACT_VERSION,
                        "research_science_packs":RESEARCH_SCIENCE_PACKS,
                        "sustainability_soak_seconds":SUSTAINABILITY_SOAK_SECONDS,
                    },
                ),
            ),
            preconditions=(),
            predicted_effects=hard,
            termination_conditions=hard+guards,
            positions=concrete,
        ),
    )
