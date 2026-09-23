"""What a machine produced has to reach the machine that consumes it.

The world read of 2026-09-23 found five of nine drills at ``no_fuel``, seven
of ten arms at ``waiting_for_source_items`` and three of five furnaces at
``no_ingredients`` with one at ``full_output``. Every one of those is the
same shape of defect: a machine fills a chest, and nothing ever takes the
contents out again. The coal drill mines into its own output chest while the
boiler two hundred tiles away stands at ``no_fuel``.

This module decides the link, over tiles, and never over a radius:

``one arm``
    an inserter occupies one tile, picks up from the tile behind it and drops
    on the tile in front. Two containers exactly two tiles apart on one axis,
    with that middle tile free, are joined by a single arm. Note what this
    rules out: two *adjacent* containers cannot be joined at all, because the
    arm would have to stand on one of them. A chest placed flush against the
    drill that fills it is a dead end by construction, which is why the coal
    cell cannot be closed by dropping one inserter next to it.

``an arm, a lane, an arm``
    otherwise the source is lifted onto a belt, the belt is routed around
    what is standing, and a second arm lifts it off into the target. The lane
    is budgeted: a link that would cost more belt than the budget is refused
    by name rather than built halfway.

``a refusal is a result``
    a target with no free tile beside it, or no free lane to it, is reported
    as such. Building a lane that stops short leaves belts on the map that
    carry coal into open ground, which is worse than not building.
"""

from __future__ import annotations

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.delivery import (
    MODE_BELT,
    MODE_INSERTER,
    MODE_REFUSED,
    REFUSAL_NO_TARGET_TILE,
    REFUSAL_OVER_BUDGET,
    plan_delivery,
)


def _tile(x: int, y: int) -> frozenset[GridPoint]:
    return frozenset({GridPoint(x, y)})


def test_two_tiles_apart_is_joined_by_a_single_arm() -> None:
    # Source at (10, 10), target at (12, 10): the tile between them is free,
    # so one inserter standing on (11, 10) picks up west and drops east.
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(12, 10),
        blocked=frozenset(),
    )

    assert link.mode == MODE_INSERTER
    assert link.lift is not None
    assert link.lift.position == (11.5, 10.5)
    assert link.lift.picks_from == GridPoint(10, 10)
    assert link.lift.drops_at == GridPoint(12, 10)
    assert link.lift.direction == "RIGHT"
    assert link.path == ()


def test_the_single_arm_works_on_every_axis() -> None:
    upward = plan_delivery(
        source_tiles=_tile(10, 12),
        target_tiles=_tile(10, 10),
        blocked=frozenset(),
    )

    assert upward.mode == MODE_INSERTER
    assert upward.lift is not None
    assert upward.lift.direction == "UP"
    assert upward.lift.position == (10.5, 11.5)


def test_an_occupied_middle_tile_is_not_an_arm_tile() -> None:
    # The gap is there but something is standing in it, so the single-arm
    # link is not available and the plan falls through to a lane.
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(12, 10),
        blocked=frozenset({GridPoint(11, 10)}),
    )

    assert link.mode == MODE_BELT


def test_adjacent_containers_cannot_be_joined_by_one_arm() -> None:
    # This is the coal cell: the drill drops into a chest flush against it.
    # There is no tile for an arm to stand on between the two, so the link
    # has to go around, and a planner that answered MODE_INSERTER here would
    # be asking the engine to build an inserter on top of a chest.
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(11, 10),
        blocked=frozenset(),
    )

    assert link.mode != MODE_INSERTER


def test_a_distant_target_is_reached_by_an_arm_a_lane_and_an_arm() -> None:
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(20, 10),
        blocked=frozenset(),
    )

    assert link.mode == MODE_BELT
    assert link.lift is not None and link.drop is not None
    # The arm out of the source picks up from the source itself.
    assert link.lift.picks_from == GridPoint(10, 10)
    # The arm into the target drops into the target itself.
    assert link.drop.drops_at == GridPoint(20, 10)
    # The belt runs from where the first arm drops to where the second picks
    # up, and both ends are part of the lane.
    assert link.path[0] == link.lift.drops_at
    assert link.path[-1] == link.drop.picks_from
    # No belt may be laid on the source, the target or either arm.
    assert GridPoint(10, 10) not in link.path
    assert GridPoint(20, 10) not in link.path
    assert GridPoint(*map(int, (link.lift.position[0] - 0.5, link.lift.position[1] - 0.5))) not in link.path


def test_a_lane_never_runs_through_what_is_standing() -> None:
    wall = frozenset(GridPoint(15, y) for y in range(5, 15))
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(20, 10),
        blocked=wall,
        belt_budget=60,
    )

    assert link.mode == MODE_BELT
    assert not set(link.path) & wall


def test_a_lane_longer_than_the_budget_is_refused_by_name() -> None:
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(60, 10),
        blocked=frozenset(),
        belt_budget=8,
    )

    assert link.mode == MODE_REFUSED
    assert link.reason == REFUSAL_OVER_BUDGET
    assert link.path == ()


def test_a_target_nothing_can_reach_is_refused_by_name() -> None:
    # The target is walled in on all four sides: no arm can stand beside it.
    # The refusal names that specifically, rather than reporting a lane that
    # was never planned -- the two lead to different next actions, and a
    # caller told "too far" would widen a budget that was never the problem.
    walled = frozenset(
        {
            GridPoint(19, 10),
            GridPoint(21, 10),
            GridPoint(20, 9),
            GridPoint(20, 11),
        }
    )
    link = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(20, 10),
        blocked=walled,
    )

    assert link.mode == MODE_REFUSED
    assert link.reason == REFUSAL_NO_TARGET_TILE


def test_the_same_world_answers_the_same_link() -> None:
    # Replay matters as much here as in the placement layer: a link that
    # cannot be replayed from the run seed cannot be replayed at all.
    first = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(20, 14),
        blocked=frozenset({GridPoint(15, y) for y in range(8, 13)}),
        belt_budget=60,
    )
    second = plan_delivery(
        source_tiles=_tile(10, 10),
        target_tiles=_tile(20, 14),
        blocked=frozenset({GridPoint(15, y) for y in range(12, 7, -1)}),
        belt_budget=60,
    )

    assert first.path == second.path
    assert first.lift == second.lift
    assert first.drop == second.drop


def test_a_multi_tile_source_is_lifted_from_one_of_its_own_tiles() -> None:
    # A 2x2 drill occupies four tiles; the arm has to pick up from a tile the
    # drill actually covers, not from its centre, which is a tile corner.
    drill = frozenset(
        {
            GridPoint(26, 8),
            GridPoint(27, 8),
            GridPoint(26, 9),
            GridPoint(27, 9),
        }
    )
    link = plan_delivery(
        source_tiles=drill,
        target_tiles=_tile(26, 14),
        blocked=frozenset(),
    )

    assert link.mode == MODE_BELT
    assert link.lift is not None
    assert link.lift.picks_from in drill
