from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from test_cortex_option_execute import (
    FIXED_NOW,
    FakeTickingEnvironment,
    option_plan,
    probe,
    transactional_executor,
)

from factorio_ai_lab.cortex.actions import ActionStatus
from factorio_ai_lab.cortex.f5_authority import (
    F5_CONTROL_PLANE_ISSUER,
    REFUSAL_F5_CONTINUOUS,
    REFUSAL_F5_LEASE,
    REFUSAL_F5_LEVEL,
    REFUSAL_F5_POLICY_GRANT,
    F5AuthorityLevel,
    F5BoundedAuthorityBridge,
)
from factorio_ai_lab.cortex.grant_ledger import (
    LEDGER_ALREADY_CONSUMED,
    LEDGER_CONSUMED,
    PersistentOptionGrantLedger,
)


def lease_attestation(scope_id: str = "arena:run:lease") -> dict[str, object]:
    return {
        "status": "active",
        "pid": 123,
        "run_id": "f5b-run",
        "arena": "f5b-shadow",
        "owner": "f5-control-plane",
        "lease_id": "lease",
        "scope_id": scope_id,
    }


def make_bridge(tmp_path: Path):
    attestation=lease_attestation()
    ledger=PersistentOptionGrantLedger(tmp_path/"f5b-grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lambda: attestation,
    )
    plan=option_plan()
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5b-shadow-contract",
        reason="validate bounded A2 bridge",
        ttl_seconds=120,
        now=FIXED_NOW,
    )
    return plan,scope,grant,ledger,bridge


def test_a2_issue_and_validate_is_durable_without_consumption(tmp_path: Path) -> None:
    plan,scope,grant,ledger,bridge=make_bridge(tmp_path)

    decision=bridge.validate_a2(
        plan,
        grant=grant,
        scope=scope,
        now=FIXED_NOW,
    )

    assert decision.allowed is True
    assert decision.code=="f5_a2_ready"
    assert grant.issued_by==F5_CONTROL_PLANE_ISSUER
    assert scope.max_executions==1
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is None


def test_a2_executes_exactly_once_and_second_attempt_fails_closed(
    tmp_path: Path,
) -> None:
    plan,scope,grant,ledger,bridge=make_bridge(tmp_path)
    env=FakeTickingEnvironment(promotes=True)

    first=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )
    second=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert first.executed is True
    assert first.result is not None
    assert first.result.status is ActionStatus.ACCEPTED
    assert env.step_calls==1
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None
    assert entry.consume_result=="reserved_before_runtime_mutation"
    assert second.executed is False
    assert second.decision.allowed is False
    assert second.decision.boundary_validation is not None
    assert second.decision.boundary_validation.ledger_status==LEDGER_ALREADY_CONSUMED


def test_non_a2_and_continuous_authority_are_refused_before_execution(
    tmp_path: Path,
) -> None:
    plan,scope,grant,ledger,bridge=make_bridge(tmp_path)
    env=FakeTickingEnvironment(promotes=True)

    wrong_level=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        authority_level=F5AuthorityLevel.A3,
        now=FIXED_NOW,
    )
    continuous=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        continuous_authority=True,
        now=FIXED_NOW,
    )

    assert wrong_level.executed is False
    assert wrong_level.decision.code==REFUSAL_F5_LEVEL
    assert continuous.executed is False
    assert continuous.decision.code==REFUSAL_F5_CONTINUOUS
    assert env.step_calls==0
    assert ledger.check(grant,now=FIXED_NOW).allowed is True


def test_policy_cannot_self_grant_authority(tmp_path: Path) -> None:
    plan,scope,grant,ledger,bridge=make_bridge(tmp_path)
    env=FakeTickingEnvironment(promotes=True)

    flagged=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        policy_originated_grant=True,
        now=FIXED_NOW,
    )
    forged=bridge.execute_a2(
        plan,
        grant=replace(grant,issued_by="learned_policy"),
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert flagged.executed is False
    assert flagged.decision.code==REFUSAL_F5_POLICY_GRANT
    assert forged.executed is False
    assert forged.decision.code==REFUSAL_F5_POLICY_GRANT
    assert env.step_calls==0
    assert ledger.check(grant,now=FIXED_NOW).allowed is True


def test_expired_grant_fails_before_runtime_mutation(tmp_path: Path) -> None:
    plan=option_plan()
    attestation=lease_attestation()
    ledger=PersistentOptionGrantLedger(tmp_path/"expired.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lambda: attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5b-expiry",
        reason="expiry gate",
        ttl_seconds=1,
        now=FIXED_NOW,
    )
    env=FakeTickingEnvironment(promotes=True)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW+timedelta(seconds=2),
    )

    assert execution.executed is False
    assert execution.decision.allowed is False
    assert env.step_calls==0
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is None


def test_lease_mismatch_refuses_without_consuming_grant(tmp_path: Path) -> None:
    plan,scope,grant,ledger,_=make_bridge(tmp_path)
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lambda: lease_attestation("different:scope:lease"),
    )
    env=FakeTickingEnvironment(promotes=True)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is False
    assert execution.decision.code==REFUSAL_F5_LEASE
    assert env.step_calls==0
    assert ledger.check(grant,now=FIXED_NOW).allowed is True


def test_lease_is_reattested_immediately_before_execution(tmp_path: Path) -> None:
    plan=option_plan()
    valid=lease_attestation()
    attestations=[valid,valid,lease_attestation("changed:scope:lease")]
    ledger=PersistentOptionGrantLedger(tmp_path/"reattest.sqlite3")

    def attestor():
        return attestations.pop(0)

    bridge=F5BoundedAuthorityBridge(ledger=ledger,lease_attestor=attestor)
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5b-reattest",
        reason="race closure",
        now=FIXED_NOW,
    )
    env=FakeTickingEnvironment(promotes=True)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is False
    assert execution.decision.code==REFUSAL_F5_LEASE
    assert env.step_calls==0
    assert ledger.check(grant,now=FIXED_NOW).allowed is True


def test_rejected_transaction_rolls_back_but_consumes_one_shot_grant(
    tmp_path: Path,
) -> None:
    plan,scope,grant,ledger,bridge=make_bridge(tmp_path)
    env=FakeTickingEnvironment(promotes=False)
    before=deepcopy(env.initial)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is True
    assert execution.result is not None
    assert execution.result.status is ActionStatus.REJECTED
    assert env.state==before
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None
    assert ledger.consume(grant,now=FIXED_NOW).status==LEDGER_ALREADY_CONSUMED


def test_grant_ledger_consumption_status_constant_is_stable() -> None:
    assert LEDGER_CONSUMED=="consumed"
