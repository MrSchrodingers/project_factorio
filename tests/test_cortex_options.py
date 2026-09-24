from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from factorio_ai_lab.cortex.actions import ActionAuthority, ActionProvenance
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.options import (
    REFUSAL_OPTION_BRANCH_AMBIGUOUS,
    REFUSAL_OPTION_EXECUTE_NOT_AUTHORIZED,
    REFUSAL_OPTION_PROVENANCE_MISMATCH,
    OptionBudget,
    OptionKind,
    OptionRequest,
    compose_processing_chain_option,
)
from factorio_ai_lab.instrumentation.runtime import runtime_game_ticks
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
                "name": "copper-plate",
                "energy": 3.2,
                "categories": ["smelting"],
                "ingredients": [{"name": "copper-ore", "amount": 1}],
                "products": [{"name": "copper-plate", "amount": 1}],
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
                "crafting_speed": 1,
                "crafting_speed_status": PROBE_MEASURED,
                "energy_source_status": PROBE_ABSENT,
                "fuel_categories_status": PROBE_ABSENT,
                "energy_usage_status": PROBE_ABSENT,
            },
            {
                "name": "stone-furnace",
                "type": "furnace",
                "crafting_categories": ["smelting"],
                "crafting_speed": 1,
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


def action_request(*targets: str):
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=targets,
        arguments={"producers": list(targets)},
    )
    return request_from_repair_action(
        repair,
        action_id="option-child-action",
        provenance=ActionProvenance(
            requested_by="option-test",
            source_component="tests.test_cortex_options",
            code_revision="sha",
            run_id="run",
        ),
    )


def option(
    authority: ActionAuthority = ActionAuthority.SHADOW,
    *,
    budget: OptionBudget | None = None,
) -> OptionRequest:
    return OptionRequest(
        option_id="processing-chain-1",
        kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
        goal="establish one functional processing chain",
        provenance=ActionProvenance(
            requested_by="option-test",
            source_component="tests.test_cortex_options",
            code_revision="sha",
            run_id="run",
        ),
        budget=budget or OptionBudget(requested_ticks=600),
        authority=authority,
    )


