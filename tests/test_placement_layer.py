"""The placement layer decides by tiles, and the live engine is the oracle.

Every anchor this curriculum computes is deterministic on a fixed map, so an
heir of a promoted factory aims at the tiles its ancestor already built on.
The entities below are the inherited world as RCON reported it on
2026-09-23: four burner mining drills in a row at y=83, the chests and
inserters of the baseline cells, the belt lane at y=84 and two furnaces.

``can_place`` on the ten probed positions is what
``surface.can_place_entity`` answered for a burner mining drill on that same
world, read over RCON. It is the oracle here: a tile model that disagrees
with the engine would let a stage plan a placement the engine then refuses,
which is the failure this layer exists to remove.
"""

from __future__ import annotations

from typing import Any

import pytest

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.placement import (
    OUTCOME_ADOPT,
    OUTCOME_BUILD,
    OUTCOME_REFUSE,
    REASON_ALREADY_STANDING,
    REASON_ANCHOR_FREE,
    REASON_SHIFTED,
    REASON_TILES_TAKEN,
    REASON_WORLD_UNREAD,
    WorldSurvey,
    plan_cell_placement,
    plan_placement,
    scan_offsets,
    snap_to_grid,
)

DRILL = "burner-mining-drill"

#: The iron patch bounding box measured on the live world. Every one of its
#: 624 tiles carries iron ore: the RCON coverage probe reported zero holes,
#: so "inside this box" is equivalent to "on the ore" on this map.
PATCH = (15.5, 70.5, 38.5, 95.5)


def _entity(name: str, x: float, y: float, direction: int = 0) -> dict[str, Any]:
    return {"name": name, "position": {"x": x, "y": y}, "direction": direction}


INHERITED_WORLD: tuple[dict[str, Any], ...] = (
    _entity("wooden-chest", 27.5, 70.5),
    _entity("burner-inserter", 27.5, 71.5),
    _entity(DRILL, 27.0, 73.0, 8),
    _entity("stone-furnace", 27.0, 75.0),
    _entity("wooden-chest", 19.5, 80.5),
    _entity("burner-inserter", 19.5, 81.5),
    _entity("wooden-chest", 27.5, 80.5),
    _entity("burner-inserter", 27.5, 81.5),
    _entity("wooden-chest", 32.5, 80.5),
    _entity("burner-inserter", 32.5, 81.5),
    _entity(DRILL, 19.0, 83.0, 8),
    _entity(DRILL, 23.0, 83.0, 8),
    _entity(DRILL, 27.0, 83.0, 8),
    _entity(DRILL, 32.0, 83.0, 8),
    _entity("transport-belt", 19.5, 84.5, 4),
    _entity("transport-belt", 20.5, 84.5, 4),
    _entity("transport-belt", 21.5, 84.5, 4),
    _entity("transport-belt", 22.5, 84.5, 4),
    _entity("transport-belt", 23.5, 84.5, 4),
    _entity("transport-belt", 24.5, 84.5, 8),
    _entity("transport-belt", 24.5, 85.5, 8),
    _entity("character", 24.9921875, 85.76953125),
    _entity("wooden-chest", 27.5, 84.5),
    _entity("wooden-chest", 32.5, 84.5),
    _entity("burner-inserter", 25.5, 86.5, 12),
    _entity("transport-belt", 24.5, 86.5, 4),
    _entity("burner-inserter", 27.5, 86.5, 12),
    _entity("wooden-chest", 26.5, 86.5),
    _entity("stone-furnace", 29.0, 87.0),
)

#: position -> what surface.can_place_entity answered for a burner mining
#: drill facing south, measured over RCON on the world above.
ENGINE_ANSWERS: tuple[tuple[tuple[float, float], bool], ...] = (
    ((27.0, 83.0), False),
    ((31.5, 83.0), False),
    ((27.0, 77.5), True),
    ((27.0, 88.5), True),
    ((17.5, 74.5), True),
    ((36.5, 91.5), True),
    ((27.0, 60.0), True),
    ((0.0, 0.0), True),
    ((29.0, 83.0), True),
    ((25.0, 83.0), True),
)


