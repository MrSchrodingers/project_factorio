"""Which side of an inserter is the pickup, measured against the engine.

``build_factory_graph`` derived the two sides as ``pickup = position -
direction * reach`` and ``drop = position + direction * reach``. The engine
says the opposite. Read over RCON on 2026-09-23 from the live world, through
``LuaEntity.pickup_position`` and ``LuaEntity.drop_position``, which are the
engine's own answer and not a derivation:

    burner-inserter @(25.5, 7.5) direction=0
        pickup_position  (25.5, 6.5)   -> wooden-chest
        drop_position    (25.5, 8.7)   -> burner-mining-drill

    burner-inserter @(0.5, 9.5)  direction=4
        pickup_position  (1.5, 9.5)    -> wooden-chest
        drop_position    (-0.7, 9.5)   -> boiler

All eighteen inserters standing in that world agree: direction 0 picks up at
*lower* y and drops at *higher* y, so the direction vector points from the
drop side towards the pickup side. The graph had the sign the other way, and
therefore read every inserter in the factory backwards.

What that cost, and why it is not merely cosmetic:

* ``ContainerRole.supplies_chain`` and ``fed_by_chain`` were swapped. The
  fuel reserve in ``repair_fuel_sources`` is applied to containers that
  ``supplies_chain``, so it protected the drills' *output* chests and left
  the *feed* chests -- the ones a machine is waiting on -- open to being
  drained. The reserve did the opposite of its purpose.
* ``chain_feeds`` found no feed chest at all in a world holding seven of
  them, so the coal distribution step had nothing to link.
* ``producers_reaching_processor``, ``isolated_producers`` and
  ``physical_processing_coverage`` are fitness inputs, and every chain that
  runs through an inserter was read in reverse to compute them.

The nodes below are the measured geometry above, in the shape
``_save_entity_state`` reports.
"""

from __future__ import annotations

from typing import Any

from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.planning.resupply import chain_feeds, container_roles

#: The coal cell's feed, exactly as measured: chest north, drill south.
FEED_CHEST: dict[str, Any] = {
    "name": "wooden-chest",
    "position": {"x": 25.5, "y": 6.5},
    "direction": 0,
}
FEED_ARM: dict[str, Any] = {
    "name": "burner-inserter",
    "position": {"x": 25.5, "y": 7.5},
    "direction": 0,
}
FED_DRILL: dict[str, Any] = {
    "name": "burner-mining-drill",
    "position": {"x": 25.5, "y": 8.5},
    "direction": 8,
}

#: The boiler's feed: chest east at x=1.5, boiler west at x=-0.5.
BOILER_CHEST: dict[str, Any] = {
    "name": "wooden-chest",
    "position": {"x": 1.5, "y": 9.5},
    "direction": 0,
}
BOILER_ARM: dict[str, Any] = {
    "name": "burner-inserter",
    "position": {"x": 0.5, "y": 9.5},
    "direction": 4,
}
BOILER: dict[str, Any] = {
    "name": "boiler",
    "position": {"x": -0.5, "y": 9.5},
    "direction": 4,
}


def _edges(graph: dict[str, Any], relation: str) -> set[tuple[str, str]]:
    names = {
        str(node["id"]): str(node["name"]) for node in graph["nodes"]
    }
    return {
        (names[edge["source"]], names[edge["target"]])
        for edge in graph["edges"]
        if edge["relation"] == relation
    }


def test_the_arm_picks_up_from_the_chest_the_engine_says_it_does() -> None:
    graph = build_factory_graph([FEED_CHEST, FEED_ARM, FED_DRILL])

    assert ("wooden-chest", "burner-inserter") in _edges(graph, "pickup")


def test_the_arm_drops_into_the_machine_the_engine_says_it_does() -> None:
    graph = build_factory_graph([FEED_CHEST, FEED_ARM, FED_DRILL])

    assert ("burner-inserter", "burner-mining-drill") in _edges(graph, "drop")


def test_the_same_holds_on_the_east_west_axis() -> None:
    graph = build_factory_graph([BOILER_CHEST, BOILER_ARM, BOILER])

    assert ("wooden-chest", "burner-inserter") in _edges(graph, "pickup")
    assert ("burner-inserter", "boiler") in _edges(graph, "drop")


def test_a_feed_chest_is_read_as_supplying_and_not_as_being_fed() -> None:
    # This is the reading ``repair_fuel_sources`` protects a container by.
    # Backwards, it reserved fuel in the chests nothing was waiting on and
    # offered up the chests machines were starving next to.
    graph = build_factory_graph([FEED_CHEST, FEED_ARM, FED_DRILL])
    roles = {role.name: role for role in container_roles(graph)}

    chest = roles["wooden-chest"]
    assert chest.supplies_chain is True
    assert chest.fed_by_chain is False


def test_the_feed_chest_is_found_with_the_machine_behind_it() -> None:
    graph = build_factory_graph([FEED_CHEST, FEED_ARM, FED_DRILL])

    feeds = chain_feeds(graph)

    assert len(feeds) == 1
    assert feeds[0].machine_name == "burner-mining-drill"
    assert feeds[0].container.position == (25.5, 6.5)


def test_an_output_chest_is_read_as_fed_and_not_as_supplying() -> None:
    # The other direction has to stay right too: a drill dropping into its
    # own output chest makes that chest the end of a chain, not the start.
    drill = {
        "name": "burner-mining-drill",
        "position": {"x": 27.0, "y": 9.0},
        "direction": 8,
    }
    chest = {
        "name": "wooden-chest",
        "position": {"x": 27.5, "y": 10.5},
        "direction": 0,
    }
    graph = build_factory_graph([drill, chest])
    roles = {role.name: role for role in container_roles(graph)}

    assert roles["wooden-chest"].fed_by_chain is True
    assert roles["wooden-chest"].supplies_chain is False
