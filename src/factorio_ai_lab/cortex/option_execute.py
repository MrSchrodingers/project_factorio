"""Universal execution boundary for temporally extended Cortex Options.

F2-G4A preserves the F2-G3 typed boundary while replacing process-local grant
memory with a durable one-shot authority ledger. World mutation remains
exclusive to StructuralTransactionalAdapter -> TransactionalFLEExecutor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionResult,
    ActionStatus,
    Refusal,
)
from factorio_ai_lab.cortex.grant_ledger import (
    LEDGER_ALREADY_CONSUMED,
    LEDGER_EXPIRED,
    LEDGER_MISMATCH,
    LEDGER_NOT_FOUND,
    OptionExecutionGrant,
    OptionExecutionScope,
    PersistentOptionGrantLedger,
    option_plan_digest,
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
REFUSAL_OPTION_EXECUTION_LEDGER_REQUIRED = (
    "option_execution_persistent_ledger_required"
)
REFUSAL_OPTION_EXECUTION_GRANT_NOT_PERSISTED = (
    "option_execution_grant_not_persisted"
)
REFUSAL_OPTION_EXECUTION_LEDGER_MISMATCH = (
    "option_execution_ledger_mismatch"
)
REFUSAL_OPTION_EXECUTION_GRANT_EXPIRED = "option_execution_grant_expired"
REFUSAL_OPTION_EXECUTION_RUNTIME_REQUIRED = "option_execution_runtime_required"
REFUSAL_OPTION_EXECUTION_TICK_SOURCE = "option_execution_tick_source_unavailable"
REFUSAL_OPTION_EXECUTION_GRANT_REUSED = "option_execution_grant_already_consumed"
REFUSAL_OPTION_EXECUTION_TICK_BUDGET = "option_execution_tick_budget_unaligned"
REFUSAL_OPTION_EXECUTION_SHADOW = "option_execution_shadow_only"
REFUSAL_OPTION_EXECUTION_PROPOSAL = "option_execution_proposal_only"

_TICK_SOURCE = "factorio_ai_lab.instrumentation.runtime.runtime_game_ticks"
_CHECKPOINT_TICK_SOURCE = "factorio_ai_lab.integrations.fle.FLEStep.info.ticks"


@dataclass(frozen=True)
class OptionExecutionValidation:
    """Non-mutating validation result used by the G4A dry-run boundary."""

    option_id: str
    valid: bool
    plan_digest: str
    grant: OptionExecutionGrant | None
    refusal: Refusal | None
    ledger_status: str | None
    lineage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "valid": self.valid,
            "plan_digest": self.plan_digest,
            "grant": None if self.grant is None else self.grant.to_dict(),
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "ledger_status": self.ledger_status,
            "lineage": dict(self.lineage),
            "world_mutation": False,
            "continuous_authority": False,
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
    grant_consumed_at: str | None = None
    grant_consume_result: str | None = None

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
                raise ValueError(
                    "accepted option execution must report world mutation"
                )
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
            "grant_consumed_at": self.grant_consumed_at,
            "grant_consume_result": self.grant_consume_result,
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
        errors.append(
            "ProcessingBranch parent_action_id does not reference ActionRequest"
        )
    if plan.prepared.action_id != plan.branch.request.action_id:
        errors.append(
            "PreparedStructuralAction action_id does not match branch request"
        )

    revisions = {
        option.code_revision,
        action.code_revision,
        branch.code_revision,
    }
    if len(revisions) != 1:
        errors.append(
            "code_revision lineage differs across Option/Action/Branch"
        )

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
        _condition_signature(condition) for condition in expected
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
    scope: OptionExecutionScope,
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
    if grant.scope != scope:
        errors.append("grant execution scope mismatch")
    if grant.scope.option_kind != plan.request.kind.value:
        errors.append("grant option kind mismatch")
    return tuple(errors)


def _tick_feedback(
    budget: OptionBudget,
    *,
    before: int | None,
    after: int | None,
    action_status: ActionStatus,
    checkpoint_used: bool,
    executor_step_ticks: int | None,
) -> tuple[int | None, str, OptionBudget | None]:
    if action_status is not ActionStatus.ACCEPTED:
        return None, "missing_after_rollback", None
    if before is None or after is None:
        return None, "missing", None
    if after < before:
        if (
            checkpoint_used
            and executor_step_ticks is not None
            and executor_step_ticks >= 0
        ):
            return (
                executor_step_ticks,
                "observed_checkpoint_epoch",
                OptionBudget(
                    requested_ticks=budget.requested_ticks,
                    observed_ticks=executor_step_ticks,
                    observed_source=_CHECKPOINT_TICK_SOURCE,
                ),
            )
        return None, "invalid_rewound", None
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


def _ledger_refusal(status: str) -> tuple[str, str]:
    if status == LEDGER_ALREADY_CONSUMED:
        return (
            REFUSAL_OPTION_EXECUTION_GRANT_REUSED,
            "persistent ledger reports the one-shot grant already consumed",
        )
    if status == LEDGER_EXPIRED:
        return (
            REFUSAL_OPTION_EXECUTION_GRANT_EXPIRED,
            "persistent ledger reports the grant expired",
        )
    if status == LEDGER_NOT_FOUND:
        return (
            REFUSAL_OPTION_EXECUTION_GRANT_NOT_PERSISTED,
            "grant is not present in the persistent authority ledger",
        )
    if status == LEDGER_MISMATCH:
        return (
            REFUSAL_OPTION_EXECUTION_LEDGER_MISMATCH,
            "persisted grant differs from the supplied exact grant",
        )
    return (
        REFUSAL_OPTION_EXECUTION_LEDGER_MISMATCH,
        f"persistent ledger refused grant with status {status}",
    )


class OptionExecutionBoundary:
    """Typed Option boundary backed by durable one-shot authority."""

    def __init__(
        self,
        *,
        ledger: PersistentOptionGrantLedger | None = None,
    ) -> None:
        self.ledger = ledger

    def validate(
        self,
        plan: ProcessingChainOptionPlan,
        *,
        grant: OptionExecutionGrant | None,
        scope: OptionExecutionScope | None,
        now: datetime | None = None,
    ) -> OptionExecutionValidation:
        """Validate exact lineage/grant/ledger state without consuming or mutating."""

        digest = option_plan_digest(plan)
        lineage = _lineage_payload(plan)

        lineage_errors = _lineage_errors(plan)
        if lineage_errors:
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_LINEAGE,
                    detail="; ".join(lineage_errors),
                    evidence=plan.request.evidence,
                ),
                ledger_status=None,
                lineage=lineage,
            )

        termination_errors = _termination_errors(plan)
        if termination_errors:
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_TERMINATION,
                    detail="; ".join(termination_errors),
                    evidence=plan.request.evidence,
                ),
                ledger_status=None,
                lineage=lineage,
            )

        if grant is None or scope is None:
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_GRANT_REQUIRED,
                    detail="dry-run validation requires exact grant and scope",
                    evidence=plan.request.evidence,
                ),
                ledger_status=None,
                lineage=lineage,
            )

        errors = _grant_errors(plan, grant, digest=digest, scope=scope)
        if errors:
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH,
                    detail="; ".join(errors),
                    evidence=plan.request.evidence,
                ),
                ledger_status=None,
                lineage=lineage,
            )

        if self.ledger is None:
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=REFUSAL_OPTION_EXECUTION_LEDGER_REQUIRED,
                    detail="persistent grant ledger is required for authority validation",
                    evidence=plan.request.evidence,
                ),
                ledger_status=None,
                lineage=lineage,
            )

        decision = self.ledger.check(grant, now=now)
        if not decision.allowed:
            code, detail = _ledger_refusal(decision.status)
            return OptionExecutionValidation(
                option_id=plan.request.option_id,
                valid=False,
                plan_digest=digest,
                grant=grant,
                refusal=Refusal(
                    code=code,
                    detail=detail,
                    evidence=plan.request.evidence,
                ),
                ledger_status=decision.status,
                lineage=lineage,
            )

        return OptionExecutionValidation(
            option_id=plan.request.option_id,
            valid=True,
            plan_digest=digest,
            grant=grant,
            refusal=None,
            ledger_status=decision.status,
            lineage=lineage,
        )

    def execute(
        self,
        plan: ProcessingChainOptionPlan,
        *,
        authority: ActionAuthority,
        grant: OptionExecutionGrant | None = None,
        scope: OptionExecutionScope | None = None,
        executor: Any | None = None,
        measure: Any | None = None,
        tick_source: Any | None = None,
        use_checkpoint_for_action: bool = True,
        now: datetime | None = None,
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
                    detail=(
                        "SHADOW validates the frozen Option and forbids execution"
                    ),
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
                    detail=(
                        "PROPOSAL may promote the frozen Option but cannot execute it"
                    ),
                ),
                lineage=_lineage_payload(plan),
            )

        if grant is None or scope is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_GRANT_REQUIRED,
                detail="EXECUTE requires exact one-shot grant and scope",
                grant=grant,
            )

        grant_errors = _grant_errors(
            plan,
            grant,
            digest=digest,
            scope=scope,
        )
        if grant_errors:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH,
                detail="; ".join(grant_errors),
                grant=grant,
            )

        if self.ledger is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_LEDGER_REQUIRED,
                detail="EXECUTE requires a persistent one-shot authority ledger",
                grant=grant,
            )

        if executor is None or measure is None:
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=REFUSAL_OPTION_EXECUTION_RUNTIME_REQUIRED,
                detail=(
                    "EXECUTE requires explicit transactional executor "
                    "and measurement probe"
                ),
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
                    "current FLE compiler requires an integer-second settle "
                    f"budget; got {requested_ticks} ticks"
                ),
                grant=grant,
            )
        settle_seconds = requested_ticks // ticks_per_second

        consume = self.ledger.consume(grant, now=now)
        if not consume.allowed:
            code, detail = _ledger_refusal(consume.status)
            return _refused(
                plan,
                authority=authority,
                digest=digest,
                code=code,
                detail=detail,
                grant=grant,
            )
        if consume.entry is None or consume.entry.consumed_at is None:
            raise RuntimeError("ledger allowed execution without durable consumption")

        action_result = StructuralTransactionalAdapter().execute(
            plan.prepared,
            authority=ActionAuthority.EXECUTE,
            executor=executor,
            measure=measure,
            use_checkpoint_for_action=use_checkpoint_for_action,
            settle_seconds=settle_seconds,
        )

        ticks_after = runtime_game_ticks(tick_source)
        checkpoint_used = bool(
            action_result.measurements.get("checkpoint_used", False)
        )
        raw_executor_ticks = action_result.measurements.get(
            "executor_step_ticks"
        )
        executor_step_ticks = (
            int(raw_executor_ticks)
            if isinstance(raw_executor_ticks, (int, float))
            and not isinstance(raw_executor_ticks, bool)
            and raw_executor_ticks >= 0
            else None
        )
        observed_ticks, tick_status, feedback = _tick_feedback(
            plan.request.budget,
            before=ticks_before,
            after=ticks_after,
            action_status=action_result.status,
            checkpoint_used=checkpoint_used,
            executor_step_ticks=executor_step_ticks,
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
            grant_consumed_at=consume.entry.consumed_at,
            grant_consume_result=consume.entry.consume_result,
        )
