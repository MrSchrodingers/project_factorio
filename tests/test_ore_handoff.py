"""The ore a drill mined has to reach the furnace that smelts it.

The world read of 2026-09-23 counted seven stone furnaces at
``no_ingredients`` and, four tiles away, wooden chests holding 478, 455 and
270 iron ore. The drill fills its output chest and the chain ends there: the
same defect the coal trunk answers, one material over. The planner is the
same one, and what this module pins is the choice of source and target, the
filter on what is carried, and the primer without which the arms are
ornaments.

``a source is a container a chain fills, holding ore, measured``
    a chest nothing fills is not a production output, and a chest whose
    contents nobody read is not evidence of ore. Absence is not zero here
    either: an unread world plans nothing.

``a target is a smelter the engine reports out of ingredients``
    read from the same survey the placement is planned against. A furnace
    that is smelting is not short of ore, and a second arm into it is a
    duplicate.

``an arm that cannot be primed carries nothing``
    a burner inserter moving ore does not refuel itself from what it
    carries -- that is only true of the coal trunk -- so the primer is coal
    drawn from a container the planner measured. With no coal within reach
    the step refuses by name and builds nothing: belts and arms that cannot
    move are material spent on a line that will not carry.
"""

from __future__ import annotations

import ast
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.delivery import MODE_REFUSED
from factorio_ai_lab.planning.placement import WorldSurvey

#: A 2x2 drill, the chest it mines into, and a furnace standing clear of
#: both with nothing carrying ore across.
ORE_DRILL = {
    "name": "burner-mining-drill",
    "position": {"x": 27.0, "y": 83.0},
    "direction": 8,
    "status": '"working"',
}
ORE_CHEST = {
    "name": "wooden-chest",
    "position": {"x": 27.5, "y": 84.5},
    "direction": 0,
    "status": '"normal"',
}
FURNACE = {
    "name": "stone-furnace",
    "position": {"x": 27.0, "y": 88.0},
    "direction": 0,
    "status": '"no_ingredients"',
}
BUSY_FURNACE = {
    "name": "stone-furnace",
    "position": {"x": 31.0, "y": 88.0},
    "direction": 0,
    "status": '"working"',
}
COAL_CHEST = {
    "name": "wooden-chest",
    "position": {"x": 24.5, "y": 84.5},
    "direction": 0,
    "status": '"normal"',
}


def _survey(*entities: Any) -> WorldSurvey:
    return WorldSurvey.from_entities(
        entities or (ORE_DRILL, ORE_CHEST, FURNACE, COAL_CHEST),
        footprints={},
    )


def _graph(*, fed_by_chain: bool = True) -> dict[str, Any]:
    """Drill -> arm -> chest, in the shape ``build_factory_graph`` emits."""
    nodes = [
        {
            "id": "drill:1",
            "name": "burner-mining-drill",
            "category": "extraction",
            "x": 27.0,
            "y": 83.0,
        },
        {
            "id": "chest:1",
            "name": "wooden-chest",
            "category": "buffer",
            "x": 27.5,
            "y": 84.5,
        },
    ]
    edges = []
    if fed_by_chain:
        edges.append({"source": "drill:1", "target": "chest:1", "relation": "drop"})
    return {"nodes": nodes, "edges": edges}


def _contents(**held: Any) -> list[dict[str, Any]]:
    """Container contents as one RCON read reports them."""
    rows = [
        {
            "name": "wooden-chest",
            "position": {"x": 27.5, "y": 84.5},
            "contents": {"iron-ore": 478},
        },
        {
            "name": "wooden-chest",
            "position": {"x": 24.5, "y": 84.5},
            "contents": {"coal": 33},
        },
    ]
    for row in rows:
        key = (row["position"]["x"], row["position"]["y"])
        if key in held:
            row["contents"] = held[key]
    return rows


def test_a_chest_a_chain_fills_with_ore_is_a_source() -> None:
    sources = curriculum_runner.ore_sources(_graph(), _contents())

    assert len(sources) == 1
    assert sources[0].position == (27.5, 84.5)
    assert sources[0].item == "iron-ore"
    assert sources[0].amount == 478


