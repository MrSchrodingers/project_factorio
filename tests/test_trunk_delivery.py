"""One line out of the source, one arm per target.

The world read of 2026-09-23 counted fifteen feed chests and two lanes: the
other thirteen were refused ``no_free_tile_beside_the_source``. The source is
a 1x1 chest, a chest has four sides, and every lane spends one of them on its
own lifting arm -- so the second lane leaves two sides, the third leaves one,
and the fourth is refused against a world that has plenty of room. Planning
thirteen independent links out of one chest is the defect; a trunk is the
answer. One arm lifts out of the source, one belt line runs past the targets,
and each target gets an arm that takes material off that line.

What is pinned here:

``the source is lifted once``
    however many targets the trunk serves, exactly one arm stands beside the
    source. A planner that spends a second side of the chest has rebuilt the
    defect.

``every belt points at the next belt``
    a trunk whose tiles point at the arm beside them stops the material at
    the first branch and starves every branch after it. The direction of a
    tile is decided by the tile that follows it in the trunk, and only the
    last tile of the whole line points at an arm.

``a branch takes material off the line, it does not restart it``
    the arm serving a target picks up from a tile the trunk covers and drops
    into the target.

``a target out of budget is refused by name``
    and the trunk built for the reachable targets stays.
"""

from __future__ import annotations

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.delivery import (
    MODE_BELT,
    MODE_INSERTER,
    MODE_REFUSED,
    REFUSAL_NO_SOURCE_TILE,
    REFUSAL_OVER_BUDGET,
    plan_delivery,
    plan_trunk,
)


def _tile(x: int, y: int) -> frozenset[GridPoint]:
    return frozenset({GridPoint(x, y)})


def _seat(arm: object) -> GridPoint:
    position = arm.position  # type: ignore[attr-defined]
    return GridPoint(int(position[0] - 0.5), int(position[1] - 0.5))


#: A 1x1 output chest and five feed chests spread around it, all of them
#: further than one arm can span. This is the coal cell of the live world
#: reduced to its shape: one source, many targets, nothing in the way.
SOURCE = _tile(20, 20)
TARGETS = (
    _tile(20, 14),
    _tile(24, 14),
    _tile(16, 14),
    _tile(26, 18),
    _tile(14, 18),
)


def test_one_source_serves_every_target_the_sequential_planner_refuses() -> None:
    # The incumbent: each link is planned on its own and spends one side of
    # the source. Four sides, and the ones after them are refused against an
    # empty map.
    blocked: set[GridPoint] = set()
    sequential = 0
    for target in TARGETS:
        link = plan_delivery(
            source_tiles=SOURCE,
            target_tiles=target,
            blocked=blocked,
            belt_budget=40,
        )
        if not link.builds:
            continue
        sequential += 1
        blocked |= set(link.path)
        for arm in (link.lift, link.drop):
            if arm is not None:
                blocked.add(_seat(arm))

    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )
    served = [branch for branch in plan.branches if branch.builds]

    assert sequential < len(TARGETS), (
        "o cenario tem de reproduzir a recusa que o tronco existe para resolver"
    )
    assert len(served) == len(TARGETS)
    assert len(served) > sequential


def test_the_source_is_lifted_exactly_once() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )

    lifts = [branch.lift for branch in plan.branches if branch.lift is not None]
    assert len(lifts) == 1
    assert lifts[0].picks_from in SOURCE
    assert lifts[0].drops_at == plan.path[0]


def test_every_belt_points_at_the_tile_that_follows_it() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )
    steps = {step.tile: step.direction for step in plan.steps}
    offsets = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

    assert len(steps) == len(plan.path)
    for index, tile in enumerate(plan.path[:-1]):
        offset = offsets[steps[tile]]
        assert GridPoint(tile.x + offset[0], tile.y + offset[1]) == plan.path[index + 1]


def test_a_branch_picks_up_from_the_trunk_and_drops_into_its_target() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )
    laid = set(plan.path)

    for branch in plan.branches:
        if branch.mode != MODE_BELT:
            continue
        assert branch.drop is not None
        assert branch.drop.picks_from in laid
        assert branch.drop.drops_at in TARGETS[branch.target]


def test_the_trunk_is_one_continuous_line() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )

    assert len(set(plan.path)) == len(plan.path)
    for first, second in zip(plan.path, plan.path[1:], strict=False):
        assert abs(first.x - second.x) + abs(first.y - second.y) == 1


def test_the_belt_a_branch_adds_is_the_tail_of_the_trunk() -> None:
    # Each branch reports the segment it extended the trunk by, in flow
    # order, and the segments concatenate into the whole line. The script
    # builds one block per branch, so a segment that does not join the
    # previous one leaves a gap the material never crosses.
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )

    rebuilt: list[GridPoint] = []
    for branch in plan.branches:
        rebuilt.extend(step.tile for step in branch.steps)
    assert tuple(rebuilt) == plan.path


def test_nothing_is_laid_on_the_source_a_target_or_an_arm() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset(),
        belt_budget=60,
    )
    laid = set(plan.path)
    seats = {
        _seat(arm)
        for branch in plan.branches
        for arm in (branch.lift, branch.drop)
        if arm is not None
    }

    assert not laid & SOURCE
    assert not laid & seats
    assert not seats & SOURCE
    for target in TARGETS:
        assert not laid & target
        assert not seats & target


