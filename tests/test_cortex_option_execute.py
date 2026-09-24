from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionProvenance,
    ActionStatus,
)
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.option_execute import (
    REFUSAL_OPTION_EXECUTION_GRANT_REUSED,
    REFUSAL_OPTION_EXECUTION_LINEAGE,
    REFUSAL_OPTION_EXECUTION_TERMINATION,
    REFUSAL_OPTION_EXECUTION_TICK_SOURCE,
    OptionExecutionBoundary,
    OptionExecutionGrant,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
    compose_processing_chain_option,
)
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    RuntimeFactorioCatalog,
)


def entity(name, x, y, *, unit, contents=None, direction=0):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "unit_number": unit,
        "direction": direction,
        "status": "working",
        "contents": [] if contents is None else contents,
    }


def catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog({
        "recipes": [
            {
                "name": "iron-plate",
                "energy": 3.2,
                "categories": ["smelting"],
                "ingredients": [{"name": "iron-ore", "amount": 1}],
                "products": [{"name": "iron-plate", "amount": 1}],
                "enabled": True,
            },
            {
                "name": "stone-furnace",
                "energy": 0.5,
                "categories": ["crafting"],
                "ingredients": [{"name": "stone", "amount": 5}],
                "products": [{"name": "stone-furnace", "amount": 1}],
                "enabled": True,
            },
        ],
        "technologies": [],
        "machines": [
            {
                "name": "character",
                "type": "character",
                "crafting_categories": ["crafting"],
                "crafting_speed": 1.0,
                "crafting_speed_status": PROBE_MEASURED,
                "energy_source_status": PROBE_ABSENT,
                "energy_usage_status": PROBE_ABSENT,
                "fuel_categories_status": PROBE_ABSENT,
            },
            {
                "name": "stone-furnace",
                "type": "furnace",
                "crafting_categories": ["smelting"],
                "crafting_speed": 1.0,
                "crafting_speed_status": PROBE_MEASURED,
                "energy_source_type": "burner",
                "energy_source_status": PROBE_MEASURED,
                "energy_usage_per_tick_j": 1500,
                "energy_usage_status": PROBE_MEASURED,
                "fuel_categories": ["chemical"],
                "fuel_categories_status": PROBE_MEASURED,
            },
            {
                "name": "inserter",
                "type": "inserter",
                "energy_source_type": "electric",
                "energy_source_status": PROBE_MEASURED,
                "energy_usage_per_tick_j": 245,
                "energy_usage_status": PROBE_MEASURED,
                "fuel_categories": [],
                "fuel_categories_status": PROBE_ABSENT,
            },
            {
                "name": "burner-inserter",
                "type": "inserter",
                "energy_source_type": "burner",
                "energy_source_status": PROBE_MEASURED,
                "energy_usage_per_tick_j": 2400,
                "energy_usage_status": PROBE_MEASURED,
                "fuel_categories": ["chemical"],
                "fuel_categories_status": PROBE_MEASURED,
            },
        ],
        "fuels": [
            {
                "name": "coal",
                "fuel_value_j": 4_000_000,
                "fuel_value_status": PROBE_MEASURED,
                "fuel_categories": ["chemical"],
                "fuel_categories_status": PROBE_MEASURED,
            },
        ],
        "belts": [],
    })


def action_request():
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=("u1",),
        arguments={"producers": ["u1"]},
    )
    return request_from_repair_action(
        repair,
        action_id="f2g3-action",
        provenance=ActionProvenance(
            requested_by="f2-g3-test",
            source_component="tests.test_cortex_option_execute",
            code_revision="f2g3-sha",
            run_id="f2g3-run",
        ),
    )


