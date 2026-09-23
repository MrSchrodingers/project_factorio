"""The map card has to show what sustains the status it prints.

Three readings the game already answers were dropped on the floor by the
scene payload, and each absence cost an inspection on the live box:

* a wooden chest reported position and status and nothing about what it
  held, so "is the ore arriving?" needed a separate RCON call;
* a boiler reported working with no fuel, no burning item and no steam
  behind it, so the claim could not be checked from the panel;
* an assembling machine reported no_ingredients without naming the
  ingredient, so the missing item was guessed.

The fourth reading is the electric network. A machine answering
network_id -1 next to a pole on live network 7743 stalled a whole stage,
and the cause only surfaced because someone measured it by hand.

Every assertion below is about keeping an absence an absence. A chest whose
inventory was never read must never render as an empty chest, and a machine
whose input was not read must not claim that nothing is missing.
"""

from __future__ import annotations

import json
from typing import Any

from factorio_ai_lab.dashboard.state import (
    ENTITY_READING_GROUPS,
    DashboardState,
    FactorioObserver,
    json_finite,
)

PROBED = list(ENTITY_READING_GROUPS)


class StubObserver:
    """A world observer with the entity rows the Lua sweep would return."""

    def __init__(
        self,
        entities: list[dict[str, Any]],
        *,
        entity_readings: list[str] | None = None,
    ) -> None:
        self.entities = entities
        self.entity_readings = (
            PROBED if entity_readings is None else entity_readings
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": True,
            "tick": 4711,
            "entities": self.entities,
            "entity_count": len(self.entities),
            "entity_readings": self.entity_readings,
            "production": {},
            "latency_ms": 8.0,
            "error": None,
        }

    def map_snapshot(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "connected": True,
            "center": {"x": 0.0, "y": 0.0},
            "bounds": {
                "left_top": {"x": -32.0, "y": -32.0},
                "right_bottom": {"x": 32.0, "y": 32.0},
            },
            "resources": [],
            "natural": [],
            "terrain_runs": [],
            "water_tile_count": 0,
        }

    def entity_prototypes(self) -> dict[str, Any]:
        return {"by_name": {}, "count": 0}

    def close(self) -> None:
        return None


def _scene(
    entities: list[dict[str, Any]],
    *,
    entity_readings: list[str] | None = None,
) -> dict[str, Any]:
    state = DashboardState()
    state.factorio = StubObserver(entities, entity_readings=entity_readings)
    return state.world_scene()


def _by_name(scene: dict[str, Any], name: str) -> dict[str, Any]:
    for entity in scene["entities"]:
        if entity["name"] == name:
            return entity
    raise AssertionError(f"{name} missing from the scene")


def _chest(**overrides: Any) -> dict[str, Any]:
    row = {
        "name": "wooden-chest",
        "type": "container",
        "position": {"x": -57.5, "y": 80.5},
        "direction": 0,
        "status": "normal",
        "contents": [{"name": "iron-plate", "count": 13}],
    }
    row.update(overrides)
    return row


def _boiler(**overrides: Any) -> dict[str, Any]:
    row = {
        "name": "boiler",
        "type": "boiler",
        "position": {"x": -40.5, "y": 70.5},
        "direction": 2,
        "status": "working",
        "fuel": [{"name": "coal", "count": 4}],
        "burning": "coal",
        "fuel_remaining": 2705000.0,
        "fluids": [
            {"index": 1, "name": "water", "amount": 200.0, "temperature": 15.0},
            {"index": 2, "name": "steam", "amount": 60.0, "temperature": 165.0},
        ],
    }
    row.update(overrides)
    return row


def _assembler(**overrides: Any) -> dict[str, Any]:
    row = {
        "name": "assembling-machine-1",
        "type": "assembling-machine",
        "position": {"x": -50.5, "y": 75.5},
        "direction": 0,
        "status": "no_ingredients",
        "recipe": "iron-gear-wheel",
        "energy": 0.0,
        "ingredients": [
            {"name": "iron-plate", "amount": 2.0, "type": "item"}
        ],
        "craft_input": [],
        "craft_output": [],
        "network_id": -1,
    }
    row.update(overrides)
    return row


