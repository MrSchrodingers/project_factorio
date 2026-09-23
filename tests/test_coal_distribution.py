"""The coal the factory mined has to reach the machines that burn it.

Generation 72 loaded 286 coal into seven chests and still ended with six
machines at ``no_fuel``. The fuel feed fills each chest once, out of a stock
nothing replaces, while the coal drill mines into its own output chest the
whole time and no arm ever carries the two together. That is a battery, not a
factory, and this module is the carry.

What is pinned here:

``only a chest that burns coal, and only one nothing refills``
    a container feeding a furnace's ore input is not a fuel feed, and coal
    tipped into it is coal spent on nothing and an input jammed. A container
    something already drops into has a supplier, and a second lane to it is a
    duplicate.

``the script has to be Python the engine can run``
    every script this runner returns is audited by ``test_fle_triggers`` and
    ``test_fuel_feed``, which replace the formatted fields with ``0`` and
    parse the result. That audit passing is necessary and not sufficient: the
    script also has to parse with the real values in it, which is what the
    engine will actually be handed.

``two lanes may not be laid through the same tile``
    each link is planned against what is standing plus what the links before
    it will add, or the second lane fails inside ``place_entity`` after the
    first one is already committed.
"""

from __future__ import annotations

import ast
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.delivery import MODE_REFUSED
from factorio_ai_lab.planning.placement import WorldSurvey

#: The coal cell as stage 6 builds it: a 2x2 drill with its output chest on
#: the tile below its right column.
COAL_DRILL = {
    "name": "burner-mining-drill",
    "position": {"x": 27.0, "y": 9.0},
    "direction": 8,
}
COAL_CHEST = {
    "name": "wooden-chest",
    "position": {"x": 27.5, "y": 10.5},
    "direction": 0,
}
#: A feed chest the fuel feed built, four tiles north of the drill.
FEED_CHEST = {
    "name": "wooden-chest",
    "position": {"x": 27.5, "y": 5.5},
    "direction": 0,
}


class _Chests:
    """Container contents as RCON would report them."""

    def __init__(self, contents: dict[tuple[float, float], dict[str, int]]) -> None:
        self._contents = contents

    def __call__(self, _instance: Any, **kwargs: Any) -> int:
        held = self._contents.get((kwargs["x"], kwargs["y"]), {})
        item = kwargs.get("item")
        if item is None:
            return sum(held.values())
        return held.get(item, 0)


def _graph(*, fed_by_chain: bool = False, machine: str = "burner-mining-drill") -> dict[str, Any]:
    """Feed chest -> arm -> machine, in the shape ``build_factory_graph`` emits."""
    nodes = [
        {"id": "chest:1", "name": "wooden-chest", "category": "buffer", "x": 27.5, "y": 5.5},
        {"id": "arm:1", "name": "burner-inserter", "category": "transfer", "x": 27.5, "y": 6.5},
        {"id": "machine:1", "name": machine, "category": "extraction", "x": 27.0, "y": 9.0},
    ]
    edges = [
        {"source": "chest:1", "target": "arm:1", "relation": "pickup"},
        {"source": "arm:1", "target": "machine:1", "relation": "drop"},
    ]
    if fed_by_chain:
        nodes.append(
            {"id": "belt:1", "name": "transport-belt", "category": "transport", "x": 28.5, "y": 5.5}
        )
        edges.append({"source": "belt:1", "target": "chest:1", "relation": "belt_flow"})
    return {"nodes": nodes, "edges": edges}


def _survey() -> WorldSurvey:
    return WorldSurvey.from_entities(
        (COAL_DRILL, COAL_CHEST, FEED_CHEST),
        footprints={},
    )


def _targets(monkeypatch: Any, graph: dict[str, Any], contents: dict[Any, Any]) -> Any:
    monkeypatch.setattr(curriculum_runner, "_chest_item_count", _Chests(contents))
    return curriculum_runner.coal_delivery_targets(graph, object())


def test_a_feed_chest_that_burns_coal_is_a_target(monkeypatch: Any) -> None:
    targets = _targets(monkeypatch, _graph(), {(27.5, 5.5): {}})

    assert len(targets) == 1
    role, machine, refusal = targets[0]
    assert machine == "burner-mining-drill"
    assert refusal is None
    assert role.position == (27.5, 5.5)


def test_a_chest_something_already_fills_is_left_alone(monkeypatch: Any) -> None:
    targets = _targets(monkeypatch, _graph(fed_by_chain=True), {(27.5, 5.5): {}})

    assert len(targets) == 1
    assert targets[0][2] == curriculum_runner.COAL_LINK_ALREADY_FED


def test_a_chest_holding_ore_is_not_a_fuel_feed(monkeypatch: Any) -> None:
    # The input chest of a furnace. Coal tipped in here is spent on nothing
    # and the furnace input is jammed with it.
    targets = _targets(monkeypatch, _graph(), {(27.5, 5.5): {"iron-ore": 40}})

    assert targets[0][2] == curriculum_runner.COAL_LINK_NOT_COAL