def _plan(anchor: tuple[float, float], **kwargs: Any):
    return plan_placement(
        entity=DRILL,
        anchor=anchor,
        world=INHERITED_WORLD,
        **kwargs,
    )


@pytest.mark.parametrize(
    "anchor,can_place",
    ENGINE_ANSWERS,
    ids=[f"{x}_{y}" for (x, y), _ in ENGINE_ANSWERS],
)
def test_the_tile_decision_agrees_with_the_live_engine(
    anchor: tuple[float, float],
    can_place: bool,
) -> None:
    plan = _plan(anchor)
    if can_place:
        assert plan.outcome == OUTCOME_BUILD, (
            f"o motor aceita {anchor} e a camada recusou: {plan}"
        )
        assert plan.position == snap_to_grid(anchor, (2, 2))
        assert plan.shift == (0, 0)
    else:
        assert plan.outcome != OUTCOME_BUILD, (
            f"o motor recusa {anchor} e a camada mandou construir: {plan}"
        )


def test_an_occupied_anchor_is_adopted_not_rebuilt() -> None:
    plan = _plan((27.0, 83.0))

    assert plan.outcome == OUTCOME_ADOPT
    assert plan.position == (27.0, 83.0)
    assert plan.reason == REASON_ALREADY_STANDING
    assert plan.adopted_name == DRILL


def test_a_half_tile_anchor_is_snapped_before_the_tiles_are_read() -> None:
    # The engine snaps an even-sided entity onto a tile boundary: the arm
    # aimed at x=31.5 stands at x=32 in the live world, on top of a drill
    # that is already there. Reading the tiles of the unsnapped centre would
    # answer 30..31 and call the placement free.
    assert snap_to_grid((31.5, 83.0), (2, 2)) == (32.0, 83.0)
    assert snap_to_grid((27.0, 77.5), (2, 2)) == (27.0, 78.0)
    # An odd-sided entity is centred on the tile instead.
    assert snap_to_grid((27.0, 83.0), (1, 1)) == (27.5, 83.5)

    assert _plan((31.5, 83.0)).outcome == OUTCOME_ADOPT


def test_a_taken_anchor_shifts_to_free_tiles_when_nothing_may_be_adopted() -> None:
    # A trial has to build its own cell: adopting the ancestor's drill would
    # measure the ancestor. With adoption disabled the anchor is simply
    # taken, and the scan answers the nearest free tiles.
    plan = _plan((31.5, 83.0), adopt_names=frozenset(), reach=6, region=PATCH)

    assert plan.outcome == OUTCOME_BUILD
    assert plan.reason == REASON_SHIFTED
    assert plan.position == (30.0, 83.0)
    assert plan.shift == (-2, 0)
    assert set(plan.tiles) == {
        GridPoint(29, 82),
        GridPoint(30, 82),
        GridPoint(29, 83),
        GridPoint(30, 83),
    }


def test_the_reserved_output_tile_is_part_of_the_decision() -> None:
    # The cell is a drill plus the chest it drops into. West of the centre
    # the nearest free drill tiles are at (21, 83), but the tile under them
    # carries the belt lane, so place_entity_next_to would fail the trial.
    # Reserving the output tile walks the scan on to (25, 83), whose output
    # tile is clear.
    def reserve(tiles: frozenset[GridPoint]) -> tuple[GridPoint, ...]:
        bottom = max(tile.y for tile in tiles)
        right = max(tile.x for tile in tiles)
        return (GridPoint(right, bottom + 1),)

    unreserved = _plan((22.5, 83.0), adopt_names=frozenset(), reach=6, region=PATCH)
    assert unreserved.position == (21.0, 83.0)

    reserved = _plan(
        (22.5, 83.0),
        adopt_names=frozenset(),
        reach=6,
        region=PATCH,
        extra_tiles=reserve,
    )
    assert reserved.outcome == OUTCOME_BUILD
    assert reserved.position == (25.0, 83.0)
    assert reserved.shift == (2, 0)


