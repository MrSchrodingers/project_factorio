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
from factorio_ai_lab.cortex.executor import (
    ActionBinding,
    UniversalExecutor,
    request_from_repair_action,
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
    "ConditionOperator",
    "ConditionState",
    "EvidenceRef",
    "LegacyRepairParityAdapter",
    "PreparationResult",
    "PreparedLegacyAction",
    "PreparedStructuralAction",
    "ProcessingBranch",
    "Refusal",
    "StructuralOperation",
    "StructuralPreparationResult",
    "StructuralProcessingPlan",
    "UniversalExecutor",
    "plan_processing_for_buffered_output",
    "prepare_structural_branch",
    "request_from_repair_action",
]