def test_a_chest_nothing_fills_is_not_a_source() -> None:
    # A chest an arm draws from is the input of something. Carrying its
    # contents off starves whatever that is.
    assert curriculum_runner.ore_sources(_graph(fed_by_chain=False), _contents()) == ()


def test_a_chest_holding_too_little_ore_is_not_a_source() -> None:
    thin = [
        dict(row, contents={"iron-ore": 3})
        if row["position"]["x"] == 27.5
        else row
        for row in _contents()
    ]

    assert curriculum_runner.ore_sources(_graph(), thin) == ()


def test_an_unread_world_answers_no_sources() -> None:
    # None is not an empty world: a sweep that did not happen must not read
    # as a world with no ore in it.
    assert curriculum_runner.ore_sources(_graph(), None) == ()


def test_a_furnace_out_of_ingredients_is_a_target() -> None:
    targets = curriculum_runner.ore_delivery_targets(_survey())

    assert len(targets) == 1
    assert targets[0].position == (27.0, 88.0)
    assert targets[0].name == "stone-furnace"


def test_a_furnace_that_is_smelting_is_not_a_target() -> None:
    targets = curriculum_runner.ore_delivery_targets(
        _survey(ORE_DRILL, ORE_CHEST, BUSY_FURNACE, COAL_CHEST)
    )

    assert targets == ()


def test_the_ore_reaches_the_furnace_the_engine_reported_empty() -> None:
    links = curriculum_runner.plan_ore_distribution(
        _survey(),
        sources=curriculum_runner.ore_sources(_graph(), _contents()),
        targets=curriculum_runner.ore_delivery_targets(_survey()),
        primer=curriculum_runner.ore_primer_source(_contents(), _survey()),
    )

    assert len(links) == 1
    built = links[0]
    assert built.link.builds
    assert built.machine == "stone-furnace"
    assert built.source == (27.5, 84.5)
    # The arm at the far end drops into the furnace itself.
    arm = built.link.drop or built.link.lift
    assert arm is not None
    assert arm.drops_at in {
        GridPoint(26, 87),
        GridPoint(27, 87),
        GridPoint(26, 88),
        GridPoint(27, 88),
    }
    # Nothing is laid on the chest it draws from or on the furnace.
    assert GridPoint(27, 84) not in built.link.path
    assert GridPoint(27, 88) not in built.link.path


def test_a_target_served_by_one_source_is_not_served_again_by_the_next() -> None:
    second_chest = {
        "name": "wooden-chest",
        "position": {"x": 30.5, "y": 84.5},
        "direction": 0,
        "status": '"normal"',
    }
    graph = _graph()
    graph["nodes"].append(
        {
            "id": "chest:2",
            "name": "wooden-chest",
            "category": "buffer",
            "x": 30.5,
            "y": 84.5,
        }
    )
    graph["edges"].append(
        {"source": "drill:1", "target": "chest:2", "relation": "drop"}
    )
    contents = [
        *_contents(),
        {
            "name": "wooden-chest",
            "position": {"x": 30.5, "y": 84.5},
            "contents": {"iron-ore": 133},
        },
    ]
    survey = _survey(ORE_DRILL, ORE_CHEST, second_chest, FURNACE, COAL_CHEST)

    links = curriculum_runner.plan_ore_distribution(
        survey,
        sources=curriculum_runner.ore_sources(graph, contents),
        targets=curriculum_runner.ore_delivery_targets(survey),
        primer=curriculum_runner.ore_primer_source(contents, survey),
    )

    built = [item for item in links if item.link.builds]
    assert len(built) == 1
    assert built[0].source == (27.5, 84.5)


def test_without_coal_to_prime_them_no_arm_is_built() -> None:
    # A burner inserter carrying ore does not refuel itself out of what it
    # carries. Built without a primer it stands still, and the belt behind
    # it is material spent on a line that will not carry.
    survey = _survey(ORE_DRILL, ORE_CHEST, FURNACE)
    links = curriculum_runner.plan_ore_distribution(
        survey,
        sources=curriculum_runner.ore_sources(_graph(), _contents()),
        targets=curriculum_runner.ore_delivery_targets(survey),
        primer=None,
    )

    assert links
    assert all(item.link.mode == MODE_REFUSED for item in links)
    assert links[0].link.reason == curriculum_runner.ORE_LINK_NO_PRIMER


