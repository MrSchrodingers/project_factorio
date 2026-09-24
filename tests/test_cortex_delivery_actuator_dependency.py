from __future__ import annotations

from factorio_ai_lab.cortex.actions import ActionFamily
from factorio_ai_lab.cortex.delivery_actuator_dependency import (
    REFUSAL_DELIVERY_NO_SATISFIABLE_ACTUATOR,
    REFUSAL_DELIVERY_POWER_UNAVAILABLE,
    REFUSAL_DELIVERY_POWER_UNMEASURED,
    complete_delivery_actuator_dependency,
)
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.cortex.structural_prepare import (
    DELIVERY_ACTUATOR_CONTRACT_VERSION,
    FUNCTIONAL_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    RuntimeFactorioCatalog,
)


def prepared() -> PreparedStructuralAction:
    return PreparedStructuralAction(
        action_id="f2f4-action",
        family=ActionFamily.PLACEMENT,
        intent="place_processing_for_buffered_output",
        binding="cortex.structural.processing",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=FUNCTIONAL_CONTRACT_VERSION,
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
                op="fuel_processor",
                parameters={
                    "fuel_item": "coal",
                    "quantity": 1,
                    "supply_plan": {
                        "fuel_draws": [],
                    },
                },
            ),
            StructuralOperation(
                op="connect_delivery",
                parameters={
                    "mode": "inserter",
                    "entities": ["inserter"],
                    "source_buffer": "u256",
                    "target_position": {"x": 30.0, "y": 85.0},
                    "delivery": {
                        "mode": "inserter",
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
        measurement_keys=(
            "producers_reaching_processor",
            "physical_processing_coverage",
            "processor_exists",
            "processor_status",
            "processor_output",
        ),
        preflight={
            "processor": "stone-furnace",
            "material": "iron-ore",
            "product": "iron-plate",
            "placement": {
                "outcome": "build",
                "position": {"x": 30.0, "y": 85.0},
            },
        },
    )


def catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog(
        {
            "connected": True,
            "recipes": [],
            "technologies": [],
            "machines": [
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
        }
    )


def operation_names(action: PreparedStructuralAction) -> list[str]:
    return [operation.op for operation in action.operations]


def delivery_entities(action: PreparedStructuralAction) -> list[str]:
    operation = next(
        operation
        for operation in action.operations
        if operation.op == "connect_delivery"
    )
    return list(operation.parameters["entities"])


def test_no_power_selects_measured_burner_actuator_with_covered_fuel() -> None:
    result = complete_delivery_actuator_dependency(
        prepared(),
        catalog=catalog(),
        inventory={
            "inserter": 50,
            "burner-inserter": 50,
            "coal": 480,
        },
        electric_power_available=False,
        horizon_s=10,
    )

    assert result.ready is True
    assert result.dependency is not None
    assert result.dependency.actuator == "burner-inserter"
    assert result.dependency.energy.source_type == "burner"
    assert result.dependency.fuel_dependency is not None
    assert result.dependency.fuel_dependency.fuel.name == "coal"

    assert [row.actuator for row in result.evaluations] == [
        "inserter",
        "burner-inserter",
    ]
    assert result.evaluations[0].refusal is not None
    assert result.evaluations[0].refusal.code == REFUSAL_DELIVERY_POWER_UNAVAILABLE
    assert result.evaluations[1].ready is True

    completed = result.prepared
    assert completed is not None
    assert completed.contract_version == DELIVERY_ACTUATOR_CONTRACT_VERSION
    assert delivery_entities(completed) == ["burner-inserter"]
    names = operation_names(completed)
    assert names.index("connect_delivery") < names.index(
        "fuel_delivery_actuator"
    )
    assert (
        completed.preflight["delivery_actuator_dependency"]["actuator"]
        == "burner-inserter"
    )


def test_observed_power_keeps_requested_electric_actuator() -> None:
    result = complete_delivery_actuator_dependency(
        prepared(),
        catalog=catalog(),
        inventory={
            "inserter": 50,
            "burner-inserter": 50,
            "coal": 480,
        },
        electric_power_available=True,
    )

    assert result.ready is True
    assert result.dependency is not None
    assert result.dependency.actuator == "inserter"
    assert result.dependency.energy.source_type == "electric"
    assert result.dependency.fuel_dependency is None
    assert result.prepared is not None
    assert delivery_entities(result.prepared) == ["inserter"]
    assert "fuel_delivery_actuator" not in operation_names(result.prepared)


def test_unmeasured_power_does_not_license_electric_actuator() -> None:
    result = complete_delivery_actuator_dependency(
        prepared(),
        catalog=catalog(),
        inventory={"inserter": 50},
        electric_power_available=None,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_DELIVERY_NO_SATISFIABLE_ACTUATOR
    electric = result.evaluations[0]
    assert electric.actuator == "inserter"
    assert electric.refusal is not None
    assert electric.refusal.code == REFUSAL_DELIVERY_POWER_UNMEASURED


def test_burner_actuator_without_covered_fuel_is_not_selected() -> None:
    result = complete_delivery_actuator_dependency(
        prepared(),
        catalog=catalog(),
        inventory={"burner-inserter": 50},
        electric_power_available=False,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_DELIVERY_NO_SATISFIABLE_ACTUATOR
    burner = next(
        row for row in result.evaluations
        if row.actuator == "burner-inserter"
    )
    assert burner.ready is False
    assert burner.refusal is not None
    assert burner.fuel_evaluations
    assert all(not row.covered for row in burner.fuel_evaluations)


def test_v3_burner_actuator_compiles_to_place_then_fuel() -> None:
    result = complete_delivery_actuator_dependency(
        prepared(),
        catalog=catalog(),
        inventory={
            "inserter": 50,
            "burner-inserter": 50,
            "coal": 480,
        },
        electric_power_available=False,
        horizon_s=10,
    )
    assert result.prepared is not None

    compiled = compile_structural_action(result.prepared)

    assert compiled.ready is True
    assert compiled.compiled is not None
    code = compiled.compiled.code
    assert "Prototype.BurnerInserter" in code
    assert "cortex_delivery=place_entity(" in code
    assert "cortex_delivery=insert_item(" in code
    assert "Prototype.Coal" in code
    assert code.index("cortex_delivery=place_entity(") < code.index(
        "cortex_delivery=insert_item("
    )
    assert "fuel_delivery_actuator" in compiled.compiled.operation_names
