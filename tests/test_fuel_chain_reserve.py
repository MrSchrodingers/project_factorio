"""What a feed chest keeps has to match what the machine behind it burns.

``FUEL_CHAIN_RESERVE_COAL`` is derived from one burner drill over the feed
horizon and comes to 33. The fuel feed puts 135 into the boiler's chest,
because a boiler burns a coal in 2.2 s against a drill's 26.7 s and is sized
from its own measured draw. The repair loop then offers every container that
feeds a chain everything above 33, so it may draw the boiler's chest down to
a quarter of what that boiler was measured to need -- and a boiler outage
stops every electric machine on the network with it.

No damage has been observed from this yet: ``power_starved_entities`` has
been 0 in every generation measured. The coupling is still wrong, and it is
wrong in the direction that hides: the reserve is read off the wrong machine,
so it happens to be right only while the drills are the ones being drawn
from.

The reserve is therefore derived per container, from the machine the graph
says the container feeds, using the same profile and the same horizon the
feed sized the charge with. A container feeding a machine nothing can size
keeps the drill figure, which is what it kept before.
"""

from __future__ import annotations

from typing import Any

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.fuel import BurnerProfile

#: The boiler draw the runtime reports, in joules per tick.
BOILER_J_PER_TICK = 30000.0


def _graph(machine: str, *, node: str = "machine:1") -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "chest:1", "name": "wooden-chest", "category": "buffer", "x": 0.5, "y": 9.5},
            {"id": "arm:1", "name": "burner-inserter", "category": "transfer", "x": 0.5, "y": 8.5},
            {"id": node, "name": machine, "category": "processing", "x": 0.5, "y": 6.5},
        ],
        "edges": [
            {"source": "chest:1", "target": "arm:1", "relation": "pickup"},
            {"source": "arm:1", "target": node, "relation": "drop"},
        ],
    }


def _boiler_profile() -> BurnerProfile:
    profile = curriculum_runner.profile_from_energy_per_tick("boiler", BOILER_J_PER_TICK)
    assert profile is not None
    return profile


def test_the_boiler_chest_keeps_what_the_boiler_was_measured_to_burn() -> None:
    reserves = curriculum_runner.fuel_chain_reserves(
        _graph("boiler"), boiler_profile=_boiler_profile()
    )

    expected = _boiler_profile().coal_for_seconds(
        curriculum_runner.BOILER_MEASURED_FULL_DRAW_SECONDS
    )
    assert reserves["chest:1"] == expected
    # The whole point: more than the flat drill figure it used to keep.
    assert reserves["chest:1"] > int(curriculum_runner.FUEL_CHAIN_RESERVE_COAL)


def test_a_drill_chest_keeps_the_drill_figure() -> None:
    reserves = curriculum_runner.fuel_chain_reserves(
        _graph("burner-mining-drill"), boiler_profile=_boiler_profile()
    )

    assert reserves["chest:1"] == int(curriculum_runner.FUEL_CHAIN_RESERVE_COAL)


def test_without_a_measured_boiler_draw_the_reserve_is_the_drill_figure() -> None:
    # An unmeasured draw is not a draw of zero. Sizing the boiler's reserve
    # from a guess is what this codebase refuses everywhere else, so the
    # fallback is the figure the loop already kept.
    reserves = curriculum_runner.fuel_chain_reserves(_graph("boiler"), boiler_profile=None)

    assert reserves["chest:1"] == int(curriculum_runner.FUEL_CHAIN_RESERVE_COAL)


def test_a_container_feeding_two_machines_keeps_the_hungrier_one() -> None:
    graph = _graph("burner-mining-drill")
    graph["nodes"].append(
        {"id": "machine:2", "name": "boiler", "category": "processing", "x": 3.5, "y": 6.5}
    )
    graph["edges"].append({"source": "arm:1", "target": "machine:2", "relation": "drop"})

    reserves = curriculum_runner.fuel_chain_reserves(
        graph, boiler_profile=_boiler_profile()
    )

    expected = _boiler_profile().coal_for_seconds(
        curriculum_runner.BOILER_MEASURED_FULL_DRAW_SECONDS
    )
    assert reserves["chest:1"] == expected


def test_a_container_that_feeds_nothing_named_keeps_the_default() -> None:
    # No drop edge: the graph cannot say what emptying this chest would stop,
    # so the reserve stays the figure the loop kept before.
    graph = _graph("boiler")
    graph["edges"] = [{"source": "chest:1", "target": "arm:1", "relation": "pickup"}]

    reserves = curriculum_runner.fuel_chain_reserves(
        graph, boiler_profile=_boiler_profile()
    )

    assert reserves.get("chest:1", int(curriculum_runner.FUEL_CHAIN_RESERVE_COAL)) == int(
        curriculum_runner.FUEL_CHAIN_RESERVE_COAL
    )


def test_the_repair_loop_leaves_the_boiler_its_charge(monkeypatch: Any) -> None:
    # The end-to-end statement: a boiler chest holding exactly the charge the
    # feed gave it offers the repair loop nothing, because every unit in it is
    # still owed to the boiler.
    charge = _boiler_profile().coal_for_seconds(
        curriculum_runner.BOILER_MEASURED_FULL_DRAW_SECONDS
    )

    monkeypatch.setattr(
        curriculum_runner,
        "_chest_item_count",
        lambda _instance, **kwargs: charge,
    )
    monkeypatch.setattr(
        curriculum_runner,
        "_runtime_fuel_feed_figures",
        lambda _instance: {"boiler_energy_per_tick": BOILER_J_PER_TICK},
    )

    observation = type("_Obs", (), {"graph": _graph("boiler")})()
    sources = curriculum_runner.repair_fuel_sources(
        observation, object(), anchor=(0.0, 0.0)
    )

    assert sources == ()


def test_the_repair_loop_still_draws_the_surplus(monkeypatch: Any) -> None:
    # A reserve is not a lock: anything above what the boiler still has to
    # burn is fuel the starved machines may have.
    charge = (
        _boiler_profile().coal_for_seconds(
            curriculum_runner.BOILER_MEASURED_FULL_DRAW_SECONDS
        )
        + 20
    )

    monkeypatch.setattr(
        curriculum_runner,
        "_chest_item_count",
        lambda _instance, **kwargs: charge,
    )
    monkeypatch.setattr(
        curriculum_runner,
        "_runtime_fuel_feed_figures",
        lambda _instance: {"boiler_energy_per_tick": BOILER_J_PER_TICK},
    )

    observation = type("_Obs", (), {"graph": _graph("boiler")})()
    sources = curriculum_runner.repair_fuel_sources(
        observation, object(), anchor=(0.0, 0.0)
    )

    assert len(sources) == 1
    assert sources[0][2] == 20
