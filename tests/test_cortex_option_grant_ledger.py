from __future__ import annotations

import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_cortex_option_execute import (
    FIXED_NOW,
    FakeTickingEnvironment,
    option_plan,
    probe,
    transactional_executor,
)

from factorio_ai_lab.cortex.actions import ActionAuthority, ActionStatus
from factorio_ai_lab.cortex.grant_ledger import (
    LEDGER_ALREADY_CONSUMED,
    LEDGER_CONSUMED,
    LEDGER_EXPIRED,
    LEDGER_READY,
    OptionExecutionGrant,
    OptionExecutionScope,
    PersistentOptionGrantLedger,
)
from factorio_ai_lab.cortex.option_execute import (
    REFUSAL_OPTION_EXECUTION_GRANT_EXPIRED,
    REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH,
    REFUSAL_OPTION_EXECUTION_GRANT_REUSED,
    REFUSAL_OPTION_EXECUTION_LEDGER_REQUIRED,
    OptionExecutionBoundary,
)


def _consume_in_spawned_process(
    ledger_path: str,
    grant: OptionExecutionGrant,
    now,
    queue,
) -> None:
    ledger = PersistentOptionGrantLedger(ledger_path)
    queue.put(ledger.consume(grant, now=now).status)


def make_grant(
    path: Path,
    *,
    ttl_seconds: int = 600,
):
    plan = option_plan()
    scope = OptionExecutionScope.for_plan(
        plan,
        experiment_id="f2-g4a-ledger-test",
        world_lease_id="dry-run-world",
    )
    grant = OptionExecutionGrant.for_plan(
        plan,
        scope=scope,
        issued_by="f2-g4a-test",
        reason="durable authority test",
        ttl_seconds=ttl_seconds,
        now=FIXED_NOW,
    )
    ledger = PersistentOptionGrantLedger(path)
    ledger.issue(grant)
    return plan, scope, grant, ledger


def test_grant_schema_is_persisted_without_consumption(tmp_path: Path) -> None:
    _, _, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")

    entry = ledger.get(grant.grant_id)

    assert entry is not None
    assert entry.grant == grant
    assert entry.consumed_at is None
    assert entry.consume_result is None
    assert ledger.check(grant, now=FIXED_NOW).status == LEDGER_READY


def test_duplicate_grant_id_is_not_upserted(tmp_path: Path) -> None:
    _, _, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        ledger.issue(grant)

    entry = ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is None


def test_expired_grant_is_refused_without_consumption(tmp_path: Path) -> None:
    plan, scope, grant, ledger = make_grant(
        tmp_path / "ledger.sqlite3",
        ttl_seconds=1,
    )
    env = FakeTickingEnvironment(promotes=True)
    tx = transactional_executor(env)

    result = OptionExecutionBoundary(ledger=ledger).execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant,
        scope=scope,
        executor=tx,
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW + timedelta(seconds=2),
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_GRANT_EXPIRED
    assert env.step_calls == 0
    entry = ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is None


