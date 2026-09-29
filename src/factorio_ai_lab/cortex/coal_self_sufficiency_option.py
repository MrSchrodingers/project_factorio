"""Deterministic F5-C coal self-sufficiency Option.

The Option is inert until an external F5 A2 grant is issued.  It continues the
already-promoted iron extraction world and proves causal endogenous coal use:
all bootstrap coal except one seed is quarantined, mined coal is transferred
back into fuel consumers, and a second production window must grow while the
incumbent iron capability survives.
"""

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
    COAL_SELF_SUFFICIENCY_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.placement import (
    GridPoint,
    ResourceSurvey,
    plan_placement,
)

EXTRACTOR="burner-mining-drill"
OUTPUT_BUFFER="wooden-chest"
QUARANTINE_BUFFER="wooden-chest"
COAL_RESOURCE="coal"
DIRECTION_VALUE=4
DIRECTION="DOWN"

BOOTSTRAP_RESOURCES: tuple[dict[str,Any],...]=(
    {"resource":"stone","quantity":30},
    {"resource":"coal","quantity":6},
    {"resource":"iron-ore","quantity":16},
    {"resource":"wood","quantity":10,"radius":24},
)
BOOTSTRAP_SMELT_IRON_ORE=12
BOOTSTRAP_SMELT_COAL=4
BOOTSTRAP_FURNACES=3
BOOTSTRAP_SMELT_SECONDS=45
BOOTSTRAP_SEED_COAL=1
SEED_WINDOW_SECONDS=30
ENDOGENOUS_WINDOW_SECONDS=20
ENDOGENOUS_TRANSFER_MIN=2

REFUSAL_COAL_KIND="f5c_coal_option_kind"
REFUSAL_COAL_EXECUTE="f5c_coal_compose_execute_forbidden"
REFUSAL_COAL_PROVENANCE="f5c_coal_provenance_mismatch"
REFUSAL_COAL_SURVEY="f5c_coal_resource_survey_required"
REFUSAL_COAL_TARGET="f5c_coal_target_unavailable"
REFUSAL_COAL_INCUMBENT="f5c_coal_incumbent_iron_required"


def coal_cell_reserve(
    tiles: frozenset[GridPoint],
) -> tuple[GridPoint,...]:
    """Reserve output DOWN and quarantine UP around a 2x2 burner drill."""
    if not tiles:
        return ()
    x=max(tile.x for tile in tiles)
    return (
        GridPoint(x,max(tile.y for tile in tiles)+1),
        GridPoint(x,min(tile.y for tile in tiles)-1),
    )


def _normalize_resource_survey(resources: ResourceSurvey) -> ResourceSurvey:
    normalized: dict[GridPoint,str]={}
    for raw,name in resources.tiles.items():
        if isinstance(raw,GridPoint):
            point=raw
        elif (
            isinstance(raw,Sequence)
            and not isinstance(raw,(str,bytes))
            and len(raw)==2
            and all(
                isinstance(value,(int,float)) and not isinstance(value,bool)
                for value in raw
            )
        ):
            point=GridPoint(int(raw[0]),int(raw[1]))
        else:
            raise TypeError(
                "resource survey tile key must be GridPoint or numeric (x,y)"
            )
        normalized[point]=str(name)
    return ResourceSurvey(tiles=normalized,surveyed=resources.surveyed)


def _target_placement(
    *,
    resources: ResourceSurvey,
    world_entities: Sequence[Mapping[str,Any]],
    footprints: Mapping[str,tuple[int,int]] | None,
):
    normalized=_normalize_resource_survey(resources)
    anchors=sorted(
        tile
        for tile,name in normalized.tiles.items()
        if name==COAL_RESOURCE
    )
    for point in anchors:
        candidate=plan_placement(
            entity=EXTRACTOR,
            anchor=(float(point.x),float(point.y)),
            world=world_entities,
            footprints=footprints,
            direction=DIRECTION_VALUE,
            reach=0,
            extra_tiles=coal_cell_reserve,
            resources=normalized,
        )
        if (
            candidate.builds
            and candidate.position is not None
            and candidate.resource_tiles is not None
            and candidate.resource_tiles>0
            and COAL_RESOURCE in candidate.resource_names
        ):
            return candidate
    return None


