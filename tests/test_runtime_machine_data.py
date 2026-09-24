"""Machine and belt facts must arrive from the runtime, and a broken probe
must never read as a field the entity does not have.

Factorio 2.0.73 removed ``LuaEntityPrototype.crafting_speed``; reading it
raises ``LuaEntityPrototype doesn't contain key crafting_speed``. The probe
wrapped that read in ``pcall`` and stored the failure as ``nil``, so every
machine reported "no crafting speed" and nothing in the payload said the
read had failed. The supported accessor is ``get_crafting_speed()``.

The payload assertions below run against a stub shaped like the live
payload. The accessor assertions run against the Lua the observer really
sends, which is what decides whether the live game answers at all.
"""

from __future__ import annotations

import json

import pytest

from factorio_ai_lab.dashboard.state import FactorioObserver, json_finite
from factorio_ai_lab.planning.runtime_catalog import (
    BELT_SPEED_UNIT,
    PROBE_ABSENT,
    PROBE_FAILED,
    PROBE_MEASURED,
    PROBE_UNKNOWN,
    TICKS_PER_SECOND,
    RuntimeFactorioCatalog,
)

PAYLOAD = {
    "connected": True,
    "factorio_version": "2.0.73",
    "recipes": [],
    "technologies": [],
    "machines": [
        {
            "name": "assembling-machine-2",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
            "crafting_speed": 0.75,
            "crafting_speed_status": PROBE_MEASURED,
            "resource_categories": [],
            "mining_speed_status": PROBE_ABSENT,
            "energy_source_type": "electric",
            "energy_source_status": PROBE_MEASURED,
            "energy_usage_per_tick_j": 2500,
            "energy_usage_status": PROBE_MEASURED,
            "fuel_categories": [],
            "fuel_categories_status": PROBE_ABSENT,
        },
        {
            "name": "stone-furnace",
            "type": "furnace",
            "crafting_categories": ["smelting"],
            "crafting_speed": 1.0,
            "crafting_speed_status": PROBE_MEASURED,
            "resource_categories": [],
            "mining_speed_status": PROBE_ABSENT,
            "energy_source_type": "burner",
            "energy_source_status": PROBE_MEASURED,
            "energy_usage_per_tick_j": 1500,
            "energy_usage_status": PROBE_MEASURED,
            "fuel_categories": ["chemical"],
            "fuel_categories_status": PROBE_MEASURED,
        },
        {
            "name": "electric-mining-drill",
            "type": "mining-drill",
            "crafting_categories": [],
            "crafting_speed_status": PROBE_ABSENT,
            "resource_categories": ["basic-solid"],
            "mining_speed": 0.5,
            "mining_speed_status": PROBE_MEASURED,
        },
        {
            "name": "oil-refinery",
            "type": "assembling-machine",
            "crafting_categories": ["oil-processing"],
            "crafting_speed_status": PROBE_FAILED,
            "resource_categories": [],
            "mining_speed_status": PROBE_ABSENT,
        },
        {
            "name": "legacy-machine",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
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
        {
            "name": "wood",
            "fuel_value_j": 2_000_000,
            "fuel_value_status": PROBE_MEASURED,
            "fuel_categories": ["chemical"],
            "fuel_categories_status": PROBE_MEASURED,
        },
        {
            "name": "uranium-fuel-cell",
            "fuel_value_j": 8_000_000_000,
            "fuel_value_status": PROBE_MEASURED,
            "fuel_categories": ["nuclear"],
            "fuel_categories_status": PROBE_MEASURED,
        },
    ],
    "belts": [
        {
            "name": "transport-belt",
            "type": "transport-belt",
            "belt_speed": 0.03125,
            "belt_speed_status": PROBE_MEASURED,
            "belt_speed_unit": BELT_SPEED_UNIT,
            "max_underground_distance_status": PROBE_ABSENT,
        },
        {
            "name": "express-transport-belt",
            "type": "transport-belt",
            "belt_speed": 0.09375,
            "belt_speed_status": PROBE_MEASURED,
            "belt_speed_unit": BELT_SPEED_UNIT,
            "max_underground_distance_status": PROBE_ABSENT,
        },
        {
            "name": "underground-belt",
            "type": "underground-belt",
            "belt_speed": 0.03125,
            "belt_speed_status": PROBE_MEASURED,
            "belt_speed_unit": BELT_SPEED_UNIT,
            "max_underground_distance": 5,
            "max_underground_distance_status": PROBE_MEASURED,
        },
        {
            "name": "unreadable-belt",
            "type": "transport-belt",
            "belt_speed_status": PROBE_FAILED,
            "belt_speed_unit": BELT_SPEED_UNIT,
            "max_underground_distance_status": PROBE_FAILED,
        },
    ],
    "counts": {
        "recipes": 0,
        "technologies": 0,
        "machines": 5,
        "belts": 4,
        "fuels": 3,
    },
}


@pytest.fixture(name="catalog")
def catalog_fixture() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog(PAYLOAD)


def test_knowledge_command_uses_the_supported_crafting_speed_accessor():
    command = FactorioObserver._GAME_KNOWLEDGE_COMMAND
    assert "get_crafting_speed()" in command
    assert "entity.crafting_speed" not in command


def test_knowledge_command_probes_belt_speed_and_underground_reach():
    command = FactorioObserver._GAME_KNOWLEDGE_COMMAND
    assert "belt_speed" in command
    assert "max_underground_distance" in command




def test_knowledge_command_probes_machine_energy_and_fuels():
    command = FactorioObserver._GAME_KNOWLEDGE_COMMAND
    assert "get_max_energy_usage()" in command
    assert "burner_prototype" in command
    assert "electric_energy_source_prototype" in command
    assert "fuel_categories" in command
    assert "item.fuel_category" in command
    assert "fuel_value" in command


def test_machine_energy_distinguishes_burner_and_electric(catalog):
    furnace = catalog.machine_energy("stone-furnace")
    assembler = catalog.machine_energy("assembling-machine-2")

    assert furnace.source_type == "burner"
    assert furnace.source_measured is True
    assert furnace.energy_usage_per_tick_j == 1500
    assert furnace.energy_usage_measured is True
    assert furnace.fuel_categories == ("chemical",)

    assert assembler.source_type == "electric"
    assert assembler.source_measured is True
    assert assembler.energy_usage_per_tick_j == 2500
    assert assembler.fuel_categories == ()


def test_unknown_machine_energy_stays_unknown(catalog):
    energy = catalog.machine_energy("no-such-machine")
    assert energy.source_type is None
    assert energy.source_type_status == PROBE_UNKNOWN
    assert energy.energy_usage_per_tick_j is None
    assert energy.energy_usage_status == PROBE_UNKNOWN


def test_compatible_fuels_use_runtime_category_and_energy_density(catalog):
    fuels = catalog.compatible_fuels(("chemical",))

    assert [fuel.name for fuel in fuels] == ["coal", "wood"]
    assert fuels[0].fuel_value_j == 4_000_000
    assert catalog.compatible_fuels(("nuclear",))[0].name == "uranium-fuel-cell"

def test_crafting_speed_arrives_for_machines_that_have_one(catalog):
    assembler = catalog.machine_speed("assembling-machine-2")
    furnace = catalog.machine_speed("stone-furnace")
    assert assembler.crafting_speed == 0.75
    assert assembler.crafting_speed_status == PROBE_MEASURED
    assert assembler.crafting_speed_measured is True
    assert furnace.crafting_speed == 1.0
    assert furnace.crafting_speed_measured is True


def test_probe_failure_is_distinguishable_from_an_absent_field(catalog):
    failed = catalog.machine_speed("oil-refinery")
    absent = catalog.machine_speed("electric-mining-drill")
    assert failed.crafting_speed_status == PROBE_FAILED
    assert absent.crafting_speed_status == PROBE_ABSENT
    assert failed.crafting_speed_status != absent.crafting_speed_status
    assert failed.crafting_speed is None
    assert absent.crafting_speed is None
    assert failed.crafting_speed_measured is False
    assert absent.crafting_speed_measured is False


def test_absent_crafting_speed_is_not_zero(catalog):
    absent = catalog.machine_speed("electric-mining-drill")
    assert absent.crafting_speed is None
    assert absent.crafting_speed != 0
    assert absent.mining_speed == 0.5
    assert absent.mining_speed_measured is True


def test_machine_without_a_status_field_is_unknown_not_absent(catalog):
    legacy = catalog.machine_speed("legacy-machine")
    missing = catalog.machine_speed("no-such-machine")
    assert legacy.crafting_speed_status == PROBE_UNKNOWN
    assert legacy.crafting_speed is None
    assert missing.crafting_speed_status == PROBE_UNKNOWN
    assert missing.crafting_speed is None


def test_belt_speed_is_present_with_its_unit_declared(catalog):
    belt = catalog.belt_speed("transport-belt")
    assert belt is not None
    assert belt.tiles_per_tick == 0.03125
    assert belt.unit == BELT_SPEED_UNIT == "tiles_per_tick"
    assert belt.belt_speed_status == PROBE_MEASURED
    assert {row.name for row in catalog.belt_speeds()} == {
        "transport-belt",
        "express-transport-belt",
        "underground-belt",
        "unreadable-belt",
    }


def test_belt_speed_converts_to_tiles_per_second(catalog):
    belt = catalog.belt_speed("transport-belt")
    express = catalog.belt_speed("express-transport-belt")
    assert belt.tiles_per_second == pytest.approx(1.875)
    assert express.tiles_per_second == pytest.approx(5.625)
    assert belt.tiles_per_second == pytest.approx(
        belt.tiles_per_tick * TICKS_PER_SECOND
    )


def test_unreadable_belt_keeps_the_failure_visible(catalog):
    belt = catalog.belt_speed("unreadable-belt")
    assert belt.belt_speed_status == PROBE_FAILED
    assert belt.tiles_per_tick is None
    assert belt.tiles_per_second is None
    assert belt.max_underground_distance_status == PROBE_FAILED


def test_underground_reach_is_absent_on_surface_belts(catalog):
    surface = catalog.belt_speed("transport-belt")
    underground = catalog.belt_speed("underground-belt")
    assert surface.max_underground_distance is None
    assert surface.max_underground_distance_status == PROBE_ABSENT
    assert underground.max_underground_distance == 5
    assert underground.max_underground_distance_status == PROBE_MEASURED


def test_summary_reports_runtime_machine_and_belt_facts(catalog):
    summary = catalog.summary()
    assert summary["belt_count"] == 4
    assert summary["measured_crafting_speed_count"] == 2
    belts = {row["name"]: row for row in summary["belt_speeds"]}
    assert belts["transport-belt"]["tiles_per_tick"] == 0.03125
    assert belts["transport-belt"]["tiles_per_second"] == pytest.approx(1.875)
    assert belts["transport-belt"]["belt_speed_unit"] == BELT_SPEED_UNIT
    assert json.dumps(json_finite(summary), allow_nan=False)

def test_machine_names_by_type_uses_observed_runtime_type(catalog):
    assert catalog.machine_names_by_type("inserter") == (
        "burner-inserter",
        "inserter",
    )
    assert catalog.machine_names_by_type("furnace") == ("stone-furnace",)
    assert catalog.machine_names_by_type("not-a-type") == ()

def test_knowledge_command_keeps_energy_actuators_in_machine_catalog():
    command = FactorioObserver._GAME_KNOWLEDGE_COMMAND
    assert 'local energy_actor=energy_source_status~="absent"' in command
    assert "if crafting or mining or energy_actor then" in command
