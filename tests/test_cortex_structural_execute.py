from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionStatus,
)
from factorio_ai_lab.cortex.structural_execute import (
    REFUSAL_EXECUTE_AUTHORITY_REQUIRED,
    REFUSAL_MEASUREMENT_FAILED,
    REFUSAL_OPERATION_UNSUPPORTED,
    REFUSAL_POSTCONDITION_FAILED,
    REFUSAL_TRANSACTION_FAILED,
    StructuralTransactionalAdapter,
    compile_structural_action,
    prototype_symbol,
)
from factorio_ai_lab.cortex.structural_prepare import (
    CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor


@dataclass(frozen=True)
class FakeAction:
    agent_idx: int
    code: str
    game_state: dict[str, Any] | None


def fake_action_factory(
    agent_idx: int,
    code: str,
    game_state: dict[str, Any] | None,
) -> FakeAction:
    return FakeAction(agent_idx, code, deepcopy(game_state))


class FakeStructuralEnvironment:
    def __init__(self, *, promotes: bool, engine_error: bool = False, processor_exists: bool = True) -> None:
        self.promotes = promotes
        self.engine_error = engine_error
        self.processor_exists = processor_exists
        self.initial = {
            "producers_reaching_processor": 3,
            "physical_processing_coverage": 0.5,
            "processor_exists": None,
            "processor_output": 0.0,
        }
        self.state = deepcopy(self.initial)
        self.reset_calls: list[dict[str, Any] | None] = []
        self.last_action: FakeAction | None = None

    def reset(self, *, options=None, seed=None):
        del seed
        game_state = None if options is None else options.get("game_state")
        self.state = deepcopy(self.initial if game_state is None else game_state)
        self.reset_calls.append(deepcopy(game_state))
        return {"state": deepcopy(self.state)}

    def step(self, action: FakeAction):
        self.last_action = action
        if self.promotes:
            self.state["producers_reaching_processor"] = 4
            self.state["physical_processing_coverage"] = 2 / 3
            self.state["processor_exists"] = self.processor_exists
            self.state["processor_output"] = 1.0
        return (
            {"raw_text": action.code},
            1.0,
            False,
            False,
            {
                "output_game_state": deepcopy(self.state),
                "error_occurred": self.engine_error,
            },
        )

    def close(self) -> None:
        pass


def prepared_action(
    *,
    delivery_mode: str = "inserter",
    entity: str = "inserter",
) -> PreparedStructuralAction:
    return PreparedStructuralAction(
        action_id="f2e-action",
        family=ActionFamily.PLACEMENT,
        intent="place_processing_for_buffered_output",
        binding="cortex.structural.processing",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=CONTRACT_VERSION,
        measurement_keys=(
            "producers_reaching_processor",
            "physical_processing_coverage",
            "processor_exists",
            "processor_status",
            "processor_output",
        ),
        operations=(
            StructuralOperation(
                op="ensure_item",
                parameters={"item": "stone-furnace", "quantity": 1},
            ),
            StructuralOperation(
                op="place_processor",
                parameters={
                    "entity": "stone-furnace",
                    "position": {"x": 30.0, "y": 85.0},
                },
            ),
            StructuralOperation(
                op="configure_processing",
                parameters={
                    "material": "iron-ore",
                    "recipe": "iron-plate",
                    "product": "iron-plate",
                    "processor": "stone-furnace",
                },
            ),
            StructuralOperation(
                op="connect_delivery",
                parameters={
                    "mode": delivery_mode,
                    "entities": [entity],
                    "source_buffer": "u1854",
                    "target_position": {"x": 30.0, "y": 85.0},
                    "delivery": {
                        "mode": delivery_mode,
                        "lift": {
                            "position": {"x": 28.5, "y": 84.5},
                            "direction": "RIGHT",
                            "picks_from": {"x": 27, "y": 84},
                            "drops_at": {"x": 29, "y": 84},
                        },
                        "drop": None,
                        "path": [],
                        "belt_count": 0,
                        "reason": None,
                    },
                },
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={
                    "conditions": [
                        {
                            "name": (
                                "physical_factory_graph."
                                "producers_reaching_processor"
                            ),
                            "operator": "increase",
                            "state": "unknown",
                            "expected": None,
                            "hard": True,
                            "evidence": [],
                        },
                    ],
                },
            ),
        ),
        preflight={
            "material": "iron-ore",
            "product": "iron-plate",
            "processor": "stone-furnace",
            "world_mutation": False,
        },
    )


def probe(env: FakeStructuralEnvironment):
    def measure(_prepared: PreparedStructuralAction) -> dict[str, Any]:
        return deepcopy(env.state)

    return measure


def executor(env: FakeStructuralEnvironment) -> TransactionalFLEExecutor:
    result = TransactionalFLEExecutor(
        env,
        action_factory=fake_action_factory,
    )
    result.reset(seed=17, game_state=deepcopy(env.initial))
    return result


def test_prototype_symbol_is_validated_against_real_fle_enum() -> None:
    assert prototype_symbol("stone-furnace") == "Prototype.StoneFurnace"
    assert prototype_symbol("iron-ore") == "Prototype.IronOre"
    assert prototype_symbol("iron-plate") == "Prototype.IronPlate"
    assert prototype_symbol("inserter") == "Prototype.Inserter"


def test_compiler_generates_deterministic_direct_inserter_transaction() -> None:
    result = compile_structural_action(prepared_action())

    assert result.ready is True
    assert result.compiled is not None
    code = result.compiled.code
    assert "Prototype.StoneFurnace" in code
    assert "Prototype.Inserter" in code
    assert "Direction.RIGHT" in code
    assert "Position(x=30.0,y=85.0)" in code
    assert "set_entity_recipe" not in code
    assert "sleep(8)" in code
    assert result.compiled.purpose == "infrastructure"
    assert result.compiled.settle_seconds == 8


def test_compiler_refuses_unsupported_delivery_mode() -> None:
    result = compile_structural_action(prepared_action(delivery_mode="belt"))

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPERATION_UNSUPPORTED


def test_non_execute_authority_never_calls_transaction_executor() -> None:
    env = FakeStructuralEnvironment(promotes=True)
    tx = executor(env)
    reset_count = len(env.reset_calls)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.SHADOW,
        executor=tx,
        measure=probe(env),
    )

    assert result.status is ActionStatus.REFUSED
    assert result.changed_world is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_EXECUTE_AUTHORITY_REQUIRED
    assert env.last_action is None
    assert len(env.reset_calls) == reset_count