def test_container_reports_the_items_it_holds() -> None:
    entity = _by_name(_scene([_chest()]), "wooden-chest")
    assert entity["contents"]["status"] == "measured"
    assert entity["contents"]["items"] == [{"name": "iron-plate", "count": 13.0}]
    assert entity["contents"]["total"] == 13.0


def test_unread_container_is_not_an_empty_container() -> None:
    """The marker is what separates "nothing there" from "never asked"."""
    entity = _by_name(_scene([_chest(contents=None)], entity_readings=[]), "wooden-chest")
    assert entity["contents"]["status"] == "unprobed"
    assert entity["contents"]["items"] is None
    assert entity["contents"]["total"] is None


def test_failed_container_probe_is_not_an_empty_container() -> None:
    row = _chest()
    del row["contents"]
    row["contents_status"] = "probe_failed"
    entity = _by_name(_scene([row]), "wooden-chest")
    assert entity["contents"]["status"] == "probe_failed"
    assert entity["contents"]["items"] is None


def test_measured_empty_container_stays_empty() -> None:
    entity = _by_name(_scene([_chest(contents=[])]), "wooden-chest")
    assert entity["contents"]["status"] == "measured"
    assert entity["contents"]["items"] == []
    assert entity["contents"]["total"] == 0.0


def test_entity_without_inventory_omits_the_group() -> None:
    row = {
        "name": "transport-belt",
        "type": "transport-belt",
        "position": {"x": 1.5, "y": 2.5},
        "status": "working",
    }
    entity = _by_name(_scene([row]), "transport-belt")
    assert "contents" not in entity
    assert "fuel" not in entity


def test_burner_reports_fuel_and_what_it_burns() -> None:
    entity = _by_name(_scene([_boiler()]), "boiler")
    fuel = entity["fuel"]
    assert fuel["status"] == "measured"
    assert fuel["items"] == [{"name": "coal", "count": 4.0}]
    assert fuel["burning"] == "coal"
    assert fuel["remaining_joules"] == 2705000.0


def test_burner_out_of_fuel_reports_empty_and_nothing_burning() -> None:
    row = _boiler(status="no_fuel", fuel=[], fuel_remaining=0.0)
    del row["burning"]
    entity = _by_name(_scene([row]), "boiler")
    assert entity["fuel"]["items"] == []
    assert entity["fuel"]["burning"] is None
    assert entity["fuel"]["burning_status"] == "absent"
    assert entity["fuel"]["remaining_joules"] == 0.0


def test_unread_fuel_is_not_zero_fuel() -> None:
    row = _boiler()
    del row["fuel"]
    del row["fuel_remaining"]
    entity = _by_name(_scene([row], entity_readings=[]), "boiler")
    assert entity["fuel"]["status"] == "unprobed"
    assert entity["fuel"]["items"] is None
    assert entity["fuel"]["remaining_joules"] is None


def test_boiler_reports_the_steam_it_makes() -> None:
    entity = _by_name(_scene([_boiler()]), "boiler")
    fluids = entity["fluids"]
    assert fluids["status"] == "measured"
    assert fluids["boxes"] == [
        {"index": 1, "name": "water", "amount": 200.0, "temperature": 15.0},
        {"index": 2, "name": "steam", "amount": 60.0, "temperature": 165.0},
    ]


def test_assembler_names_the_missing_ingredient() -> None:
    entity = _by_name(_scene([_assembler()]), "assembling-machine-1")
    crafting = entity["crafting"]
    assert crafting["status"] == "measured"
    assert crafting["ingredients"] == [
        {
            "name": "iron-plate",
            "required": 2.0,
            "available": 0.0,
            "satisfied": False,
        }
    ]
    assert crafting["missing"] == [
        {"name": "iron-plate", "required": 2.0, "available": 0.0, "shortfall": 2.0}
    ]


