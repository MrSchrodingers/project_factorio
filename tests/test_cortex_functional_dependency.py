from __future__ import annotations

from factorio_ai_lab.cortex.actions import ActionFamily
from factorio_ai_lab.cortex.functional_dependency import (
    REFUSAL_ENERGY_SOURCE_NOT_COMPOSED,
    REFUSAL_FUEL_SUPPLY_UNAVAILABLE,
    complete_structural_dependencies,
)
from factorio_ai_lab.cortex.structural_execute import (
    REFUSAL_WORLD_FUEL_DRAW_UNSUPPORTED,
    compile_structural_action,
)
from factorio_ai_lab.cortex.structural_prepare import (
    CONTRACT_VERSION,
    FUNCTIONAL_CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.resupply import FuelSource
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    RuntimeFactorioCatalog,
)


def prepared(processor: str = "stone-furnace") -> PreparedStructuralAction:
    return PreparedStructuralAction(
        action_id="f2f-action",
        family=ActionFamily.PLACEMENT,
        intent="place_processing_for_buffered_output",
        binding="cortex.structural.processing",
        purpose=PURPOSE_INFRASTRUCTURE,
        contract_version=CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="ensure_item",
                parameters={"item": processor, "quantity": 1},
            ),
            StructuralOperation(
                op="place_processor",
                parameters={
                    "entity": processor,
                    "position": {"x": 30.0, "y": 85.0},
                },
            ),
            StructuralOperation(
                op="configure_processing",
                parameters={
                    "material": "iron-ore",
                    "recipe": "iron-plate",
                    "product": "iron-plate",
                    "processor": processor,
                },
            ),
            StructuralOperation(
                op="connect_delivery",
                parameters={
                    "mode": "inserter",
                    "entities": ["inserter"],
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
            "processor": processor,
            "material": "iron-ore",
            "product": "iron-plate",
            "placement": {
                "outcome": "build",
                "position": {"x": 30.0, "y": 85.0},
            },
        },
    )


def catalog(*, electric: bool = False) -> RuntimeFactorioCatalog:
    source = "electric" if electric else "burner"
    categories = [] if electric else ["chemical"]
    category_status = PROBE_ABSENT if electric else PROBE_MEASURED
    return RuntimeFactorioCatalog(
        {
            "connected": True,
            "recipes": [],
            "technologies": [],
            "machines": [
                {
                    "name": "stone-furnace",
                    "type": "furnace",
                    "crafting_categories": ["smelting"],
                    "crafting_speed": 1.0,
                    "crafting_speed_status": PROBE_MEASURED,
                    "mining_speed_status": PROBE_ABSENT,
                    "energy_source_type": source,
                    "energy_source_status": PROBE_MEASURED,
                    "energy_usage_per_tick_j": 1500,
                    "energy_usage_status": PROBE_MEASURED,
                    "fuel_categories": categories,
                    "fuel_categories_status": category_status,
                },
            ],
            "fuels": [
                {
                    "name": "nuclear-fuel",
                    "fuel_value_j": 1_210_000_000,
                    "fuel_value_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                },
                {
                    "name": "coal",
                    "fuel_value_j": 4_000_000,
                    "fuel_value_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                },
                {
                    "name": "wood",
                    "fuel_value_j": 2_000_000,
                    "fuel_value_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                },
            ],
            "belts": [],
        }
    )


def test_available_coal_beats_unavailable_higher_density_fuel() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(),
        inventory={"coal": 20},
        horizon_s=10,
    )

    assert result.ready is True
    assert result.dependency is not None
    assert result.dependency.fuel.name == "coal"
    assert result.dependency.units_needed == 1
    assert result.dependency.carried_only is True
    assert [row.fuel.name for row in result.evaluations] == [
        "nuclear-fuel",
        "coal",
    ]
    assert result.evaluations[0].covered is False
    assert result.evaluations[1].covered is True

    completed = result.prepared
    assert completed is not None
    assert completed.contract_version == FUNCTIONAL_CONTRACT_VERSION
    names = [operation.op for operation in completed.operations]
    assert names.index("fuel_processor") < names.index("connect_delivery")
    assert completed.preflight["energy_dependency"]["fuel"]["name"] == "coal"


def test_selection_falls_through_to_measured_available_wood() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(),
        inventory={"wood": 20},
        horizon_s=10,
    )

    assert result.ready is True
    assert result.dependency is not None
    assert result.dependency.fuel.name == "wood"
    assert [row.fuel.name for row in result.evaluations] == [
        "nuclear-fuel",
        "coal",
        "wood",
    ]


def test_world_supply_can_be_planned_without_being_silently_executed() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(),
        inventory={},
        fuel_sources_by_item={
            "coal": (
                FuelSource(
                    position=(25.5, 85.5),
                    available=10,
                    supplies_chain=False,
                ),
            ),
        },
        horizon_s=10,
    )

    assert result.ready is True
    assert result.dependency is not None
    assert result.dependency.fuel.name == "coal"
    assert result.dependency.carried_only is False
    assert result.dependency.supply_plan.fuel_planned == 1

    assert result.prepared is not None
    compiled = compile_structural_action(result.prepared)
    assert compiled.ready is False
    assert compiled.refusal is not None
    assert compiled.refusal.code == REFUSAL_WORLD_FUEL_DRAW_UNSUPPORTED


def test_no_available_compatible_fuel_is_named_refusal() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(),
        inventory={},
        horizon_s=10,
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_FUEL_SUPPLY_UNAVAILABLE
    assert all(not row.covered for row in result.evaluations)


def test_electric_machine_requires_a_different_dependency_adapter() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(electric=True),
        inventory={"coal": 20},
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code == REFUSAL_ENERGY_SOURCE_NOT_COMPOSED


def test_carried_fuel_v2_contract_compiles_to_processor_insert() -> None:
    result = complete_structural_dependencies(
        prepared(),
        catalog=catalog(),
        inventory={"coal": 20},
        horizon_s=10,
    )
    assert result.prepared is not None

    compiled = compile_structural_action(result.prepared)

    assert compiled.ready is True
    assert compiled.compiled is not None
    assert "Prototype.Coal" in compiled.compiled.code
    assert "cortex_processor=insert_item(" in compiled.compiled.code
    assert "quantity=1" in compiled.compiled.code
    assert "fuel_processor" in compiled.compiled.operation_names