def test_execute_commits_only_after_hard_postcondition_increases() -> None:
    env = FakeStructuralEnvironment(promotes=True)
    tx = executor(env)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.EXECUTE,
        executor=tx,
        measure=probe(env),
    )

    assert result.status is ActionStatus.ACCEPTED
    assert result.changed_world is True
    assert all(
        condition.state.value == "satisfied"
        for condition in result.postconditions
        if condition.hard
    )
    assert env.state["producers_reaching_processor"] == 4
    assert env.state["processor_output"] == 1.0
    assert tx.game_state["producers_reaching_processor"] == 4
    assert result.measurements["before"]["producers_reaching_processor"] == 3
    assert result.measurements["candidate_after"]["producers_reaching_processor"] == 4
    counters = tx.intervention_snapshot()
    assert isinstance(counters["attempted_infrastructure"], dict)


def test_failed_hard_postcondition_rolls_back_exact_checkpoint() -> None:
    env = FakeStructuralEnvironment(promotes=False)
    tx = executor(env)
    before = deepcopy(tx.game_state)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.EXECUTE,
        executor=tx,
        measure=probe(env),
    )

    assert result.status is ActionStatus.REJECTED
    assert result.changed_world is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_POSTCONDITION_FAILED
    assert result.postconditions[0].state.value == "unsatisfied"
    assert env.state == before
    assert tx.game_state == before


def test_measurement_failure_rejects_and_rolls_back() -> None:
    env = FakeStructuralEnvironment(promotes=True)
    tx = executor(env)
    calls = 0

    def failing_measure(_prepared: PreparedStructuralAction):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("probe unavailable")
        return deepcopy(env.state)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.EXECUTE,
        executor=tx,
        measure=failing_measure,
    )

    assert result.status is ActionStatus.REJECTED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_MEASUREMENT_FAILED
    assert env.state["producers_reaching_processor"] == 3
    assert tx.game_state["producers_reaching_processor"] == 3


def test_engine_error_is_distinct_from_postcondition_failure() -> None:
    env = FakeStructuralEnvironment(promotes=True, engine_error=True)
    tx = executor(env)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.EXECUTE,
        executor=tx,
        measure=probe(env),
    )

    assert result.status is ActionStatus.REJECTED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_TRANSACTION_FAILED
    assert env.state["producers_reaching_processor"] == 3
    assert tx.game_state["producers_reaching_processor"] == 3

def test_false_processor_exists_cannot_satisfy_execution_guard() -> None:
    env = FakeStructuralEnvironment(promotes=True, processor_exists=False)
    tx = executor(env)
    before = deepcopy(tx.game_state)

    result = StructuralTransactionalAdapter().execute(
        prepared_action(),
        authority=ActionAuthority.EXECUTE,
        executor=tx,
        measure=probe(env),
    )

    assert result.status is ActionStatus.REJECTED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_POSTCONDITION_FAILED
    existence = next(
        condition
        for condition in result.postconditions
        if condition.name == "processor_exists"
    )
    assert existence.state.value == "unsatisfied"
    assert env.state == before
    assert tx.game_state == before
