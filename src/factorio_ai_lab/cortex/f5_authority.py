"""F5 bounded authority bridge.

F5 never exposes ambient EXECUTE authority.  This bridge permits exactly one A2
transactional Option when an external control-plane caller supplies a live
WorldLease attestation.  Grants are durable, expiring and consumed atomically by
OptionExecutionBoundary before runtime mutation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from factorio_ai_lab.cortex.actions import ActionAuthority
from factorio_ai_lab.cortex.grant_ledger import (
    OptionExecutionGrant,
    OptionExecutionScope,
    PersistentOptionGrantLedger,
)
from factorio_ai_lab.cortex.option_execute import (
    OptionExecutionBoundary,
    OptionExecutionResult,
    OptionExecutionValidation,
)
from factorio_ai_lab.cortex.options import OptionPlan

F5_CONTROL_PLANE_ISSUER = "cortex_f5_control_plane"
F5_A2_SCHEMA_VERSION = "cortex_f5_a2_authority_bridge_v1"

REFUSAL_F5_LEVEL = "f5_authority_level_not_a2"
REFUSAL_F5_CONTINUOUS = "f5_continuous_authority_forbidden"
REFUSAL_F5_POLICY_GRANT = "f5_policy_self_grant_forbidden"
REFUSAL_F5_LEASE = "f5_world_lease_attestation_invalid"
REFUSAL_F5_SCOPE = "f5_authority_scope_invalid"
REFUSAL_F5_BOUNDARY = "f5_option_boundary_refused"


class F5AuthorityLevel(StrEnum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"
    A6 = "A6"


LeaseAttestor = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class F5AuthorityDecision:
    allowed: bool
    code: str
    detail: str
    authority_level: F5AuthorityLevel
    lease_attestation: dict[str, Any] | None
    boundary_validation: OptionExecutionValidation | None
    schema_version: str = F5_A2_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "allowed": self.allowed,
            "code": self.code,
            "detail": self.detail,
            "authority_level": self.authority_level.value,
            "lease_attestation": self.lease_attestation,
            "boundary_validation": (
                None
                if self.boundary_validation is None
                else self.boundary_validation.to_dict()
            ),
        }


@dataclass(frozen=True)
class F5AuthorityExecution:
    decision: F5AuthorityDecision
    result: OptionExecutionResult | None
    executed: bool
    schema_version: str = F5_A2_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision.to_dict(),
            "executed": self.executed,
            "result": None if self.result is None else self.result.to_dict(),
        }


class F5BoundedAuthorityBridge:
    """External A2 control plane over the durable Option boundary."""

    def __init__(
        self,
        *,
        ledger: PersistentOptionGrantLedger,
        lease_attestor: LeaseAttestor,
    ) -> None:
        self.ledger = ledger
        self.lease_attestor = lease_attestor
        self.boundary = OptionExecutionBoundary(ledger=ledger)

    @staticmethod
    def _attested_scope_id(attestation: Mapping[str, Any]) -> str | None:
        if attestation.get("status") != "active":
            return None
        scope_id = attestation.get("scope_id")
        if not isinstance(scope_id, str) or not scope_id.strip():
            return None
        for key in ("lease_id", "run_id", "arena", "owner", "pid"):
            if attestation.get(key) in (None, ""):
                return None
        return scope_id

    def issue_a2_grant(
        self,
        plan: OptionPlan,
        *,
        experiment_id: str,
        reason: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[OptionExecutionScope, OptionExecutionGrant]:
        """Issue one durable A2 grant from the external F5 control plane."""

        attestation = dict(self.lease_attestor())
        scope_id = self._attested_scope_id(attestation)
        if scope_id is None:
            raise RuntimeError("F5 A2 grant requires an active attested WorldLease")
        scope = OptionExecutionScope.for_plan(
            plan,
            experiment_id=experiment_id,
            world_lease_id=scope_id,
        )
        grant = OptionExecutionGrant.for_plan(
            plan,
            scope=scope,
            issued_by=F5_CONTROL_PLANE_ISSUER,
            reason=reason,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        self.ledger.issue(grant)
        return scope, grant

    def validate_a2(
        self,
        plan: OptionPlan,
        *,
        grant: OptionExecutionGrant,
        scope: OptionExecutionScope,
        authority_level: F5AuthorityLevel = F5AuthorityLevel.A2,
        continuous_authority: bool = False,
        policy_originated_grant: bool = False,
        now: datetime | None = None,
    ) -> F5AuthorityDecision:
        if authority_level is not F5AuthorityLevel.A2:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_LEVEL,
                detail="F5 bounded bridge accepts exactly authority level A2",
                authority_level=authority_level,
                lease_attestation=None,
                boundary_validation=None,
            )
        if continuous_authority:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_CONTINUOUS,
                detail="continuous authority is forbidden throughout F5",
                authority_level=authority_level,
                lease_attestation=None,
                boundary_validation=None,
            )
        if policy_originated_grant or grant.issued_by != F5_CONTROL_PLANE_ISSUER:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_POLICY_GRANT,
                detail="learned policy may rank Options but cannot issue authority",
                authority_level=authority_level,
                lease_attestation=None,
                boundary_validation=None,
            )
        if scope.max_executions != 1:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_SCOPE,
                detail="A2 scope must be strictly one-shot",
                authority_level=authority_level,
                lease_attestation=None,
                boundary_validation=None,
            )

        attestation = dict(self.lease_attestor())
        scope_id = self._attested_scope_id(attestation)
        if scope_id is None or scope_id != scope.world_lease_id:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_LEASE,
                detail="active WorldLease attestation does not match exact A2 scope",
                authority_level=authority_level,
                lease_attestation=attestation,
                boundary_validation=None,
            )

        validation = self.boundary.validate(
            plan,
            grant=grant,
            scope=scope,
            now=now,
        )
        if not validation.valid:
            return F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_BOUNDARY,
                detail=(
                    "underlying durable Option boundary refused A2 validation: "
                    + (
                        validation.refusal.code
                        if validation.refusal is not None
                        else "unknown"
                    )
                ),
                authority_level=authority_level,
                lease_attestation=attestation,
                boundary_validation=validation,
            )
        return F5AuthorityDecision(
            allowed=True,
            code="f5_a2_ready",
            detail=(
                "exact A2 grant is persisted, unconsumed, unexpired and bound "
                "to the active WorldLease"
            ),
            authority_level=authority_level,
            lease_attestation=attestation,
            boundary_validation=validation,
        )

    def execute_a2(
        self,
        plan: OptionPlan,
        *,
        grant: OptionExecutionGrant,
        scope: OptionExecutionScope,
        executor: Any,
        measure: Any,
        tick_source: Any,
        authority_level: F5AuthorityLevel = F5AuthorityLevel.A2,
        continuous_authority: bool = False,
        policy_originated_grant: bool = False,
        use_checkpoint_for_action: bool = True,
        now: datetime | None = None,
    ) -> F5AuthorityExecution:
        decision = self.validate_a2(
            plan,
            grant=grant,
            scope=scope,
            authority_level=authority_level,
            continuous_authority=continuous_authority,
            policy_originated_grant=policy_originated_grant,
            now=now,
        )
        if not decision.allowed:
            return F5AuthorityExecution(
                decision=decision,
                result=None,
                executed=False,
            )

        # Re-attest immediately before the one-shot boundary consumes the grant.
        final_attestation = dict(self.lease_attestor())
        scope_id = self._attested_scope_id(final_attestation)
        if scope_id != scope.world_lease_id:
            refused = F5AuthorityDecision(
                allowed=False,
                code=REFUSAL_F5_LEASE,
                detail="WorldLease changed between A2 validation and execution",
                authority_level=authority_level,
                lease_attestation=final_attestation,
                boundary_validation=decision.boundary_validation,
            )
            return F5AuthorityExecution(
                decision=refused,
                result=None,
                executed=False,
            )

        result = self.boundary.execute(
            plan,
            authority=ActionAuthority.EXECUTE,
            grant=grant,
            scope=scope,
            executor=executor,
            measure=measure,
            tick_source=tick_source,
            use_checkpoint_for_action=use_checkpoint_for_action,
            now=now,
        )
        return F5AuthorityExecution(
            decision=decision,
            result=result,
            executed=True,
        )
