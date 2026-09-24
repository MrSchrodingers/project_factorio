"""Preparation contract for structural Cortex actions.

F2-D converts a pure ProcessingBranch into a deterministic operation contract.
The prepared action is still inert: it has no execute method and never calls
TransactionalFLEExecutor.  A later checkpoint may compile these operations to
FLE only after authority and postcondition gates are proven.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.cortex.actions import ActionFamily, Refusal
from factorio_ai_lab.cortex.structural import ProcessingBranch
from factorio_ai_lab.planning.delivery import MODE_BELT, MODE_INSERTER

CONTRACT_VERSION = "cortex_structural_ops_v1"
PURPOSE_INFRASTRUCTURE = "infrastructure"

REFUSAL_BRANCH_PRECONDITION = "structural_branch_precondition_unsatisfied"
REFUSAL_BRANCH_PLACEMENT = "structural_branch_placement_not_buildable"
REFUSAL_BRANCH_DELIVERY = "structural_branch_delivery_not_buildable"

MEASUREMENT_KEYS = (
    "producers_reaching_processor",
    "physical_processing_coverage",
    "processor_exists",
    "processor_status",
    "processor_output",
)


@dataclass(frozen=True)
class StructuralOperation:
    """One semantic operation in a prepared structural action."""

    op: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.op.strip():
            raise ValueError("structural operation name must be non-empty")
        object.__setattr__(self, "parameters", dict(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class PreparedStructuralAction:
    """Inert operation contract derived from one ProcessingBranch."""

    action_id: str
    family: ActionFamily
    intent: str
    binding: str
    purpose: str
    contract_version: str
    operations: tuple[StructuralOperation, ...]
    measurement_keys: tuple[str, ...]
    preflight: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "preflight", dict(self.preflight))
        if self.purpose != PURPOSE_INFRASTRUCTURE:
            raise ValueError(f"unsupported structural purpose {self.purpose!r}")
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported structural contract {self.contract_version!r}")
        if not self.operations:
            raise ValueError("prepared structural action requires operations")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "family": self.family.value,
            "intent": self.intent,
            "binding": self.binding,
            "purpose": self.purpose,
            "contract_version": self.contract_version,
            "measurement_keys": list(self.measurement_keys),
            "operations": [operation.to_dict() for operation in self.operations],
            "preflight": dict(self.preflight),
        }


@dataclass(frozen=True)
class StructuralPreparationResult:
    """Prepared structural contract or one named refusal."""

    branch: ProcessingBranch
    prepared: PreparedStructuralAction | None = None
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.refusal is None):
            raise ValueError("preparation must contain exactly one of prepared/refusal")

    @property
    def ready(self) -> bool:
        return self.prepared is not None


def _delivery_operation(branch: ProcessingBranch) -> StructuralOperation:
    delivery = branch.delivery
    payload: dict[str, Any] = {
        "mode": delivery.mode,
        "source_buffer": branch.source_buffer,
        "target_position": (
            None
            if branch.placement.position is None
            else {
                "x": branch.placement.position[0],
                "y": branch.placement.position[1],
            }
        ),
        "delivery": delivery.to_dict(),
    }
    if delivery.mode == MODE_INSERTER:
        payload["entities"] = ["inserter"]
    elif delivery.mode == MODE_BELT:
        payload["entities"] = ["inserter", "transport-belt"]
    return StructuralOperation(op="connect_delivery", parameters=payload)


def prepare_structural_branch(
    branch: ProcessingBranch,
) -> StructuralPreparationResult:
    """Compile one pure branch into an inert, versioned operation contract."""

    blockers = [
        condition.name
        for condition in branch.preconditions
        if condition.hard and not condition.satisfied
    ]
    if blockers:
        return StructuralPreparationResult(
            branch=branch,
            refusal=Refusal(
                code=REFUSAL_BRANCH_PRECONDITION,
                detail="hard precondition(s) not satisfied: " + ", ".join(blockers),
                retriable=True,
                evidence=branch.request.evidence,
            ),
        )

    if not (branch.placement.builds or branch.placement.adopts):
        return StructuralPreparationResult(
            branch=branch,
            refusal=Refusal(
                code=REFUSAL_BRANCH_PLACEMENT,
                detail="processing branch has no build/adopt placement",
                retriable=True,
                evidence=branch.request.evidence,
            ),
        )

    if not branch.delivery.builds:
        return StructuralPreparationResult(
            branch=branch,
            refusal=Refusal(
                code=REFUSAL_BRANCH_DELIVERY,
                detail="processing branch has no buildable delivery link",
                retriable=True,
                evidence=branch.request.evidence,
            ),
        )

    operations: list[StructuralOperation] = [
        StructuralOperation(
            op="ensure_item",
            parameters={
                "item": branch.processor,
                "quantity": 1,
                "dependency_plan": branch.machine_dependency.as_dict(),
            },
        ),
    ]

    if branch.placement.builds:
        operations.append(
            StructuralOperation(
                op="place_processor",
                parameters={
                    "entity": branch.processor,
                    "position": {
                        "x": branch.placement.position[0],
                        "y": branch.placement.position[1],
                    },
                    "placement": branch.placement.to_dict(),
                },
            )
        )
    else:
        operations.append(
            StructuralOperation(
                op="adopt_processor",
                parameters={
                    "entity": branch.processor,
                    "position": (
                        None
                        if branch.placement.position is None
                        else {
                            "x": branch.placement.position[0],
                            "y": branch.placement.position[1],
                        }
                    ),
                    "placement": branch.placement.to_dict(),
                },
            )
        )

    operations.extend(
        (
            StructuralOperation(
                op="configure_processing",
                parameters={
                    "material": branch.material,
                    "recipe": branch.recipe.recipe_name,
                    "product": branch.product,
                    "processor": branch.processor,
                },
            ),
            _delivery_operation(branch),
            StructuralOperation(
                op="verify_postconditions",
                parameters={
                    "conditions": [
                        condition.to_dict()
                        for condition in branch.postconditions
                    ],
                },
            ),
        )
    )

    prepared = PreparedStructuralAction(
        action_id=branch.request.action_id,
        family=branch.request.family,
        intent=branch.request.intent,
        binding="cortex.structural.processing",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=CONTRACT_VERSION,
        operations=tuple(operations),
        measurement_keys=MEASUREMENT_KEYS,
        preflight={
            "material": branch.material,
            "product": branch.product,
            "processor": branch.processor,
            "source_buffer": branch.source_buffer,
            "producers": list(branch.producers),
            "buffers": list(branch.buffers),
            "placement": branch.placement.to_dict(),
            "delivery": branch.delivery.to_dict(),
            "machine_dependency_feasible": branch.machine_dependency.feasible,
            "world_mutation": False,
        },
    )
    return StructuralPreparationResult(branch=branch, prepared=prepared)