def _hard_postconditions() -> tuple[ActionCondition,...]:
    return (
        ActionCondition(
            name="coal_mined",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="endogenous_coal_reaches_fuel_consumer",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="external_bootstrap_fuel_retired",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
    )


@dataclass(frozen=True)
class CoalSelfSufficiencyOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep,...]
    preconditions: tuple[ActionCondition,...]
    predicted_effects: tuple[ActionCondition,...]
    termination_conditions: tuple[ActionCondition,...]
    incumbent_iron_extractor_position: tuple[float,float]
    incumbent_iron_buffer_position: tuple[float,float]
    bootstrap_resources: tuple[Mapping[str,Any],...]

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
            "incumbent_iron_extractor_position":{
                "x":self.incumbent_iron_extractor_position[0],
                "y":self.incumbent_iron_extractor_position[1],
            },
            "incumbent_iron_buffer_position":{
                "x":self.incumbent_iron_buffer_position[0],
                "y":self.incumbent_iron_buffer_position[1],
            },
            "bootstrap_resources":[
                dict(row) for row in self.bootstrap_resources
            ],
            "world_mutation":False,
            "execute_authorized":False,
        }


@dataclass(frozen=True)
class CoalSelfSufficiencyOptionResult:
    request: OptionRequest
    plan: CoalSelfSufficiencyOptionPlan | None=None
    refusal: Refusal | None=None

    def __post_init__(self) -> None:
        if (self.plan is None)==(self.refusal is None):
            raise ValueError(
                "coal self-sufficiency composition requires exactly plan/refusal"
            )

    @property
    def ready(self) -> bool:
        return self.plan is not None


