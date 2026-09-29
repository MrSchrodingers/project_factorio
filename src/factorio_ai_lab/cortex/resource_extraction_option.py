"""Typed F5 resource-extraction Option for zero-inventory open play.

The plan is inert.  It freezes the bootstrap quantities, target resource cell,
prepared structural operations and physical postconditions before A2 authority
may be issued.
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
from factorio_ai_lab.cortex.structural_execute import (
    execution_guard_conditions,
)
from factorio_ai_lab.cortex.structural_prepare import (
    PURPOSE_INFRASTRUCTURE,
    RESOURCE_EXTRACTION_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.placement import ResourceSurvey, plan_placement

REFUSAL_EXTRACTION_KIND = "option_resource_extraction_kind_unsupported"
REFUSAL_EXTRACTION_PROVENANCE = "option_resource_extraction_provenance_mismatch"
REFUSAL_EXTRACTION_EXECUTE = "option_resource_extraction_execute_not_authorized"
REFUSAL_EXTRACTION_SURVEY = "option_resource_extraction_survey_missing"
REFUSAL_EXTRACTION_TARGET = "option_resource_extraction_target_unavailable"

IRON_RESOURCE = "iron-ore"
EXTRACTOR = "burner-mining-drill"
OUTPUT_BUFFER = "wooden-chest"
BOOTSTRAP_FUEL = "coal"

BOOTSTRAP_RESOURCES: tuple[dict[str, Any], ...] = (
    {"resource": "stone", "quantity": 30},
    {"resource": "coal", "quantity": 24},
    {"resource": "iron-ore", "quantity": 16},
    {"resource": "wood", "quantity": 10, "radius": 24},
)
BOOTSTRAP_SMELT_IRON_ORE = 12
BOOTSTRAP_SMELT_COAL = 4
BOOTSTRAP_FURNACES = 3
BOOTSTRAP_SMELT_SECONDS = 45
EXTRACTOR_FUEL_UNITS = 8
EXTRACTION_DIRECTION = "DOWN"
EXTRACTION_DIRECTION_VALUE = 8

EXTRACTION_MEASUREMENT_KEYS = (
    "resource_patch_valid",
    "drill_operational",
    "iron_ore_produced",
    "destination_reachable",
    "production_positive_during_validation_window",
    "extractor_exists",
    "buffer_iron_ore",
)


@dataclass(frozen=True)
class ResourceExtractionOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    steps: tuple[OptionStep, ...]
    preconditions: tuple[ActionCondition, ...]
    predicted_effects: tuple[ActionCondition, ...]
    termination_conditions: tuple[ActionCondition, ...]
    resource: str
    extractor: str
    output_buffer: str
    bootstrap_resources: tuple[Mapping[str, Any], ...]

    @property
    def world_mutation(self) -> bool:
        return False

    @property
    def execute_authorized(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "action_request": self.action_request.to_dict(),
            "prepared": self.prepared.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "preconditions": [
                condition.to_dict() for condition in self.preconditions
            ],
            "predicted_effects": [
                condition.to_dict() for condition in self.predicted_effects
            ],
            "termination_conditions": [
                condition.to_dict() for condition in self.termination_conditions
            ],
            "resource": self.resource,
            "extractor": self.extractor,
            "output_buffer": self.output_buffer,
            "bootstrap_resources": [
                dict(row) for row in self.bootstrap_resources
            ],
            "world_mutation": False,
            "execute_authorized": False,
        }


@dataclass(frozen=True)
class ResourceExtractionOptionResult:
    request: OptionRequest
    plan: ResourceExtractionOptionPlan | None = None
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.refusal is None):
            raise ValueError(
                "resource extraction composition requires exactly plan/refusal"
            )

    @property
    def ready(self) -> bool:
        return self.plan is not None


def _hard_postconditions() -> tuple[ActionCondition, ...]:
    return (
        ActionCondition(
            name="resource_patch_valid",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="drill_operational",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="iron_ore_produced",
            operator=ConditionOperator.INCREASE,
            state=ConditionState.UNKNOWN,
            expected=None,
            hard=True,
        ),
        ActionCondition(
            name="destination_reachable",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
        ActionCondition(
            name="production_positive_during_validation_window",
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        ),
    )


def _normalize_resource_survey(resources: ResourceSurvey) -> ResourceSurvey:
    """Normalize external tile keys to the canonical GridPoint representation."""
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
    world_entities: Sequence[Mapping[str, Any]],
    footprints: Mapping[str, tuple[int, int]] | None,
):
    normalized=_normalize_resource_survey(resources)
    anchors=sorted(
        tile
        for tile,name in normalized.tiles.items()
        if name==IRON_RESOURCE
    )
    for point in anchors:
        candidate=plan_placement(
            entity=EXTRACTOR,
            anchor=(float(point.x),float(point.y)),
            world=world_entities,
            footprints=footprints,
            direction=EXTRACTION_DIRECTION_VALUE,
            reach=0,
            resources=normalized,
        )
        if (
            candidate.builds
            and candidate.position is not None
            and candidate.resource_tiles is not None
            and candidate.resource_tiles > 0
            and IRON_RESOURCE in candidate.resource_names
        ):
            return candidate
    return None


def compose_resource_extraction_option(
    option: OptionRequest,
    *,
    action_request: ActionRequest,
    world_entities: Sequence[Mapping[str, Any]],
    resources: ResourceSurvey | None,
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> ResourceExtractionOptionResult:
    if option.kind is not OptionKind.ESTABLISH_RESOURCE_EXTRACTION:
        return ResourceExtractionOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXTRACTION_KIND,
                detail=f"unsupported extraction option kind {option.kind.value!r}",
            ),
        )
    if option.authority is ActionAuthority.EXECUTE:
        return ResourceExtractionOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXTRACTION_EXECUTE,
                detail="Option composition is inert; A2 is applied later",
            ),
        )
    if (
        option.provenance.code_revision
        != action_request.provenance.code_revision
        or (
            option.provenance.run_id is not None
            and action_request.provenance.run_id is not None
            and option.provenance.run_id != action_request.provenance.run_id
        )
    ):
        return ResourceExtractionOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXTRACTION_PROVENANCE,
                detail="option/action provenance lineage does not match",
            ),
        )
    if resources is None:
        return ResourceExtractionOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXTRACTION_SURVEY,
                detail="resource extraction requires an observed resource survey",
                retriable=True,
            ),
        )

    placement = _target_placement(
        resources=resources,
        world_entities=world_entities,
        footprints=footprints,
    )
    if placement is None or placement.position is None:
        return ResourceExtractionOptionResult(
            request=option,
            refusal=Refusal(
                code=REFUSAL_EXTRACTION_TARGET,
                detail="no buildable surveyed iron extraction target exists",
                retriable=True,
            ),
        )

    child_request = replace(
        action_request,
        provenance=replace(
            action_request.provenance,
            parent_action_id=option.option_id,
        ),
    )
    postconditions = _hard_postconditions()
    operations = (
        StructuralOperation(
            op="harvest_bootstrap_resources",
            parameters={"resources": [dict(row) for row in BOOTSTRAP_RESOURCES]},
        ),
        StructuralOperation(
            op="bootstrap_smelt_iron",
            parameters={
                "furnace_quantity": BOOTSTRAP_FURNACES,
                "iron_ore_quantity": BOOTSTRAP_SMELT_IRON_ORE,
                "coal_quantity": BOOTSTRAP_SMELT_COAL,
                "settle_seconds": BOOTSTRAP_SMELT_SECONDS,
            },
        ),
        StructuralOperation(
            op="craft_extraction_cell",
            parameters={
                "extractor": EXTRACTOR,
                "buffer": OUTPUT_BUFFER,
            },
        ),
        StructuralOperation(
            op="place_extractor",
            parameters={
                "entity": EXTRACTOR,
                "position": {
                    "x": placement.position[0],
                    "y": placement.position[1],
                },
                "direction": EXTRACTION_DIRECTION,
                "placement": placement.to_dict(),
            },
        ),
        StructuralOperation(
            op="fuel_extractor",
            parameters={
                "fuel_item": BOOTSTRAP_FUEL,
                "quantity": EXTRACTOR_FUEL_UNITS,
                "source": "world_harvested_bootstrap",
            },
        ),
        StructuralOperation(
            op="place_output_buffer",
            parameters={
                "entity": OUTPUT_BUFFER,
                "direction": EXTRACTION_DIRECTION,
            },
        ),
        StructuralOperation(
            op="verify_postconditions",
            parameters={
                "conditions": [
                    condition.to_dict() for condition in postconditions
                ]
            },
        ),
    )
    prepared = PreparedStructuralAction(
        action_id=child_request.action_id,
        family=child_request.family,
        intent=child_request.intent,
        binding="cortex.structural.resource_extraction",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=RESOURCE_EXTRACTION_CONTRACT_VERSION,
        operations=operations,
        measurement_keys=EXTRACTION_MEASUREMENT_KEYS,
        preflight={
            "capability":"iron_extraction",
            "resource":IRON_RESOURCE,
            "extractor":EXTRACTOR,
            "output_buffer":OUTPUT_BUFFER,
            "target_position":{
                "x":placement.position[0],
                "y":placement.position[1],
            },
            "placement":placement.to_dict(),
            "bootstrap_mode":"world_harvest_only",
            "bootstrap_resources":[
                dict(row) for row in BOOTSTRAP_RESOURCES
            ],
            "bootstrap_external_injection":False,
            "world_mutation":False,
        },
    )
    guards = execution_guard_conditions(prepared)
    termination = postconditions + guards
    steps = (
        OptionStep(
            step_id=f"{option.option_id}:survey",
            kind=OptionStepKind.PLANNER,
            component="factorio_ai_lab.planning.placement",
            requires=("resource_survey",),
            provides=("surveyed_iron_target",),
            details={"placement":placement.to_dict()},
        ),
        OptionStep(
            step_id=f"{option.option_id}:bootstrap",
            kind=OptionStepKind.DEPENDENCY,
            component="factorio_ai_lab.cortex.resource_extraction_option",
            requires=("world_resources",),
            provides=("bootstrap_materials",),
            details={
                "mode":"world_harvest_only",
                "resources":[dict(row) for row in BOOTSTRAP_RESOURCES],
            },
        ),
        OptionStep(
            step_id=f"{option.option_id}:prepare",
            kind=OptionStepKind.PREPARATION,
            component="factorio_ai_lab.cortex.structural_prepare",
            requires=("surveyed_iron_target","bootstrap_materials"),
            provides=("prepared_resource_extraction_v4",),
            details={"contract_version":RESOURCE_EXTRACTION_CONTRACT_VERSION},
        ),
    )
    preconditions = (
        ActionCondition(
            name="resource_patch_valid",
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
    return ResourceExtractionOptionResult(
        request=option,
        plan=ResourceExtractionOptionPlan(
            request=option,
            action_request=child_request,
            prepared=prepared,
            steps=steps,
            preconditions=preconditions,
            predicted_effects=termination,
            termination_conditions=termination,
            resource=IRON_RESOURCE,
            extractor=EXTRACTOR,
            output_buffer=OUTPUT_BUFFER,
            bootstrap_resources=tuple(
                dict(row) for row in BOOTSTRAP_RESOURCES
            ),
        ),
    )