def option_plan():
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 20}],
        ),
    ]
    option = OptionRequest(
        option_id="f2g3-processing-chain",
        kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
        goal="establish one functional processing chain",
        provenance=ActionProvenance(
            requested_by="f2-g3-test",
            source_component="tests.test_cortex_option_execute",
            code_revision="f2g3-sha",
            run_id="f2g3-run",
        ),
        budget=OptionBudget(requested_ticks=600),
        authority=ActionAuthority.SHADOW,
    )
    result = compose_processing_chain_option(
        option,
        action_request=action_request(),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory={
            "stone": 5,
            "inserter": 50,
            "burner-inserter": 50,
            "coal": 480,
            "transport-belt": 20,
        },
        electric_power_available=False,
    )
    assert result.ready is True
    assert result.plan is not None
    return result.plan


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


class FakeTickingEnvironment:
    def __init__(self, *, promotes: bool) -> None:
        self.promotes = promotes
        self.initial_ticks = 1_000
        self.ticks = self.initial_ticks
        self.initial = {
            "producers_reaching_processor": 3,
            "physical_processing_coverage": 0.5,
            "processor_exists": None,
            "processor_status": None,
            "processor_output": 0.0,
        }
        self.state = deepcopy(self.initial)
        self.last_action: FakeAction | None = None
        self.step_calls = 0

    def get_elapsed_ticks(self) -> int:
        return self.ticks

    def reset(self, *, options=None, seed=None):
        del seed
        game_state = None if options is None else options.get("game_state")
        self.state = deepcopy(self.initial if game_state is None else game_state)
        self.ticks = self.initial_ticks
        return {"state": deepcopy(self.state)}

    def step(self, action: FakeAction):
        self.step_calls += 1
        self.last_action = action
        self.ticks += 600
        if self.promotes:
            self.state["producers_reaching_processor"] = 4
            self.state["physical_processing_coverage"] = 2 / 3
            self.state["processor_exists"] = True
            self.state["processor_status"] = "working"
            self.state["processor_output"] = 1.0
        return (
            {"raw_text": action.code},
            1.0,
            False,
            False,
            {
                "output_game_state": deepcopy(self.state),
                "error_occurred": False,
            },
        )

    def close(self) -> None:
        pass


def transactional_executor(
    env: FakeTickingEnvironment,
) -> TransactionalFLEExecutor:
    tx = TransactionalFLEExecutor(
        env,
        action_factory=fake_action_factory,
    )
    tx.reset(seed=17, game_state=deepcopy(env.initial))
    return tx


def probe(env: FakeTickingEnvironment):
    def measure(_prepared):
        return deepcopy(env.state)

    return measure


def grant(plan):
    return OptionExecutionGrant.for_plan(
        plan,
        issued_by="f2-g3-test",
        reason="transactional fake validation",
    )


def test_shadow_validates_plan_without_runtime_or_mutation() -> None:
    plan = option_plan()

    result = OptionExecutionBoundary().execute(
        plan,
        authority=ActionAuthority.SHADOW,
    )

    assert result.status is ActionStatus.SHADOWED
    assert result.changed_world is False
    assert result.action_result is None
    assert result.tick_measurement_status == "not_requested"
    assert result.lineage["option_id"] == plan.request.option_id
    assert result.lineage["action_request_id"] == plan.action_request.action_id
    assert result.lineage["branch_action_id"] == plan.branch.request.action_id
    assert result.lineage["prepared_action_id"] == plan.prepared.action_id
    assert result.to_dict()["continuous_authority"] is False


def test_execute_requires_observable_tick_source_before_mutation() -> None:
    plan = option_plan()
    env = FakeTickingEnvironment(promotes=True)
    tx = transactional_executor(env)
    boundary = OptionExecutionBoundary()
    execution_grant = grant(plan)

    refused = boundary.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=execution_grant,
        executor=tx,
        measure=probe(env),
        tick_source=None,
    )

    assert refused.status is ActionStatus.REFUSED
    assert refused.refusal is not None
    assert refused.refusal.code == REFUSAL_OPTION_EXECUTION_TICK_SOURCE
    assert env.step_calls == 0
    assert env.last_action is None

    accepted = boundary.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=execution_grant,
        executor=tx,
        measure=probe(env),
        tick_source=env,
    )
    assert accepted.status is ActionStatus.ACCEPTED
    assert env.step_calls == 1