def test_the_primer_is_drawn_from_the_container_the_planner_measured() -> None:
    links = curriculum_runner.plan_ore_distribution(
        _survey(),
        sources=curriculum_runner.ore_sources(_graph(), _contents()),
        targets=curriculum_runner.ore_delivery_targets(_survey()),
        primer=curriculum_runner.ore_primer_source(_contents(), _survey()),
    )
    script = curriculum_runner.ore_distribution_script(
        [item for item in links if item.link.builds],
        primer=curriculum_runner.ore_primer_source(_contents(), _survey()),
    )

    ast.parse(script)
    assert "get_entity(Prototype.WoodenChest,Position(x=24.5,y=24.5))" not in script
    assert "Position(x=24.5,y=84.5)" in script
    assert "extract_item(Prototype.Coal" in script
    assert "insert_item(Prototype.Coal" in script


def test_the_ore_script_is_python_the_engine_can_run() -> None:
    links = curriculum_runner.plan_ore_distribution(
        _survey(),
        sources=curriculum_runner.ore_sources(_graph(), _contents()),
        targets=curriculum_runner.ore_delivery_targets(_survey()),
        primer=curriculum_runner.ore_primer_source(_contents(), _survey()),
    )
    script = curriculum_runner.ore_distribution_script(
        [item for item in links if item.link.builds],
        primer=curriculum_runner.ore_primer_source(_contents(), _survey()),
    )

    tree = ast.parse(script)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    placements = [index for index, name in enumerate(calls) if name == "place_entity"]
    assert placements
    for index in placements:
        assert "move_to" in calls[:index]
    # fle/env/gym_env/environment.py:451 fails a step on the printed text.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "error" not in node.value.lower()
            assert "exception: " not in node.value.lower()


def test_without_a_survey_nothing_is_planned() -> None:
    assert (
        curriculum_runner.plan_ore_distribution(None, sources=(), targets=()) == ()
    )


def test_the_ore_trunk_is_priced_against_the_ore_the_survey_read() -> None:
    # The lane is planned across a patch, and the patch is what the drills
    # are there to mine. One row further down costs two belts and two turns;
    # crossing costs four tiles of ore no drill will ever reach.
    west = {
        "name": "stone-furnace",
        "position": {"x": 21.0, "y": 84.0},
        "direction": 0,
        "status": '"no_ingredients"',
    }
    patch = {GridPoint(x, 84) for x in range(23, 27)}
    ore_rows = [
        {
            "name": "iron-ore",
            "type": "resource",
            "position": {"x": tile.x + 0.5, "y": tile.y + 0.5},
        }
        for tile in sorted(patch, key=lambda tile: tile.x)
    ]
    survey = WorldSurvey.from_entities(
        (ORE_DRILL, ORE_CHEST, west, COAL_CHEST, *ore_rows),
        footprints={},
        surveyed=(0.0, 0.0, 120.0, 120.0),
    )
    contents = _contents()
    links = curriculum_runner.plan_ore_distribution(
        survey,
        sources=curriculum_runner.ore_sources(_graph(), contents),
        targets=curriculum_runner.ore_delivery_targets(survey),
        primer=curriculum_runner.ore_primer_source(contents, survey),
        belt_budget=40,
    )

    assert links[0].link.builds
    laid = {tile for item in links for tile in item.link.path}
    assert laid
    assert not laid & patch


