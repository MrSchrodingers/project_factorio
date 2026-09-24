"""Universal execution boundary for temporally extended Cortex Options.

F2-G3 promotes a frozen SHADOW/PROPOSAL option plan only through an explicit
one-shot grant bound to the exact plan digest.  World mutation still happens
exclusively through StructuralTransactionalAdapter -> TransactionalFLEExecutor.

This module has no scheduler, no live Factorio bootstrap and no persistent
authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionResult,
    ActionStatus,
    Refusal,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    ProcessingChainOptionPlan,
)
from factorio_ai_lab.cortex.structural_execute import (
    StructuralTransactionalAdapter,
    execution_guard_conditions,
    prepared_postconditions,
)
from factorio_ai_lab.instrumentation.runtime import runtime_game_ticks
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND

REFUSAL_OPTION_EXECUTION_LINEAGE = "option_execution_lineage_invalid"
REFUSAL_OPTION_EXECUTION_TERMINATION = "option_execution_termination_invalid"
REFUSAL_OPTION_EXECUTION_GRANT_REQUIRED = "option_execution_grant_required"
REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH = "option_execution_grant_mismatch"
REFUSAL_OPTION_EXECUTION_RUNTIME_REQUIRED = "option_execution_runtime_required"
REFUSAL_OPTION_EXECUTION_TICK_SOURCE = "option_execution_tick_source_unavailable"
REFUSAL_OPTION_EXECUTION_GRANT_REUSED = "option_execution_grant_already_consumed"
REFUSAL_OPTION_EXECUTION_TICK_BUDGET = "option_execution_tick_budget_unaligned"
REFUSAL_OPTION_EXECUTION_SHADOW = "option_execution_shadow_only"
REFUSAL_OPTION_EXECUTION_PROPOSAL = "option_execution_proposal_only"

_TICK_SOURCE = "factorio_ai_lab.instrumentation.runtime.runtime_game_ticks"


def option_plan_digest(plan: ProcessingChainOptionPlan) -> str:
    """Stable SHA-256 over the exact frozen option plan."""

    payload = json.dumps(
        plan.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class OptionExecutionGrant:
    """One-shot promotion grant bound to one exact Option plan."""

    option_id: str
    prepared_action_id: str
    plan_digest: str
    code_revision: str
    run_id: str | None
    issued_by: str
    reason: str

    def __post_init__(self) -> None:
        for label, value in (
            ("option_id", self.option_id),
            ("prepared_action_id", self.prepared_action_id),
            ("plan_digest", self.plan_digest),
            ("code_revision", self.code_revision),
            ("issued_by", self.issued_by),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")

    @classmethod
    def for_plan(
        cls,
        plan: ProcessingChainOptionPlan,
        *,
        issued_by: str,
        reason: str,
    ) -> OptionExecutionGrant:
        provenance = plan.request.provenance
        return cls(
            option_id=plan.request.option_id,
            prepared_action_id=plan.prepared.action_id,
            plan_digest=option_plan_digest(plan),
            code_revision=provenance.code_revision,
            run_id=provenance.run_id,
            issued_by=issued_by,
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "prepared_action_id": self.prepared_action_id,
            "plan_digest": self.plan_digest,
            "code_revision": self.code_revision,
            "run_id": self.run_id,
            "issued_by": self.issued_by,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OptionExecutionResult:
    """Auditable result of one pass through the Option execution boundary."""

    option_id: str
    status: ActionStatus
    authority: ActionAuthority
    plan_digest: str
    changed_world: bool = False
    refusal: Refusal | None = None
    action_result: ActionResult | None = None
    grant: OptionExecutionGrant | None = None
    ticks_before: int | None = None
    ticks_after: int | None = None
    observed_ticks: int | None = None
    tick_measurement_status: str = "not_requested"
    feedback_budget: OptionBudget | None = None
    lineage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.option_id.strip():
            raise ValueError("option_id must be non-empty")
        if not self.plan_digest.strip():
            raise ValueError("plan_digest must be non-empty")
        object.__setattr__(self, "lineage", dict(self.lineage))
        if self.status is ActionStatus.ACCEPTED:
            if self.authority is not ActionAuthority.EXECUTE:
                raise ValueError("accepted option execution requires EXECUTE")
            if not self.changed_world:
                raise ValueError("accepted option execution must report world mutation")
        elif self.changed_world:
            raise ValueError(
                f"{self.status.value} option execution cannot claim world mutation"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "status": self.status.value,
            "authority": self.authority.value,
            "plan_digest": self.plan_digest,
            "changed_world": self.changed_world,
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "action_result": (
                None if self.action_result is None else self.action_result.to_dict()
            ),
            "grant": None if self.grant is None else self.grant.to_dict(),
            "ticks_before": self.ticks_before,
            "ticks_after": self.ticks_after,
            "observed_ticks": self.observed_ticks,
            "tick_measurement_status": self.tick_measurement_status,
            "feedback_budget": (
                None if self.feedback_budget is None else self.feedback_budget.to_dict()
            ),
            "lineage": dict(self.lineage),
            "continuous_authority": False,
        }


def _condition_signature(condition: ActionCondition) -> tuple[Any, ...]:
    return (
        condition.name,
        condition.operator.value,
        condition.expected,
        condition.hard,
    )


def _expected_termination(
    plan: ProcessingChainOptionPlan,
) -> tuple[ActionCondition, ...]:
    return prepared_postconditions(plan.prepared) + execution_guard_conditions()


def _lineage_errors(plan: ProcessingChainOptionPlan) -> tuple[str, ...]:
    option = plan.request.provenance
    action = plan.action_request.provenance
    branch = plan.branch.request.provenance
    errors: list[str] = []

    if action.parent_action_id != plan.request.option_id:
        errors.append("ActionRequest parent_action_id does not reference Option")
    if branch.parent_action_id != plan.action_request.action_id:
        errors.append("ProcessingBranch parent_action_id does not reference ActionRequest")
    if plan.prepared.action_id != plan.branch.request.action_id:
        errors.append("PreparedStructuralAction action_id does not match branch request")

    revisions = {
        option.code_revision,
        action.code_revision,
        branch.code_revision,
    }
    if len(revisions) != 1:
        errors.append("code_revision lineage differs across Option/Action/Branch")

    run_ids = {
        value
        for value in (option.run_id, action.run_id, branch.run_id)
        if value is not None
    }
    if len(run_ids) > 1:
        errors.append("run_id lineage differs across Option/Action/Branch")

    return tuple(errors)


def _termination_errors(plan: ProcessingChainOptionPlan) -> tuple[str, ...]:
    try:
        expected = _expected_termination(plan)
    except (TypeError, ValueError) as exc:
        return (f"prepared termination contract invalid: {exc}",)

    actual_signature = tuple(
        _condition_signature(condition)
        for condition in plan.termination_conditions
    )
    expected_signature = tuple(
        _condition_signature(condition)
        for condition in expected
    )
    if actual_signature != expected_signature:
        return (
            "Option termination conditions differ from transactional hard contract",
        )
    return ()


def _lineage_payload(plan: ProcessingChainOptionPlan) -> dict[str, Any]:
    option = plan.request.provenance
    action = plan.action_request.provenance
    branch = plan.branch.request.provenance
    return {
        "option_id": plan.request.option_id,
        "action_request_id": plan.action_request.action_id,
        "action_parent_option_id": action.parent_action_id,
        "branch_action_id": plan.branch.request.action_id,
        "branch_parent_action_id": branch.parent_action_id,
        "prepared_action_id": plan.prepared.action_id,
        "code_revision": option.code_revision,
        "run_id": option.run_id,
    }


def _grant_errors(
    plan: ProcessingChainOptionPlan,
    grant: OptionExecutionGrant,
    *,
    digest: str,
) -> tuple[str, ...]:
    provenance = plan.request.provenance
    errors: list[str] = []
    if grant.option_id != plan.request.option_id:
        errors.append("grant option_id mismatch")
    if grant.prepared_action_id != plan.prepared.action_id:
        errors.append("grant prepared_action_id mismatch")
    if grant.plan_digest != digest:
        errors.append("grant plan_digest mismatch")
    if grant.code_revision != provenance.code_revision:
        errors.append("grant code_revision mismatch")
    if grant.run_id != provenance.run_id:
        errors.append("grant run_id mismatch")
    return tuple(errors)


def _tick_feedback(
    budget: OptionBudget,
    *,
    before: int | None,
    after: int | None,
    action_status: ActionStatus,
) -> tuple[int | None, str, OptionBudget | None]:
    if before is None or after is None:
        return None, "missing", None
    if after < before:
        return None, "invalid_rewound", None
    if action_status is not ActionStatus.ACCEPTED and after <= before:
        return None, "missing_after_rollback", None
    elapsed = after - before
    return (
        elapsed,
        "observed",
        OptionBudget(
            requested_ticks=budget.requested_ticks,
            observed_ticks=elapsed,
            observed_source=_TICK_SOURCE,
        ),
    )


def _refused(
    plan: ProcessingChainOptionPlan,
    *,
    authority: ActionAuthority,
    digest: str,
    code: str,
    detail: str,
    grant: OptionExecutionGrant | None = None,
    retriable: bool = False,
) -> OptionExecutionResult:
    return OptionExecutionResult(
        option_id=plan.request.option_id,
        status=ActionStatus.REFUSED,
        authority=authority,
        plan_digest=digest,
        changed_world=False,
        refusal=Refusal(
            code=code,
            detail=detail,
            retriable=retriable,
            evidence=plan.request.evidence,
        ),
        grant=grant,
        lineage=_lineage_payload(plan),
    )


class OptionExecutionBoundary:
    """Single typed promotion/execution boundary for composed Cortex Options.

    Grant consumption is process-local in F2-G3.  That is sufficient for
    SHADOW/fake transactional validation, but not a persistent live authority
    ledger; live Option execution therefore remains blocked after this phase.
    """

    def __init__(self) -> None:
        self._consumed_plan_digests: set[str] = set()

    def execute(
        self,
        plan: ProcessingChainOptionPlan,
        *,
        authority: ActionAuthority,
        grant: OptionExecutionGrant | None = None,
        executor: Any | None = None,
        measure: Any | None = None,
        tick_source: Any | None = None,
        use_checkpoint_for_action: bool = True,
    ) -> OptionExecutionResult:
        digest = option_plan_digest(plan)

        lineage_errors = _lineage_errors(plan)
        if lineage_errors:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_LINEAGE,
                detail="; ".join(lineage_errors),
            )

        termination_errors = _termination_errors(plan)
        if termination_errors:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_TERMINATION,
                detail="; ".join(termination_errors),
            )

        if authority is ActionAuthority.SHADOW:
            return OptionExecutionResult(
                option_id=plan.request.option_id,
                status=ActionStatus.SHADOWED,
                authority=authority,
                plan_digest=digest,
                changed_world=False,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_SHADOW,
                    detail="SHADOW validates the frozen Option and forbids execution",
                ),
                lineage=_lineage_payload(plan),
            )

        if authority is ActionAuthority.PROPOSAL:
            return OptionExecutionResult(
                option_id=plan.request.option_id,
                status=ActionStatus.PROPOSED,
                authority=authority,
                plan_digest=digest,
                changed_world=False,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_PROPOSAL,
                    detail="PROPOSAL may promote the frozen Option but cannot execute it",
                ),
                lineage=_lineage_payload(plan),
            )

        if grant is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_GRANT_REQUIRED,
                detail="EXECUTE requires a one-shot OptionExecutionGrant",
            )

        grant_errors = _grant_errors(plan, grant, digest=digest)
        if grant_errors:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH,
                detail="; ".join(grant_errors),
                grant=grant,
            )

        if executor is None or measure is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_RUNTIME_REQUIRED,
                detail="EXECUTE requires explicit transactional executor and measurement probe",
                grant=grant,
            )

        if digest in self._consumed_plan_digests:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_GRANT_REUSED,
                detail="this boundary already consumed the frozen Option plan grant",
                grant=grant,
            )

        if tick_source is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_TICK_SOURCE,
                detail="EXECUTE requires an explicit observable Factorio tick source",
                grant=grant,
            )

        ticks_before = runtime_game_ticks(tick_source)
        if ticks_before is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_TICK_SOURCE,
                detail="Factorio tick counter unavailable before Option execution",
                grant=grant,
                retriable=True,
            )

        requested_ticks = plan.request.budget.requested_ticks
        ticks_per_second = int(TICKS_PER_SECOND)
        if requested_ticks % ticks_per_second != 0:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_TICK_BUDGET,
                detail=(
                    "current FLE compiler requires an integer-second settle budget; "
                    f"got {requested_ticks} ticks"
                ),
                grant=grant,
            )
        settle_seconds = requested_ticks // ticks_per_second

        self._consumed_plan_digests.add(digest)

        action_result = StructuralTransactionalAdapter().execute(
            plan.prepared,
            authority=ActionAuthority.EXECUTE,
            executor=executor,
            measure=measure,
            use_checkpoint_for_action=use_checkpoint_for_action,
            settle_seconds=settle_seconds,
        )

        ticks_after = runtime_game_ticks(tick_source)
        observed_ticks, tick_status, feedback = _tick_feedback(
            plan.request.budget,
            before=ticks_before,
            after=ticks_after,
            action_status=action_result.status,
        )

        return OptionExecutionResult(
            option_id=plan.request.option_id,
            status=action_result.status,
            authority=authority,
            plan_digest=digest,
            changed_world=action_result.changed_world,
            refusal=action_result.refusal,
            action_result=action_result,
            grant=grant,
            ticks_before=ticks_before,
            ticks_after=ticks_after,
            observed_ticks=observed_ticks,
            tick_measurement_status=tick_status,
            feedback_budget=feedback,
            lineage=_lineage_payload(plan),
        )
