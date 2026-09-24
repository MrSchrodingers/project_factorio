"""Cortex control-plane primitives.

The package starts with typed, no-authority action contracts.  Live authority
is introduced only through explicit phase gates.
"""

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ConditionOperator,
    ConditionState,
    EvidenceRef,
    Refusal,
)
from factorio_ai_lab.cortex.delivery_actuator_dependency import (
    DeliveryActuatorDependency,
    DeliveryActuatorEvaluation,
    DeliveryActuatorResult,
    complete_delivery_actuator_dependency,
)
from factorio_ai_lab.cortex.executor import (
    ActionBinding,
    UniversalExecutor,
    request_from_repair_action,
)
from factorio_ai_lab.cortex.functional_dependency import (
    FuelCandidateEvaluation,
    FuelDependency,
    FuelDependencyPlan,
    FunctionalDependencyResult,
    complete_structural_dependencies,
    plan_burner_fuel_dependency,
)
from factorio_ai_lab.cortex.legacy_parity import (
    LegacyRepairParityAdapter,
    PreparationResult,
    PreparedLegacyAction,
)
from factorio_ai_lab.cortex.structural import (
    ProcessingBranch,
    StructuralProcessingPlan,
    plan_processing_for_buffered_output,
)
from factorio_ai_lab.cortex.structural_execute import (
    CompiledStructuralAction,
    StructuralCompilationResult,
    StructuralTransactionalAdapter,
    compile_structural_action,
    execution_guard_conditions,
)
from factorio_ai_lab.cortex.structural_prepare import (
    PreparedStructuralAction,
    StructuralOperation,
    StructuralPreparationResult,
    prepare_structural_branch,
)

__all__ = [
    "ActionAuthority",
    "ActionBinding",
    "ActionCondition",
    "ActionFamily",
    "ActionProvenance",
    "ActionRequest",
    "ActionResult",
    "ActionStatus",
    "CompiledStructuralAction",
    "ConditionOperator",
    "ConditionState",
    "DeliveryActuatorDependency",
    "DeliveryActuatorEvaluation",
    "DeliveryActuatorResult",
    "EvidenceRef",
    "FuelCandidateEvaluation",
    "FuelDependency",
    "FuelDependencyPlan",
    "FunctionalDependencyResult",
    "LegacyRepairParityAdapter",
    "PreparationResult",
    "PreparedLegacyAction",
    "PreparedStructuralAction",
    "ProcessingBranch",
    "Refusal",
    "StructuralCompilationResult",
    "StructuralOperation",
    "StructuralPreparationResult",
    "StructuralProcessingPlan",
    "StructuralTransactionalAdapter",
    "UniversalExecutor",
    "compile_structural_action",
    "complete_delivery_actuator_dependency",
    "complete_structural_dependencies",
    "execution_guard_conditions",
    "plan_burner_fuel_dependency",
    "plan_processing_for_buffered_output",
    "prepare_structural_branch",
    "request_from_repair_action",
]
