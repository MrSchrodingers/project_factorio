"""Ground that carries ore is not the same ground as bare dirt.

The nine positions below are burner inserters standing on resource tiles in
the live factory, read over RCON on 2026-09-23 with
``find_entities_filtered{position=..., type="resource"}`` under each entity.
Eight of them sit on ore the factory is mining. Every one of those tiles is a
tile a drill can no longer use, and the placement layer had no way to see it:
a tile with ore is free of entities, so it was picked exactly like bare dirt.

The iron box ``(15.5, 70.5)-(38.5, 95.5)`` is the patch the curriculum mines,
and the RCON coverage probe recorded in
:func:`factorio_ai_lab.experiments.curriculum_runner.patch_bounds` found all
624 of its tiles carrying ore with zero holes, so "inside the box" and "on
the ore" are the same statement for this patch. For coal and copper only the
single tile under the measured arm is known here, which is what the reading
gave; the scarcity weight is derived from that same reading and is therefore
a statement about the surveyed window, not about the patch on the map.
"""

from __future__ import annotations

from typing import Any

import pytest

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.placement import (
    OUTCOME_BUILD,
    REASON_ANCHOR_FREE,
    REASON_RESOURCE_SPENT,
    REASON_SHIFTED,
    ResourceSurvey,
    WorldSurvey,
    plan_cell_placement,
    plan_placement,
)

ARM = "burner-inserter"
DRILL = "burner-mining-drill"

#: The measured iron patch: every tile of the box carries iron ore.
IRON_BOX = (15.5, 70.5, 38.5, 95.5)
IRON_TILES: tuple[GridPoint, ...] = tuple(
    GridPoint(x, y) for x in range(15, 39) for y in range(70, 96)
)

#: The one coal tile and the one copper tile the reading covered.
COAL_TILE = GridPoint(27, 7)
COPPER_TILE = GridPoint(-58, 81)

#: Tile -> resource, as the nine-arm reading reported it. No window is
#: declared: the reading says where ore is, never where ore is absent.
ORE: dict[GridPoint, str] = {
    **{tile: "iron-ore" for tile in IRON_TILES},
    COAL_TILE: "coal",
    COPPER_TILE: "copper-ore",
}

#: The nine arms standing on resource tiles, position and ore underneath.
ARMS_ON_ORE: tuple[tuple[tuple[float, float], str], ...] = (
    ((-57.5, 81.5), "copper-ore"),
    ((27.5, 7.5), "coal"),
    ((19.5, 81.5), "iron-ore"),
    ((23.5, 87.5), "iron-ore"),
    ((25.5, 86.5), "iron-ore"),
    ((27.5, 71.5), "iron-ore"),
    ((27.5, 81.5), "iron-ore"),
    ((27.5, 86.5), "iron-ore"),
    ((32.5, 81.5), "iron-ore"),
)


def _survey(window: tuple[float, float, float, float] | None = None) -> ResourceSurvey:
    return ResourceSurvey(tiles=dict(ORE), surveyed=window)


def _resource_entity(name: str, x: float, y: float) -> dict[str, Any]:
    return {"name": name, "type": "resource", "position": {"x": x, "y": y}, "amount": 1000}


@pytest.mark.parametrize(
    "anchor,ore",
    ARMS_ON_ORE,
    ids=[f"{x}_{y}" for (x, y), _ in ARMS_ON_ORE],
)
def test_an_arm_reaches_past_the_ore_when_bare_ground_is_within_reach(
    anchor: tuple[float, float],
    ore: str,
) -> None:
    # Reach 20 is wider than any arm the curriculum uses: the point is the
    # preference, not the distance. Every one of the nine anchors has bare
    # ground inside it, and the layer must spend none of the patch.
    plan = plan_placement(
        entity=ARM,
        anchor=anchor,
        world=(),
        adopt_names=frozenset(),
        reach=20,
        resources=_survey(),
    )

    assert plan.outcome == OUTCOME_BUILD
    assert plan.resource_tiles == 0, f"{anchor} sobre {ore} ainda gasta minerio: {plan}"
    assert not set(plan.tiles) & set(ORE)


