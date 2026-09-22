"""Obstacle map for the belt planner: exact tiles, not a guessed radius.

The prototype sizes asserted here were read from the live Factorio runtime
through the dashboard endpoint ``GET /api/world/scene`` (109 placeable
prototypes), not from memory.
"""

import json

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.experiments.curriculum_runner import _runtime_entity_footprints
from factorio_ai_lab.planning.astar import rectangular_bounds, weighted_astar
from factorio_ai_lab.planning.footprints import (
    blocked_tiles,
    entity_footprint,
    entity_tiles,
    prototype_footprints,
)

RUNTIME_PAYLOAD = {
    "connected": True,
    "count": 5,
    "prototypes": [
        {"name": "assembling-machine-1", "tile_width": 3, "tile_height": 3},
        {"name": "boiler", "tile_width": 3, "tile_height": 2},
        {"name": "burner-mining-drill", "tile_width": 2, "tile_height": 2},
        {"name": "steam-engine", "tile_width": 3, "tile_height": 5},
        {"name": "transport-belt", "tile_width": 1, "tile_height": 1},
    ],
}
RUNTIME_FOOTPRINTS = prototype_footprints(RUNTIME_PAYLOAD)


def entity(name, x, y, *, direction=0, **extra):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        **extra,
    }


def square(left, top, width, height):
    return {
        GridPoint(left + dx, top + dy)
        for dx in range(width)
        for dy in range(height)
    }


def test_three_by_three_machine_blocks_nine_tiles():
    tiles = entity_tiles(
        entity("assembling-machine-1", 5.5, 5.5),
        RUNTIME_FOOTPRINTS,
    )
    assert tiles == square(4, 4, 3, 3)
    assert len(tiles) == 9


def test_two_by_two_drill_blocks_four_tiles_not_nine():
    tiles = entity_tiles(
        entity("burner-mining-drill", 5.0, 5.0),
        RUNTIME_FOOTPRINTS,
    )
    assert tiles == square(4, 4, 2, 2)
    assert len(tiles) == 4
    # The replaced radius heuristic marked a 3x3 block around the centre.
    assert GridPoint(3, 3) not in tiles
    assert GridPoint(6, 6) not in tiles


def test_east_and_west_swap_width_and_height():
    north = entity("boiler", 5.5, 5.0)
    east = entity("boiler", 5.0, 5.5, direction=4)
    west = entity("boiler", 5.0, 5.5, direction=12)
    south = entity("boiler", 5.5, 5.0, direction=8)

    assert entity_footprint(north, RUNTIME_FOOTPRINTS) == (3, 2)
    assert entity_footprint(south, RUNTIME_FOOTPRINTS) == (3, 2)
    assert entity_footprint(east, RUNTIME_FOOTPRINTS) == (2, 3)
    assert entity_footprint(west, RUNTIME_FOOTPRINTS) == (2, 3)

    assert entity_tiles(north, RUNTIME_FOOTPRINTS) == square(4, 4, 3, 2)
    assert entity_tiles(east, RUNTIME_FOOTPRINTS) == square(4, 4, 2, 3)


def test_square_entities_ignore_rotation():
    for direction in (0, 4, 8, 12):
        rotated = entity("assembling-machine-1", 5.5, 5.5, direction=direction)
        assert entity_footprint(rotated, RUNTIME_FOOTPRINTS) == (3, 3)


def test_off_cardinal_direction_snaps_to_nearest_cardinal():
    # Factorio 2.0 uses 16 directions; 5 is east-ish, 1 and 15 are north-ish.
    east_ish = entity("boiler", 5.0, 5.5, direction=5)
    north_ish = entity("boiler", 5.5, 5.0, direction=1)
    wrapped = entity("boiler", 5.5, 5.0, direction=15)
    assert entity_footprint(east_ish, RUNTIME_FOOTPRINTS) == (2, 3)
    assert entity_footprint(north_ish, RUNTIME_FOOTPRINTS) == (3, 2)
    assert entity_footprint(wrapped, RUNTIME_FOOTPRINTS) == (3, 2)


def test_tall_entity_keeps_orientation():
    tiles = entity_tiles(entity("steam-engine", 5.5, 5.5), RUNTIME_FOOTPRINTS)
    assert tiles == square(4, 3, 3, 5)