def test_a_target_the_budget_cannot_reach_is_refused_by_name() -> None:
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 14), _tile(20, 90)),
        blocked=frozenset(),
        belt_budget=14,
    )

    assert plan.branches[0].builds
    assert plan.branches[1].mode == MODE_REFUSED
    assert plan.branches[1].reason == REFUSAL_OVER_BUDGET
    assert plan.branches[1].steps == ()
    # The refused target costs the reachable one nothing.
    assert plan.path == tuple(step.tile for step in plan.branches[0].steps)


def test_a_source_nothing_can_stand_beside_is_refused_by_name() -> None:
    walled = frozenset(
        {
            GridPoint(19, 20),
            GridPoint(21, 20),
            GridPoint(20, 19),
            GridPoint(20, 21),
        }
    )
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 14),),
        blocked=walled,
        belt_budget=40,
    )

    assert plan.path == ()
    assert plan.branches[0].mode == MODE_REFUSED
    assert plan.branches[0].reason == REFUSAL_NO_SOURCE_TILE


def test_a_target_two_tiles_away_is_served_by_one_arm_and_no_belt() -> None:
    # The cheapest link there is: one inserter spanning both footprints. A
    # trunk that laid belt here would spend material on a link an arm closes.
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(22, 20),),
        blocked=frozenset(),
        belt_budget=40,
    )

    assert plan.branches[0].mode == MODE_INSERTER
    assert plan.branches[0].steps == ()
    assert plan.path == ()
    assert plan.branches[0].lift is not None
    assert plan.branches[0].lift.picks_from == GridPoint(20, 20)
    assert plan.branches[0].lift.drops_at == GridPoint(22, 20)


def test_the_same_world_answers_the_same_trunk() -> None:
    first = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset({GridPoint(18, y) for y in range(15, 20)}),
        belt_budget=60,
    )
    second = plan_trunk(
        source_tiles=SOURCE,
        targets=TARGETS,
        blocked=frozenset({GridPoint(18, y) for y in range(19, 14, -1)}),
        belt_budget=60,
    )

    assert first.path == second.path
    assert [branch.drop for branch in first.branches] == [
        branch.drop for branch in second.branches
    ]


def test_a_target_beside_the_trunk_costs_no_extra_belt() -> None:
    # Two targets on the same side of the line: the second one is an arm off
    # the belt the first one laid, not a second lane.
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 12), _tile(18, 15)),
        blocked=frozenset(),
        belt_budget=40,
    )

    assert all(branch.builds for branch in plan.branches)
    assert plan.branches[1].steps == ()
    assert plan.branches[1].drop is not None
    assert plan.branches[1].drop.picks_from in set(plan.path)


#: Tiles carrying ore. Free of entities, so they were chosen exactly like
#: bare dirt: the world read of 2026-09-23 found twenty of twenty-two burner
#: inserters standing on resource tiles, and every one of them is a tile no
#: drill will ever use.
def test_an_arm_prefers_bare_ground_to_a_tile_carrying_ore() -> None:
    # A furnace beside the line, and two seats that reach it off the same
    # trunk: one of them stands on ore and the other does not. Neither costs
    # a belt, so the ground is the whole difference between them.
    furnace = frozenset({GridPoint(18, 15), GridPoint(18, 16)})
    ore = frozenset({GridPoint(19, 15)})
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 12), furnace),
        blocked=frozenset(),
        belt_budget=40,
        costly=ore,
    )

    branch = plan.branches[1]
    assert branch.builds
    assert branch.steps == ()
    assert branch.drop is not None
    assert _seat(branch.drop) == GridPoint(19, 16)


def test_the_price_of_ore_does_not_buy_a_long_detour() -> None:
    # The same preference, stated from the other side: sparing one tile of
    # ore is not worth four more belts. A planner that always avoided ore
    # would spend the budget going around patches it is standing in.
    ore = frozenset({GridPoint(20, 15)})
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 14),),
        blocked=frozenset(),
        belt_budget=40,
        costly=ore,
    )

    assert plan.branches[0].builds
    assert plan.branches[0].belt_count <= 4


def test_a_lane_goes_around_ore_when_the_detour_is_short() -> None:
    # The straight lane crosses a patch; one tile to the side is bare. The
    # price is a preference and not a veto -- a lane whose only way through
    # is ore is still built -- so what is pinned is the choice, not a
    # refusal.
    ore = frozenset(GridPoint(20, y) for y in range(13, 19))
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 12),),
        blocked=frozenset(),
        belt_budget=40,
        costly=ore,
    )

    assert plan.branches[0].builds
    assert not set(plan.path) & ore


def test_a_lane_with_no_bare_way_through_is_still_built() -> None:
    # Every tile between the two carries ore. Refusing here would cost the
    # whole link over ground that is spent either way.
    ore = frozenset(
        GridPoint(x, y) for x in range(14, 27) for y in range(10, 21)
    )
    plan = plan_trunk(
        source_tiles=SOURCE,
        targets=(_tile(20, 12),),
        blocked=frozenset(),
        belt_budget=40,
        costly=ore,
    )

    assert plan.branches[0].builds
    assert plan.path