def test_fake_execute_commits_and_feeds_observed_ticks_back_to_budget() -> None:
    plan = option_plan()
    env = FakeTickingEnvironment(promotes=True)
    tx = transactional_executor(env)
    boundary = OptionExecutionBoundary()

    result = boundary.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant(plan),
        executor=tx,
        measure=probe(env),
        tick_source=env,
    )

    assert result.status is ActionStatus.ACCEPTED
    assert result.changed_world is True
    assert result.action_result is not None
    assert result.action_result.status is ActionStatus.ACCEPTED
    assert result.ticks_before == 1_000
    assert result.ticks_after == 1_600
    assert result.observed_ticks == 600
    assert result.tick_measurement_status == "observed"
    assert result.feedback_budget is not None
    assert result.feedback_budget.observed_ticks == 600
    assert result.feedback_budget.observed_source is not None
    assert result.feedback_budget.sustainability_evaluable is True
    assert result.lineage["code_revision"] == "f2g3-sha"
    assert result.lineage["run_id"] == "f2g3-run"


def test_rejected_transaction_does_not_claim_zero_ticks_after_rollback() -> None:
    plan = option_plan()
    env = FakeTickingEnvironment(promotes=False)
    tx = transactional_executor(env)

    result = OptionExecutionBoundary().execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=grant(plan),
        executor=tx,
        measure=probe(env),
        tick_source=env,
    )

    assert result.status is ActionStatus.REJECTED
    assert result.changed_world is False
    assert result.ticks_before == 1_000
    assert result.ticks_after == 1_000
    assert result.observed_ticks is None
    assert result.tick_measurement_status == "missing_after_rollback"
    assert result.feedback_budget is None
    assert env.state == env.initial
    assert tx.game_state == env.initial


def test_one_boundary_consumes_frozen_plan_grant_once() -> None:
    plan = option_plan()
    env = FakeTickingEnvironment(promotes=True)
    tx = transactional_executor(env)
    boundary = OptionExecutionBoundary()
    execution_grant = grant(plan)

    first = boundary.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=execution_grant,
        executor=tx,
        measure=probe(env),
        tick_source=env,
    )
    assert first.status is ActionStatus.ACCEPTED
    assert env.step_calls == 1

    second = boundary.execute(
        plan,
        authority=ActionAuthority.EXECUTE,
        grant=execution_grant,
        executor=tx,
        measure=probe(env),
        tick_source=env,
    )
    assert second.status is ActionStatus.REFUSED
    assert second.refusal is not None
    assert second.refusal.code == REFUSAL_OPTION_EXECUTION_GRANT_REUSED
    assert env.step_calls == 1


def test_lineage_mismatch_is_refused_before_authority() -> None:
    plan = option_plan()
    bad_action = replace(
        plan.action_request,
        provenance=replace(
            plan.action_request.provenance,
            parent_action_id="wrong-option",
        ),
    )
    bad_plan = replace(plan, action_request=bad_action)

    result = OptionExecutionBoundary().execute(
        bad_plan,
        authority=ActionAuthority.SHADOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_LINEAGE


def test_termination_contract_mismatch_is_refused_before_authority() -> None:
    plan = option_plan()
    bad_plan = replace(
        plan,
        termination_conditions=plan.termination_conditions[:-1],
    )

    result = OptionExecutionBoundary().execute(
        bad_plan,
        authority=ActionAuthority.SHADOW,
    )

    assert result.status is ActionStatus.REFUSED
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTION_TERMINATION


def test_option_execution_modules_do_not_import_curriculum_runner() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "src/factorio_ai_lab/cortex/options.py",
        root / "src/factorio_ai_lab/cortex/option_execute.py",
    ]
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not any("curriculum_runner" in module for module in imported)