def compose_coal_self_sufficiency_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    world_entities: Sequence[Mapping[str,Any]],
    resources: ResourceSurvey | None,
    incumbent_iron_extractor_position: tuple[float,float] | None,
    incumbent_iron_buffer_position: tuple[float,float] | None,
    footprints: Mapping[str,tuple[int,int]] | None=None,
) -> CoalSelfSufficiencyOptionResult:
    if option.kind is not OptionKind.ESTABLISH_COAL_SELF_SUFFICIENCY:
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_KIND,
                detail=f"unsupported coal option kind {option.kind.value!r}",
            ),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_EXECUTE,
                detail="coal Option composition is inert; A2 is applied later",
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
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )
    if resources is None:
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_SURVEY,
                detail="coal self-sufficiency requires observed resource survey",
                retriable=True,
            ),
        )
    if (
        incumbent_iron_extractor_position is None
        or incumbent_iron_buffer_position is None
    ):
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_INCUMBENT,
                detail="coal promotion requires incumbent iron capability positions",
            ),
        )

    placement=_target_placement(
        resources=resources,
        world_entities=world_entities,
        footprints=footprints,
    )
    if placement is None or placement.position is None:
        return CoalSelfSufficiencyOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_COAL_TARGET,
                detail="no buildable surveyed coal extraction target exists",
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
    target=(float(placement.position[0]),float(placement.position[1]))
    preconditions=(
        ActionCondition(
            name="coal_resource_patch_valid",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.SATISFIED,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="iron_extraction_incumbent",
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
    )

    prepared=PreparedStructuralAction(
        action_id=child.action_id,
        family=child.family,
        intent=child.intent,
        binding="cortex.structural.coal_self_sufficiency",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=COAL_SELF_SUFFICIENCY_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="establish_coal_self_sufficiency",
                parameters={
                    "target_position":{"x":target[0],"y":target[1]},
                    "placement":placement.to_dict(),
                    "bootstrap_resources":[
                        dict(row) for row in BOOTSTRAP_RESOURCES
                    ],
                    "bootstrap_furnace_quantity":BOOTSTRAP_FURNACES,
                    "bootstrap_iron_ore_quantity":BOOTSTRAP_SMELT_IRON_ORE,
                    "bootstrap_smelting_coal":BOOTSTRAP_SMELT_COAL,
                    "bootstrap_smelt_seconds":BOOTSTRAP_SMELT_SECONDS,
                    "bootstrap_seed_coal":BOOTSTRAP_SEED_COAL,
                    "seed_window_seconds":SEED_WINDOW_SECONDS,
                    "endogenous_window_seconds":ENDOGENOUS_WINDOW_SECONDS,
                    "endogenous_transfer_min":ENDOGENOUS_TRANSFER_MIN,
                    "incumbent_iron_extractor_position":{
                        "x":incumbent_iron_extractor_position[0],
                        "y":incumbent_iron_extractor_position[1],
                    },
                    "incumbent_iron_buffer_position":{
                        "x":incumbent_iron_buffer_position[0],
                        "y":incumbent_iron_buffer_position[1],
                    },
                },
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={"conditions":[row.to_dict() for row in hard]},
            ),
        ),
        measurement_keys=(
            "coal_mined",
            "endogenous_coal_reaches_fuel_consumer",
            "external_bootstrap_fuel_retired",
            "coal_extractor_exists",
            "coal_endogenous_growth",
            "incumbent_iron_survives",
            "incumbent_iron_buffer_growth",
        ),
        preflight={
            "capability":"coal_self_sufficiency",
            "resource":"coal",
            "placement":placement.to_dict(),
            "target_position":{"x":target[0],"y":target[1]},
            "incumbent_iron_extractor_position":{
                "x":incumbent_iron_extractor_position[0],
                "y":incumbent_iron_extractor_position[1],
            },
            "incumbent_iron_buffer_position":{
                "x":incumbent_iron_buffer_position[0],
                "y":incumbent_iron_buffer_position[1],
            },
            "bootstrap_mode":"world_harvest_then_quarantine",
            "bootstrap_external_injection":False,
            "world_mutation":False,
        },
    )
    guards=execution_guard_conditions(prepared)
    termination=hard+guards
    steps=(
        OptionStep(
            step_id=f"{option.option_id}:survey",
            kind=OptionStepKind.PLANNER,
            component="factorio_ai_lab.planning.placement",
            requires=("resource_survey","incumbent_iron_extraction"),
            provides=("surveyed_coal_target",),
            details={"placement":placement.to_dict()},
        ),
        OptionStep(
            step_id=f"{option.option_id}:causal-fuel",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.coal_self_sufficiency_option",
            requires=("surveyed_coal_target",),
            provides=("endogenous_coal_cycle",),
            details={
                "bootstrap_seed":BOOTSTRAP_SEED_COAL,
                "seed_window_seconds":SEED_WINDOW_SECONDS,
                "endogenous_window_seconds":ENDOGENOUS_WINDOW_SECONDS,
                "bootstrap_quarantine":True,
            },
        ),
        OptionStep(
            step_id=f"{option.option_id}:prepare",
            kind=OptionStepKind.PREPARATION,
            component="factorio_ai_lab.cortex.structural_prepare",
            requires=("surveyed_coal_target","endogenous_coal_cycle"),
            provides=("prepared_coal_self_sufficiency_v5",),
            details={"contract_version":COAL_SELF_SUFFICIENCY_CONTRACT_VERSION},
        ),
    )

    return CoalSelfSufficiencyOptionResult(
        request=option,
        plan=CoalSelfSufficiencyOptionPlan(
            request=option,
            action_request=child,
            prepared=prepared,
            steps=steps,
            preconditions=preconditions,
            predicted_effects=termination,
            termination_conditions=termination,
            incumbent_iron_extractor_position=(
                float(incumbent_iron_extractor_position[0]),
                float(incumbent_iron_extractor_position[1]),
            ),
            incumbent_iron_buffer_position=(
                float(incumbent_iron_buffer_position[0]),
                float(incumbent_iron_buffer_position[1]),
            ),
            bootstrap_resources=tuple(
                dict(row) for row in BOOTSTRAP_RESOURCES
            ),
        ),
    )
