"""Typed F5 technical recovery Option for a rollback-damaged promoted baseline."""

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
from factorio_ai_lab.cortex.structural_execute import execution_guard_conditions
from factorio_ai_lab.cortex.structural_prepare import (
    PURPOSE_INFRASTRUCTURE,
    ROLLBACK_RECOVERY_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)

RECOVERABLE_COMPONENTS=(
    "iron_extractor",
    "iron_buffer",
    "iron_furnace",
    "coal_extractor",
    "coal_buffer",
    "coal_quarantine",
    "copper_furnace",
)
STONE_PER_FURNACE=5
WOOD_PER_CHEST=2
IRON_PLATE_PER_DRILL=9
COAL_BOOTSTRAP_REQUIRED=10
COAL_SEED_RECOVERY_REQUIRED=8
IRON_SMELT_SECONDS=75
RECOVERY_WINDOW_SECONDS=65
COPPER_SMELT_SECONDS=12
RECOVERY_SETTLE_SECONDS=2
COAL_STOCK_TARGET=16
SCIENCE_BUFFER_MIN=10

REFUSAL_KIND="f5c_rollback_recovery_option_kind"
REFUSAL_EXECUTE="f5c_rollback_recovery_execute_forbidden"
REFUSAL_PROVENANCE="f5c_rollback_recovery_provenance_mismatch"
REFUSAL_POSITIONS="f5c_rollback_recovery_positions_required"
REFUSAL_COMPONENTS="f5c_rollback_recovery_components_invalid"


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
            "promoted_baseline_restored",
            "iron_extraction_restored",
            "iron_smelting_restored",
            "coal_stock_recovered",
            "copper_smelting_restored",
            "science_buffer_intact",
        )
    )


def _position(raw: tuple[float,float] | None) -> tuple[float,float] | None:
    if raw is None:
        return None
    return float(raw[0]),float(raw[1])


@dataclass(frozen=True)
class RollbackRecoveryOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    positions: Mapping[str,tuple[float,float]]
    missing_components: tuple[str,...]

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
            "missing_components":list(self.missing_components),
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class RollbackRecoveryOptionResult:
    request: OptionRequest
    plan: RollbackRecoveryOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError("rollback recovery requires exactly plan/refusal")

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_rollback_recovery_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    positions: Mapping[str,tuple[float,float] | None],
    missing_components: Sequence[str],
) -> RollbackRecoveryOptionResult:
    if option.kind is not OptionKind.RESTORE_PROMOTED_ENTITY:
        return RollbackRecoveryOptionResult(
            request=option,
            refusal=Refusal(code=REFUSAL_KIND,detail="wrong rollback recovery Option kind"),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return RollbackRecoveryOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXECUTE,
                detail="rollback recovery composition is inert; A2 is external",
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
        return RollbackRecoveryOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )

    missing=tuple(sorted({str(value) for value in missing_components}))
    allowed=set(RECOVERABLE_COMPONENTS)
    if not missing or any(value not in allowed for value in missing):
        return RollbackRecoveryOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COMPONENTS,
                detail=(
                    "rollback recovery requires a non-empty recoverable missing "
                    f"subset, got {missing!r}"
                ),
            ),
        )

    required=(
        "stone","wood","coal_resource","iron_resource",
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","coal_quarantine",
        "boiler","copper_extractor","copper_buffer","copper_furnace",
    )
    concrete: dict[str,tuple[float,float]]={}
    for name in required:
        value=_position(positions.get(name))
        if value is None:
            return RollbackRecoveryOptionResult(
                request=option,
                refusal=Refusal(
                    code=REFUSAL_POSITIONS,
                    detail=f"rollback recovery requires frozen position {name}",
                ),
            )
        concrete[name]=value

    child=replace(
        action_request,
        provenance=replace(action_request.provenance,parent_action_id=option.option_id),
    )
    hard=_hard_postconditions()
    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.rollback_recovery",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=ROLLBACK_RECOVERY_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="recover_promoted_baseline",
                parameters={
                    "positions":{
                        name:{"x":pos[0],"y":pos[1]}
                        for name,pos in concrete.items()
                    },
                    "missing_components":list(missing),
                    "stone_per_furnace":STONE_PER_FURNACE,
                    "wood_per_chest":WOOD_PER_CHEST,
                    "iron_plate_per_drill":IRON_PLATE_PER_DRILL,
                    "coal_bootstrap_required":COAL_BOOTSTRAP_REQUIRED,
                    "coal_seed_recovery_required":COAL_SEED_RECOVERY_REQUIRED,
                    "iron_smelt_seconds":IRON_SMELT_SECONDS,
                    "recovery_window_seconds":RECOVERY_WINDOW_SECONDS,
                    "copper_smelt_seconds":COPPER_SMELT_SECONDS,
                    "settle_seconds":RECOVERY_SETTLE_SECONDS,
                    "coal_stock_target":COAL_STOCK_TARGET,
                    "science_buffer_min":SCIENCE_BUFFER_MIN,
                },
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=tuple(row.name for row in hard),
        preflight={
            "mode":"technical_rollback_recovery",
            "promotion_credit":False,
            "external_resource_injection":False,
            "world_reset":False,
            "bootstrap_material_origin":"endogenous_live_world",
            "quarantine_recreated_empty":True,
            "missing_components":list(missing),
        },
    )
    guards=execution_guard_conditions(prepared)
    return RollbackRecoveryOptionResult(
        request=option,
        plan=RollbackRecoveryOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=(
                OptionStep(
                    step_id=f"{option.option_id}:prepare",
                    kind=OptionStepKind.PREPARATION,
                    component="factorio_ai_lab.cortex.rollback_recovery_option",
                    requires=(
                        "six_promoted_capabilities",
                        "accepted_pre_corruption_world_fingerprint",
                        "live_endogenous_recovery_resources",
                    ),
                    provides=("prepared_rollback_recovery_v11",),
                    details={
                        "contract_version":ROLLBACK_RECOVERY_CONTRACT_VERSION,
                        "missing_components":list(missing),
                    },
                ),
            ),
            preconditions=(),
            predicted_effects=hard,
            termination_conditions=hard+guards,
            positions=concrete,
            missing_components=missing,
        ),
    )