def iron_world():
    return [
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


def inventory():
    return {
        "stone": 5,
        "inserter": 50,
        "burner-inserter": 50,
        "coal": 480,
        "transport-belt": 20,
    }


def operation(action, name: str):
    return next(row for row in action.operations if row.op == name)


def test_option_budget_never_treats_requested_ticks_as_observed() -> None:
    budget = OptionBudget(requested_ticks=600)

    assert budget.requested_seconds == pytest.approx(10.0)
    assert budget.observed_ticks is None
    assert budget.effective_ticks is None
    assert budget.planning_ticks == 600
    assert budget.planning_seconds == pytest.approx(10.0)
    assert budget.planning_source == "requested_budget"
    assert budget.sustainability_evaluable is False
    assert budget.budget_overrun is None


def test_option_budget_records_observed_game_ticks_separately() -> None:
    budget = OptionBudget(
        requested_ticks=600,
        observed_ticks=4800,
        observed_source="observed_game_ticks",
    )

    assert budget.requested_seconds == pytest.approx(10.0)
    assert budget.observed_seconds == pytest.approx(80.0)
    assert budget.effective_ticks == 4800
    assert budget.planning_ticks == 4800
    assert budget.planning_seconds == pytest.approx(80.0)
    assert budget.planning_source == "observed_game_ticks"
    assert budget.sustainability_evaluable is True
    assert budget.budget_overrun is True


def test_runtime_game_ticks_accepts_env_or_instance_and_fails_closed() -> None:
    instance = SimpleNamespace(get_elapsed_ticks=lambda: 1234)
    env = SimpleNamespace(unwrapped=SimpleNamespace(instance=instance))

    assert runtime_game_ticks(instance) == 1234
    assert runtime_game_ticks(env) == 1234
    assert runtime_game_ticks(object()) is None


def test_processing_chain_option_composes_existing_dependencies_without_mutation() -> None:
    world = iron_world()
    before = deepcopy(world)

    result = compose_processing_chain_option(
        option(),
        action_request=action_request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory=inventory(),
        electric_power_available=False,
    )

    assert result.ready is True
    assert result.refusal is None
    assert result.plan is not None
    plan = result.plan
    assert world == before
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version == "cortex_structural_ops_v3"
    assert plan.branch.material == "iron-ore"
    assert plan.branch.product == "iron-plate"
    assert plan.processor_fuel is not None
    assert plan.processor_fuel.fuel.name == "coal"
    assert plan.actuator_dependency is not None
    assert plan.actuator_dependency.actuator == "burner-inserter"
    assert plan.actuator_dependency.fuel_dependency is not None
    assert plan.actuator_dependency.fuel_dependency.fuel.name == "coal"
    assert [step.component for step in plan.steps] == [
        "factorio_ai_lab.cortex.structural",
        "factorio_ai_lab.cortex.structural_prepare",
        "factorio_ai_lab.cortex.functional_dependency",
        "factorio_ai_lab.cortex.delivery_actuator_dependency",
    ]
    assert all(
        step.details.get("planning_horizon_ticks") == 600
        for step in plan.steps[2:]
    )
    assert plan.request.budget.effective_ticks is None
    assert plan.request.budget.sustainability_evaluable is False
    assert plan.action_request.provenance.parent_action_id == "processing-chain-1"
    assert plan.branch.request.provenance.parent_action_id == "option-child-action"
    assert [condition.name for condition in plan.termination_conditions] == [
        "physical_factory_graph.producers_reaching_processor",
        "processor_exists",
        "processor_output",
    ]
    assert [condition.name for condition in plan.predicted_effects] == [
        "physical_factory_graph.producers_reaching_processor",
        "processor_exists",
        "processor_output",
    ]


def test_observed_tick_horizon_resizes_processor_and_actuator_energy() -> None:
    world = iron_world()
    result = compose_processing_chain_option(
        option(
            budget=OptionBudget(
                requested_ticks=600,
                observed_ticks=3000,
                observed_source="FLE.get_elapsed_ticks",
            )
        ),
        action_request=action_request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory=inventory(),
        electric_power_available=False,
    )

    assert result.ready is True
    assert result.plan is not None
    plan = result.plan
    assert plan.request.budget.planning_ticks == 3000
    assert plan.request.budget.planning_seconds == pytest.approx(50.0)
    assert all(
        step.details.get("planning_horizon_ticks") == 3000
        for step in plan.steps[2:]
    )
    assert all(
        step.details.get("planning_horizon_source") == "observed_game_ticks"
        for step in plan.steps[2:]
    )

    processor_fuel = operation(plan.prepared, "fuel_processor")
    actuator_fuel = operation(plan.prepared, "fuel_delivery_actuator")
    assert processor_fuel.parameters["quantity"] == 2
    assert actuator_fuel.parameters["quantity"] == 3


def test_processing_chain_option_refuses_implicit_choice_between_materials() -> None:
    world = iron_world() + [
        entity("burner-mining-drill", 20, 0, unit=3, direction=8),
        entity(
            "wooden-chest",
            20,
            2,
            unit=4,
            contents=[{"name": "copper-ore", "count": 15}],
        ),
    ]

    result = compose_processing_chain_option(
        option(),
        action_request=action_request("u1", "u3"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory={**inventory(), "stone": 10},
        electric_power_available=False,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_BRANCH_AMBIGUOUS
    assert "copper-ore" in result.refusal.detail
    assert "iron-ore" in result.refusal.detail


def test_f2g2_option_refuses_execute_authority() -> None:
    world = iron_world()

    result = compose_processing_chain_option(
        option(ActionAuthority.EXECUTE),
        action_request=action_request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory=inventory(),
        electric_power_available=False,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_EXECUTE_NOT_AUTHORIZED

def test_option_budget_requires_integer_ticks() -> None:
    with pytest.raises(ValueError):
        OptionBudget(requested_ticks=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OptionBudget(requested_ticks=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OptionBudget(
            requested_ticks=600,
            observed_ticks=1.5,  # type: ignore[arg-type]
            observed_source="observed_game_ticks",
        )


def test_option_refuses_mixed_code_lineage() -> None:
    world = iron_world()
    request = action_request("u1")
    mismatched = OptionRequest(
        option_id="processing-chain-mixed-lineage",
        kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
        goal="establish one functional processing chain",
        provenance=ActionProvenance(
            requested_by="option-test",
            source_component="tests.test_cortex_options",
            code_revision="different-sha",
            run_id="run",
        ),
        budget=OptionBudget(requested_ticks=600),
    )

    result = compose_processing_chain_option(
        mismatched,
        action_request=request,
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        inventory=inventory(),
        electric_power_available=False,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_OPTION_PROVENANCE_MISMATCH