def test_wrong_exact_scope_is_refused_before_consumption(tmp_path: Path) -> None:
    plan, scope, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")
    wrong_scope = replace(scope, world_lease_id="different-world")
    env = FakeTickingEnvironment(promotes=True)

    result = OptionExecutionBoundary(ledger=ledger).execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant,
        scope=wrong_scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH
    assert env.step_calls == 0
    assert ledger.check(grant, now=FIXED_NOW).status == LEDGER_READY


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prepared_action_id", "wrong-prepared-action"),
        ("plan_digest", "0" * 64),
        ("code_revision", "wrong-sha"),
        ("run_id", "wrong-run"),
    ],
)
def test_lineage_bound_grant_fields_refuse_before_consume(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    plan, scope, grant, ledger = make_grant(tmp_path / f"{field}.sqlite3")
    supplied = replace(grant, **{field: value})
    env = FakeTickingEnvironment(promotes=True)

    result = OptionExecutionBoundary(ledger=ledger).execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=supplied,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH
    assert env.step_calls == 0
    assert ledger.check(grant, now=FIXED_NOW).status == LEDGER_READY


def test_execute_refuses_process_local_authority_without_ledger() -> None:
    plan = option_plan()
    scope = OptionExecutionScope.for_plan(
        plan,
        experiment_id="f2-g4a-ledger-required",
        world_lease_id="fake-world",
    )
    grant = OptionExecutionGrant.for_plan(
        plan,
        scope=scope,
        issued_by="f2-g4a-test",
        reason="ledger required",
        now=FIXED_NOW,
    )
    env = FakeTickingEnvironment(promotes=True)

    result = OptionExecutionBoundary().execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_LEDGER_REQUIRED
    assert env.step_calls == 0


def test_dry_run_validation_does_not_consume_grant(tmp_path: Path) -> None:
    plan, scope, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")
    boundary = OptionExecutionBoundary(ledger=ledger)

    first = boundary.validate(
        plan,
        grant=grant,
        scope=scope,
        now=FIXED_NOW,
    )
    reconstructed = OptionExecutionBoundary(
        ledger=PersistentOptionGrantLedger(tmp_path / "ledger.sqlite3")
    )
    second = reconstructed.validate(
        plan,
        grant=grant,
        scope=scope,
        now=FIXED_NOW,
    )

    assert first.valid is True
    assert first.ledger_status == LEDGER_READY
    assert second.valid is True
    assert second.ledger_status == LEDGER_READY
    entry = ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is None


def test_consumed_after_crash_window_remains_unusable(tmp_path: Path) -> None:
    plan, scope, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")

    decision = ledger.consume(grant, now=FIXED_NOW)
    assert decision.status == LEDGER_CONSUMED

    env = FakeTickingEnvironment(promotes=True)
    reconstructed = OptionExecutionBoundary(
        ledger=PersistentOptionGrantLedger(tmp_path / "ledger.sqlite3")
    )
    result = reconstructed.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant,
        scope=scope,
        executor=transactional_executor(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_GRANT_REUSED
    assert env.step_calls == 0


def test_concurrent_double_consume_has_exactly_one_winner(
    tmp_path: Path,
) -> None:
    _, _, grant, ledger = make_grant(tmp_path / "ledger.sqlite3")

    def consume_once() -> str:
        local = PersistentOptionGrantLedger(tmp_path / "ledger.sqlite3")
        return local.consume(grant, now=FIXED_NOW).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: consume_once(), range(2)))

    assert sorted(statuses) == sorted(
        [LEDGER_CONSUMED, LEDGER_ALREADY_CONSUMED]
    )
    entry = ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None
    assert entry.consume_result == "reserved_before_runtime_mutation"


def test_expired_check_is_stable_after_process_reconstruction(
    tmp_path: Path,
) -> None:
    _, _, grant, _ = make_grant(
        tmp_path / "ledger.sqlite3",
        ttl_seconds=1,
    )
    reconstructed = PersistentOptionGrantLedger(tmp_path / "ledger.sqlite3")

    decision = reconstructed.check(
        grant,
        now=FIXED_NOW + timedelta(seconds=2),
    )

    assert decision.status == LEDGER_EXPIRED
    assert decision.allowed is False


def test_multiprocess_double_consume_has_exactly_one_winner(
    tmp_path: Path,
) -> None:
    _, _, grant, ledger = make_grant(tmp_path / "ledger-mp.sqlite3")
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_consume_in_spawned_process,
            args=(
                str(tmp_path / "ledger-mp.sqlite3"),
                grant,
                FIXED_NOW,
                queue,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    statuses = [queue.get(timeout=5), queue.get(timeout=5)]
    assert sorted(statuses) == sorted(
        [LEDGER_CONSUMED, LEDGER_ALREADY_CONSUMED]
    )
    entry = ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None


@pytest.mark.parametrize(
    "field",
    ["experiment_id", "world_lease_id", "option_kind"],
)
def test_scope_rejects_wildcards(field: str) -> None:
    values = {
        "experiment_id": "experiment-1",
        "world_lease_id": "world-1",
        "option_kind": "establish_processing_chain",
    }
    values[field] = "*"
    with pytest.raises(ValueError, match="wildcard"):
        OptionExecutionScope(**values)


def test_grant_requires_run_id() -> None:
    plan = option_plan()
    scope = OptionExecutionScope.for_plan(
        plan,
        experiment_id="f2-g4a-run-id",
        world_lease_id="dry-run-world",
    )
    grant = OptionExecutionGrant.for_plan(
        plan,
        scope=scope,
        issued_by="f2-g4a-test",
        reason="run id is mandatory",
        now=FIXED_NOW,
    )

    with pytest.raises(ValueError, match="run_id"):
        replace(grant, run_id=None)