def test_partial_input_shortens_the_shortfall() -> None:
    row = _assembler(craft_input=[{"name": "iron-plate", "count": 1}])
    crafting = _by_name(_scene([row]), "assembling-machine-1")["crafting"]
    assert crafting["missing"] == [
        {"name": "iron-plate", "required": 2.0, "available": 1.0, "shortfall": 1.0}
    ]


def test_stocked_machine_reports_nothing_missing() -> None:
    row = _assembler(craft_input=[{"name": "iron-plate", "count": 5}])
    crafting = _by_name(_scene([row]), "assembling-machine-1")["crafting"]
    assert crafting["missing"] == []
    assert crafting["ingredients"][0]["satisfied"] is True


def test_unread_input_never_claims_nothing_is_missing() -> None:
    row = _assembler()
    del row["craft_input"]
    row["craft_input_status"] = "probe_failed"
    crafting = _by_name(_scene([row]), "assembling-machine-1")["crafting"]
    assert crafting["input"] is None
    assert crafting["missing"] is None
    assert crafting["ingredients"][0]["available"] is None
    assert crafting["ingredients"][0]["satisfied"] is None


def test_fluid_ingredient_is_counted_from_the_fluid_boxes() -> None:
    row = _assembler(
        recipe="sulfuric-acid",
        ingredients=[{"name": "water", "amount": 100.0, "type": "fluid"}],
        fluids=[
            {"index": 1, "name": "water", "amount": 40.0, "temperature": 15.0}
        ],
    )
    crafting = _by_name(_scene([row]), "assembling-machine-1")["crafting"]
    assert crafting["ingredients"][0]["available"] == 40.0
    assert crafting["missing"] == [
        {"name": "water", "required": 100.0, "available": 40.0, "shortfall": 60.0}
    ]


def test_machine_outside_every_network_reports_the_reading() -> None:
    """-1 is an answer: the machine says it belongs to no network."""
    entity = _by_name(_scene([_assembler()]), "assembling-machine-1")
    assert entity["power"] == {"status": "measured", "network_id": -1}


def test_pole_reports_the_live_network() -> None:
    row = {
        "name": "small-electric-pole",
        "type": "electric-pole",
        "position": {"x": 3.5, "y": 4.5},
        "status": "working",
        "network_id": 7743,
    }
    entity = _by_name(_scene([row]), "small-electric-pole")
    assert entity["power"] == {"status": "measured", "network_id": 7743}


def test_unread_network_is_not_network_zero() -> None:
    row = _assembler()
    del row["network_id"]
    entity = _by_name(_scene([row], entity_readings=[]), "assembling-machine-1")
    assert entity["power"]["status"] == "unprobed"
    assert entity["power"]["network_id"] is None


def test_existing_scene_contract_is_preserved() -> None:
    scene = _scene([_chest(), _boiler(), _assembler()])
    assert set(scene) >= {
        "connected",
        "tick",
        "bounds",
        "center",
        "entities",
        "entity_count",
        "resources",
        "terrain_runs",
        "prototypes",
    }
    chest = _by_name(scene, "wooden-chest")
    assert set(chest) >= {"name", "type", "x", "y", "direction", "status"}
    assembler = _by_name(scene, "assembling-machine-1")
    assert assembler["recipe"] == "iron-gear-wheel"
    assert assembler["energy"] == 0.0


def test_scene_stays_strict_json() -> None:
    scene = _scene([_chest(), _boiler(), _assembler()])
    json.dumps(json_finite(scene), allow_nan=False)


def test_sweep_declares_every_group_it_probes() -> None:
    """The marker is read on the Python side; drift would silently lie."""
    command = FactorioObserver._SNAPSHOT_COMMAND
    assert "entity_readings" in command
    for group in ENTITY_READING_GROUPS:
        assert f'"{group}"' in command