def test_the_nearest_bare_tile_is_the_one_taken() -> None:
    # The arm at (27.5, 71.5) is two tiles below the northern edge of the
    # patch. The scan order decides among equals, so the answer is the
    # nearest bare tile and not merely some bare tile.
    plan = plan_placement(
        entity=ARM,
        anchor=(27.5, 71.5),
        world=(),
        adopt_names=frozenset(),
        reach=20,
        resources=_survey(),
    )

    assert plan.position == (27.5, 69.5)
    assert plan.shift == (0, -2)
    assert plan.reason == REASON_SHIFTED


def test_an_arm_with_no_bare_tile_in_reach_spends_the_ore_and_says_so() -> None:
    # Deep inside the patch: every tile within reach carries iron. Refusing
    # here would stop a stage that can still build, so the layer builds and
    # records the tile it spent.
    plan = plan_placement(
        entity=ARM,
        anchor=(27.5, 81.5),
        world=(),
        adopt_names=frozenset(),
        reach=2,
        resources=_survey(),
    )

    assert plan.outcome == OUTCOME_BUILD
    assert plan.position == (27.5, 81.5)
    assert plan.reason == REASON_RESOURCE_SPENT
    assert plan.resource_tiles == 1
    assert plan.resource_names == ("iron-ore",)
    assert plan.resource_cost is not None and plan.resource_cost > 0


def test_a_scarce_resource_costs_more_than_an_abundant_one() -> None:
    # 624 iron tiles and one coal tile were surveyed, so a coal tile is the
    # scarcer ground of the two by the same reading that found both.
    on_coal = plan_placement(
        entity=ARM,
        anchor=(27.5, 7.5),
        world=(),
        adopt_names=frozenset(),
        resources=_survey(),
    )
    on_iron = plan_placement(
        entity=ARM,
        anchor=(27.5, 81.5),
        world=(),
        adopt_names=frozenset(),
        resources=_survey(),
    )

    assert on_coal.resource_tiles == on_iron.resource_tiles == 1
    assert on_coal.resource_names == ("coal",)
    assert on_iron.resource_names == ("iron-ore",)
    assert on_coal.resource_cost is not None and on_iron.resource_cost is not None
    assert on_coal.resource_cost > on_iron.resource_cost


def test_the_drill_is_not_pushed_off_the_resource() -> None:
    # A drill on bare ground mines nothing. It is charged nothing for the
    # ore it stands on, and bare ground two tiles north must not attract it.
    plan = plan_placement(
        entity=DRILL,
        anchor=(27.0, 71.0),
        world=(),
        adopt_names=frozenset(),
        reach=6,
        resources=_survey(),
    )

    assert plan.outcome == OUTCOME_BUILD
    assert plan.position == (27.0, 71.0)
    assert plan.shift == (0, 0)
    assert plan.reason == REASON_ANCHOR_FREE
    assert set(plan.tiles) <= set(ORE)
    assert plan.resource_tiles == 4
    assert plan.resource_cost == 0.0


def test_a_tile_the_survey_did_not_cover_is_not_read_as_bare_ground() -> None:
    # The north-west corner of the patch, (15, 70), with a window covering
    # x 15..19, y 69..73. The first tile the scan offers, (14, 70), lies
    # outside the window: unknown, not bare. The measured bare tile
    # (15, 69) comes later in the scan order and still wins.
    window = (14.5, 68.5, 20.5, 74.5)
    plan = plan_placement(
        entity=ARM,
        anchor=(15.5, 70.5),
        world=(),
        adopt_names=frozenset(),
        reach=1,
        resources=_survey(window),
    )

    assert plan.position == (15.5, 69.5)
    assert plan.shift == (0, -1)
    assert plan.resource_tiles == 0
    assert plan.resource_unsurveyed == 0