def test_fallback_uses_entity_tile_dimensions_when_runtime_is_absent():
    # _save_entity_state serializes numbers as strings.
    live = entity(
        "assembling-machine-1",
        "5.5",
        "5.5",
        tile_dimensions={"tile_width": "3", "tile_height": "3"},
    )
    assert entity_tiles(live, {}) == square(4, 4, 3, 3)


def test_fallback_uses_static_table_when_runtime_and_snapshot_are_absent():
    assert entity_tiles(entity("lab", 5.5, 5.5), None) == square(4, 4, 3, 3)
    assert entity_tiles(entity("stone-furnace", 5.0, 5.0), None) == square(4, 4, 2, 2)


def test_unknown_prototype_falls_back_to_a_single_tile():
    unknown = entity("modded-mystery-machine", 5.5, 5.5)
    assert entity_footprint(unknown, RUNTIME_FOOTPRINTS) == (1, 1)
    assert entity_tiles(unknown, RUNTIME_FOOTPRINTS) == {GridPoint(5, 5)}


def test_malformed_entities_are_skipped_instead_of_raising():
    assert entity_tiles({"name": "lab"}, RUNTIME_FOOTPRINTS) == set()
    assert entity_tiles({"name": "lab", "position": {"x": "n/a", "y": 1}}, None) == set()
    assert blocked_tiles([None, "lab", 7], RUNTIME_FOOTPRINTS) == set()


def test_prototype_footprints_accepts_every_payload_shape():
    row = {"name": "lab", "tile_width": 3, "tile_height": 3}
    assert prototype_footprints({"by_name": {"lab": row}}) == {"lab": (3, 3)}
    assert prototype_footprints({"lab": row}) == {"lab": (3, 3)}
    assert prototype_footprints({"prototypes": [{"name": "lab"}]}) == {}
    assert prototype_footprints(None) == {}


def test_character_never_blocks_the_route():
    tiles = blocked_tiles(
        [
            entity("character", 5.5, 5.5),
            entity("transport-belt", 7.5, 5.5),
        ],
        RUNTIME_FOOTPRINTS,
    )
    assert tiles == {GridPoint(7, 5)}


def test_route_never_crosses_a_large_machine():
    entities = [
        entity("assembling-machine-1", 5.5, 2.5),
        entity("burner-mining-drill", 5.0, 6.0),
    ]
    blocked = blocked_tiles(entities, RUNTIME_FOOTPRINTS)
    route = weighted_astar(
        GridPoint(0, 2),
        GridPoint(11, 2),
        is_blocked=blocked.__contains__,
        in_bounds=rectangular_bounds(12, 12),
    )
    assert route is not None
    assert blocked.isdisjoint(route.path)
    # Tiles a radius-0 obstacle map left open right through the machine.
    assert GridPoint(4, 2) in blocked
    assert GridPoint(6, 2) in blocked


class _FakeRconClient:
    def __init__(self, response):
        self.response = response
        self.commands = []

    def send_command(self, command):
        self.commands.append(command)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeInstance:
    def __init__(self, response):
        self.rcon_client = _FakeRconClient(response)


def test_runtime_footprints_reuse_the_dashboard_prototype_command():
    from factorio_ai_lab.dashboard.state import FactorioObserver

    instance = _FakeInstance(json.dumps(RUNTIME_PAYLOAD))
    footprints = _runtime_entity_footprints(instance)

    assert footprints["assembling-machine-1"] == (3, 3)
    assert footprints["burner-mining-drill"] == (2, 2)
    assert instance.rcon_client.commands == [FactorioObserver._ENTITY_PROTOTYPE_COMMAND]


def test_runtime_footprints_degrade_to_the_static_table_when_rcon_fails():
    assert _runtime_entity_footprints(_FakeInstance(OSError("rcon down"))) == {}
    assert _runtime_entity_footprints(_FakeInstance("")) == {}
    assert _runtime_entity_footprints(_FakeInstance("not json")) == {}
    assert _runtime_entity_footprints(object()) == {}

    # With no runtime answer the static table still blocks the real footprint.
    assert entity_tiles(entity("assembling-machine-1", 5.5, 5.5), {}) == square(4, 4, 3, 3)