def test_a_chest_feeding_a_machine_that_burns_nothing_is_not_a_target(
    monkeypatch: Any,
) -> None:
    targets = _targets(
        monkeypatch,
        _graph(machine="assembling-machine-1"),
        {(27.5, 5.5): {}},
    )

    assert targets == ()


def test_the_lane_reaches_from_the_output_chest_to_the_feed_chest() -> None:
    links = curriculum_runner.plan_coal_distribution(
        _survey(),
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                None,
            ),
        ),
    )

    assert len(links) == 1
    link = links[0].link
    assert link.builds
    # The arm lifting out of the source picks up from the output chest tile.
    assert link.lift is not None and link.lift.picks_from == GridPoint(27, 10)
    # Nothing is laid on the drill itself.
    assert not set(link.path) & {
        GridPoint(26, 8),
        GridPoint(27, 8),
        GridPoint(26, 9),
        GridPoint(27, 9),
    }


def test_the_script_is_python_the_engine_can_run() -> None:
    # The field-stubbing audit in test_fle_triggers proves the template
    # parses; this proves the script parses with the real values in it.
    links = curriculum_runner.plan_coal_distribution(
        _survey(),
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                None,
            ),
        ),
    )
    script = curriculum_runner.coal_distribution_script(links)

    ast.parse(script)
    assert "place_entity" in script
    assert "Prototype.BurnerInserter" in script
    # Every arm gets its primer, or it never moves the first coal.
    assert "insert_item(Prototype.Coal" in script


def test_every_placement_is_preceded_by_a_walk_to_it() -> None:
    # Generation 80 planned two lanes, committed the transaction and built
    # neither: "The target position is too far away to place the entity".
    # A lane is laid tile by tile across the map and leaves build reach long
    # before its far end, so each placement walks to its own tile first.
    links = curriculum_runner.plan_coal_distribution(
        _survey(),
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                None,
            ),
        ),
    )
    script = curriculum_runner.coal_distribution_script(links)

    tree = ast.parse(script)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    placements = [index for index, name in enumerate(calls) if name == "place_entity"]
    assert placements, "o script nao coloca nada"
    for index in placements:
        assert "move_to" in calls[:index], (
            "uma colocacao sem move_to antes dela sai do alcance de construcao"
        )
    # One walk per placement, not one for the whole lane.
    assert calls.count("move_to") >= len(placements)


def test_the_script_never_trips_the_engine_failure_heuristic() -> None:
    # fle/env/gym_env/environment.py:451 marks a step failed when the printed
    # text holds either substring, so a caught exception may never be printed
    # verbatim.
    links = curriculum_runner.plan_coal_distribution(
        _survey(),
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                None,
            ),
        ),
    )
    script = curriculum_runner.coal_distribution_script(links)

    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "error" not in node.value.lower()
            assert "exception: " not in node.value.lower()


def test_a_refused_target_is_recorded_and_not_built() -> None:
    links = curriculum_runner.plan_coal_distribution(
        _survey(),
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                curriculum_runner.COAL_LINK_ALREADY_FED,
            ),
        ),
    )

    assert len(links) == 1
    assert links[0].link.mode == MODE_REFUSED
    assert links[0].link.reason == curriculum_runner.COAL_LINK_ALREADY_FED
    # Nothing refused may reach the script.
    assert curriculum_runner.coal_distribution_script(
        [item for item in links if item.link.builds]
    ).count("place_entity") == 0


def test_a_second_lane_avoids_the_first_one() -> None:
    second = dict(FEED_CHEST, position={"x": 24.5, "y": 5.5})
    survey = WorldSurvey.from_entities(
        (COAL_DRILL, COAL_CHEST, FEED_CHEST, second),
        footprints={},
    )
    links = curriculum_runner.plan_coal_distribution(
        survey,
        source=(27.5, 10.5),
        targets=(
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:1",
                    name="wooden-chest",
                    position=(27.5, 5.5),
                    supplies_chain=True,
                ),
                "burner-mining-drill",
                None,
            ),
            (
                curriculum_runner.ContainerRole(
                    node_id="chest:2",
                    name="wooden-chest",
                    position=(24.5, 5.5),
                    supplies_chain=True,
                ),
                "boiler",
                None,
            ),
        ),
        belt_budget=40,
    )

    built = [item.link for item in links if item.link.builds]
    assert len(built) == 2
    assert not set(built[0].path) & set(built[1].path)


def test_without_a_source_chest_nothing_is_planned() -> None:
    assert (
        curriculum_runner.plan_coal_distribution(
            _survey(), source=None, targets=()
        )
        == ()
    )


def test_without_a_survey_nothing_is_claimed() -> None:
    assert (
        curriculum_runner.plan_coal_distribution(
            None, source=(27.5, 10.5), targets=()
        )
        == ()
    )