def test_the_scan_stays_inside_the_measured_resource_patch() -> None:
    # One tile inside the eastern edge of the patch, boxed in by a wall of
    # drills: shifting further east would leave the ore behind.
    wall = tuple(
        _entity(DRILL, x, y)
        for x in (36.0, 38.0)
        for y in (80.0, 82.0, 84.0)
    )
    plan = plan_placement(
        entity=DRILL,
        anchor=(37.0, 82.0),
        world=wall,
        adopt_names=frozenset(),
        reach=1,
        region=(35.5, 70.5, 38.5, 95.5),
    )
    assert plan.outcome == OUTCOME_REFUSE
    assert plan.reason == REASON_TILES_TAKEN


def test_an_anchor_outside_the_region_is_not_overruled() -> None:
    # A genome may scale the placement arms past the patch. That is the
    # stage's decision; the region only keeps a *shift* from leaving the ore.
    plan = plan_placement(
        entity=DRILL,
        anchor=(60.0, 60.0),
        world=INHERITED_WORLD,
        reach=2,
        region=PATCH,
    )
    assert plan.outcome == OUTCOME_BUILD
    assert plan.position == (60.0, 60.0)


def test_a_refusal_is_a_result_not_an_exception() -> None:
    boxed = tuple(
        _entity(DRILL, 27.0 + dx, 83.0 + dy)
        for dx in (-2, 0, 2)
        for dy in (-2, 0, 2)
    )
    plan = plan_placement(
        entity=DRILL,
        anchor=(27.0, 83.0),
        world=boxed,
        adopt_names=frozenset(),
        reach=1,
    )
    assert plan.outcome == OUTCOME_REFUSE
    assert plan.reason == REASON_TILES_TAKEN
    assert plan.position is None
    assert plan.scanned == len(scan_offsets(1))


def test_the_scan_order_is_fixed_and_starts_on_the_anchor() -> None:
    offsets = scan_offsets(2)

    assert offsets[0] == (0, 0)
    assert offsets[1:5] == ((-1, 0), (0, -1), (0, 1), (1, 0))
    assert len(offsets) == 25
    assert scan_offsets(2) == offsets
    assert scan_offsets(0) == ((0, 0),)


def test_the_same_anchor_in_the_same_world_answers_the_same_tile() -> None:
    shuffled = tuple(reversed(INHERITED_WORLD))
    first = _plan((31.5, 83.0), adopt_names=frozenset(), reach=6, region=PATCH)
    second = plan_placement(
        entity=DRILL,
        anchor=(31.5, 83.0),
        world=shuffled,
        adopt_names=frozenset(),
        reach=6,
        region=PATCH,
    )
    assert first.position == second.position
    assert first.shift == second.shift


def test_a_free_anchor_is_built_on_without_being_moved() -> None:
    plan = _plan((27.0, 77.5), reach=6, region=PATCH)

    assert plan.outcome == OUTCOME_BUILD
    assert plan.reason == REASON_ANCHOR_FREE
    assert plan.shift == (0, 0)
    assert plan.scanned == 1


def test_a_world_that_could_not_be_read_builds_on_the_anchor_and_says_so() -> None:
    plan = plan_cell_placement(None, (27.0, 83.0), entity=DRILL, reach=6)

    assert plan.outcome == OUTCOME_BUILD
    assert plan.position == (27.0, 83.0)
    assert plan.reason == REASON_WORLD_UNREAD


def test_a_survey_plans_the_same_decision_as_the_raw_world() -> None:
    survey = WorldSurvey(entities=INHERITED_WORLD, footprints={})
    plan = plan_cell_placement(survey, (27.0, 83.0), entity=DRILL)

    assert plan.outcome == OUTCOME_ADOPT
    assert plan.position == (27.0, 83.0)


def test_the_plan_is_recorded_in_full() -> None:
    recorded = _plan((31.5, 83.0), adopt_names=frozenset(), reach=6).to_dict()

    assert recorded["outcome"] == OUTCOME_BUILD
    assert recorded["reason"] == REASON_SHIFTED
    assert recorded["position"] == {"x": 30.0, "y": 83.0}
    assert recorded["shift"] == {"x": -2, "y": 0}