def test_without_a_declared_window_the_unknown_tile_is_only_unknown() -> None:
    # Same geometry, no window: (14, 70) and (15, 69) are both unknown, and
    # the scan order decides between equals, so the answer moves west
    # instead of north. The unknown tile is still preferred over the
    # measured ore, and the plan says the tile was never surveyed.
    plan = plan_placement(
        entity=ARM,
        anchor=(15.5, 70.5),
        world=(),
        adopt_names=frozenset(),
        reach=1,
        resources=_survey(),
    )

    assert plan.position == (14.5, 70.5)
    assert plan.shift == (-1, 0)
    assert plan.resource_tiles == 0
    assert plan.resource_unsurveyed == 1


def test_without_a_resource_survey_nothing_is_claimed_about_the_ground() -> None:
    plan = plan_placement(
        entity=ARM,
        anchor=(27.5, 81.5),
        world=(),
        adopt_names=frozenset(),
        reach=2,
    )

    assert plan.position == (27.5, 81.5)
    assert plan.reason == REASON_ANCHOR_FREE
    assert plan.resource_tiles is None
    assert plan.resource_cost is None
    assert plan.resource_unsurveyed is None


def test_the_same_survey_answers_the_same_tile() -> None:
    shuffled = ResourceSurvey(
        tiles={tile: ORE[tile] for tile in sorted(ORE, key=lambda t: (-t.y, -t.x))},
        surveyed=None,
    )
    first = plan_placement(
        entity=ARM,
        anchor=(27.5, 71.5),
        world=(),
        adopt_names=frozenset(),
        reach=20,
        resources=_survey(),
    )
    second = plan_placement(
        entity=ARM,
        anchor=(27.5, 71.5),
        world=(),
        adopt_names=frozenset(),
        reach=20,
        resources=shuffled,
    )

    assert first.position == second.position
    assert first.resource_cost == second.resource_cost


def test_a_survey_keeps_resource_rows_out_of_the_blocking_world() -> None:
    # Ore is not an obstacle: a payload that carried resource rows into the
    # entity list would mark every tile of the patch taken and refuse the
    # whole patch.
    payload = (
        {"name": DRILL, "position": {"x": 19.0, "y": 83.0}, "direction": 8},
        _resource_entity("iron-ore", 27.5, 81.5),
        _resource_entity("iron-ore", 27.5, 82.5),
    )
    survey = WorldSurvey.from_entities(payload, footprints={})

    assert len(survey.entities) == 1
    assert survey.resources is not None
    assert survey.resources.tiles == {
        GridPoint(27, 81): "iron-ore",
        GridPoint(27, 82): "iron-ore",
    }

    plan = plan_cell_placement(
        survey,
        (27.5, 81.5),
        entity=ARM,
        adopt_names=frozenset(),
    )
    assert plan.outcome == OUTCOME_BUILD
    assert plan.resource_tiles == 1


def test_a_payload_without_resource_rows_declares_nothing_about_the_ore() -> None:
    # The read that excludes resources and the world with no ore in it look
    # the same from here, so the survey answers "unknown" rather than "none".
    survey = WorldSurvey.from_entities(
        ({"name": DRILL, "position": {"x": 19.0, "y": 83.0}, "direction": 8},),
        footprints={},
    )

    assert survey.resources is None

    plan = plan_cell_placement(survey, (27.5, 81.5), entity=ARM, adopt_names=frozenset())
    assert plan.resource_tiles is None


def test_the_spent_ground_is_recorded_in_the_plan() -> None:
    recorded = plan_placement(
        entity=ARM,
        anchor=(27.5, 81.5),
        world=(),
        adopt_names=frozenset(),
        reach=2,
        resources=_survey(),
    ).to_dict()

    assert recorded["reason"] == REASON_RESOURCE_SPENT
    assert recorded["resource_tiles"] == 1
    assert recorded["resource_names"] == ["iron-ore"]
    assert recorded["resource_unsurveyed"] == 0
    assert recorded["resource_cost"] is not None