def test_each_source_starts_a_line_of_its_own() -> None:
    # Two chests, two furnaces, two trunks. A line that refuses has to skip
    # its own branches and nobody else's: the second source is not laid onto
    # the first one's belt and has no reason to wait for it.
    far_chest = {
        "name": "wooden-chest",
        "position": {"x": 60.5, "y": 84.5},
        "direction": 0,
        "status": '"normal"',
    }
    far_furnace = {
        "name": "stone-furnace",
        "position": {"x": 60.0, "y": 88.0},
        "direction": 0,
        "status": '"no_ingredients"',
    }
    graph = _graph()
    graph["nodes"].append(
        {
            "id": "chest:far",
            "name": "wooden-chest",
            "category": "buffer",
            "x": 60.5,
            "y": 84.5,
        }
    )
    graph["edges"].append(
        {"source": "drill:1", "target": "chest:far", "relation": "drop"}
    )
    contents = [
        *_contents(),
        {
            "name": "wooden-chest",
            "position": {"x": 60.5, "y": 84.5},
            "contents": {"iron-ore": 300},
        },
    ]
    survey = _survey(ORE_DRILL, ORE_CHEST, FURNACE, COAL_CHEST, far_chest, far_furnace)

    links = curriculum_runner.plan_ore_distribution(
        survey,
        sources=curriculum_runner.ore_sources(graph, contents),
        targets=curriculum_runner.ore_delivery_targets(survey),
        primer=curriculum_runner.ore_primer_source(contents, survey),
    )
    built = [item for item in links if item.link.builds]
    script = curriculum_runner.ore_distribution_script(
        built, primer=curriculum_runner.ore_primer_source(contents, survey)
    )

    ast.parse(script)
    assert len({item.source for item in built}) == 2
    # One reset per line, so the second one is built whatever the first did.
    assert script.count("ore_trunk_ok=True") == 3


def _fuel_graph() -> dict[str, Any]:
    """Two coal chests: one a drill draws from, one nothing draws from."""
    graph = _graph()
    graph["nodes"].extend(
        [
            {
                "id": "chest:feed",
                "name": "wooden-chest",
                "category": "buffer",
                "x": 24.5,
                "y": 84.5,
            },
            {
                "id": "arm:feed",
                "name": "burner-inserter",
                "category": "transfer",
                "x": 24.5,
                "y": 83.5,
            },
            {
                "id": "chest:spare",
                "name": "wooden-chest",
                "category": "buffer",
                "x": 40.5,
                "y": 84.5,
            },
        ]
    )
    graph["edges"].extend(
        [
            {"source": "chest:feed", "target": "arm:feed", "relation": "pickup"},
            {"source": "arm:feed", "target": "drill:1", "relation": "drop"},
        ]
    )
    return graph


def test_the_primer_is_not_drawn_from_a_chest_that_feeds_a_machine() -> None:
    # The chest at (24.5, 84.5) is what keeps a drill running. Taking its
    # coal for inserter primers stops the drill this trunk exists to empty.
    contents = [
        *_contents(),
        {
            "name": "wooden-chest",
            "position": {"x": 40.5, "y": 84.5},
            "contents": {"coal": 20},
        },
    ]
    primer = curriculum_runner.ore_primer_source(
        contents,
        _survey(),
        near=(27.5, 84.5),
        graph=_fuel_graph(),
    )

    assert primer is not None
    assert primer.position == (40.5, 84.5)


def test_a_feeding_chest_keeps_a_reserve_when_it_is_the_only_coal() -> None:
    # Nothing else holds coal, so the feed chest is the only primer there
    # is. What leaves it is the surplus over the reserve, never the charge
    # the machine behind it is running on.
    primer = curriculum_runner.ore_primer_source(
        _contents(),
        _survey(),
        near=(27.5, 84.5),
        graph=_fuel_graph(),
    )

    assert primer is not None
    assert primer.position == (24.5, 84.5)
    assert primer.amount == 33 - curriculum_runner.ORE_PRIMER_RESERVE


def test_a_chest_holding_only_the_reserve_is_no_primer_at_all() -> None:
    thin = [
        dict(row, contents={"coal": curriculum_runner.ORE_PRIMER_RESERVE})
        if row["position"]["x"] == 24.5
        else row
        for row in _contents()
    ]
    primer = curriculum_runner.ore_primer_source(
        thin, _survey(), near=(27.5, 84.5), graph=_fuel_graph()
    )

    assert primer is None
