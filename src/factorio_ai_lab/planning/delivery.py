"""Linking what a machine produced to the machine that consumes it.

Every stage of this curriculum ends the same way: a machine fills a chest,
and nothing takes the contents out. The world read of 2026-09-23 counted five
of nine drills at ``no_fuel``, seven of ten arms at
``waiting_for_source_items``, three of five furnaces at ``no_ingredients``
and one at ``full_output`` -- machines standing next to the material they are
waiting for. The coal drill mines into its own output chest and the boiler
starves.

A link is decided over tiles, the same way :mod:`factorio_ai_lab.planning.
placement` decides a footprint, and it is one of three named outcomes.

``one arm``
    an inserter occupies a single tile, picks up from the tile behind it and
    drops on the tile in front. Two footprints exactly two tiles apart on one
    axis, with the tile between them free, are joined by one inserter and
    nothing else. Two *adjacent* footprints cannot be joined at all: the arm
    would have to stand on one of them. That is not an edge case, it is the
    shape the curriculum keeps building -- ``place_entity_next_to`` puts the
    output chest flush against the drill -- and it is why a coal cell cannot
    be closed by dropping one more inserter beside it.

``an arm, a lane, an arm``
    otherwise the material is lifted onto a belt, routed around what is
    standing, and lifted off into the target. The lane has a budget, because
    a link measured in hundreds of belts is a different decision from one
    measured in ten, and the caller is the one entitled to make it.

``a refusal``
    no free tile beside the target, or no lane inside the budget. Stated by
    name and built not at all: a lane that stops short pours coal onto open
    ground, which costs the material and the belts both.

One source and many targets is a fourth shape, and planning it as many links
does not work: a 1x1 chest has four sides, each link spends one of them on
its own lifting arm, and the world read of 2026-09-23 refused thirteen of
fifteen feed chests with ``no_free_tile_beside_the_source`` on a map with
room to spare. :func:`plan_trunk` answers that one: one arm lifts out of the
source, one belt line runs past the targets, and each target gets an arm that
takes material off the line. The line is extended from its own tail, never
branched, because a belt carries in one direction and a tile pointing at the
arm beside it stops the material at the first branch.

Positions are planned here and placed with ``place_entity`` at an explicit
position, never with ``place_entity_next_to``. That call scores the sides it
was not asked for and snaps an even-sided entity to the tile boundary, and
deriving a pickup tile from the side that was *requested* is what once left
the fuel feed dropping coal onto open ground with three AST tests asserting
the wrong contract.

The scan order is fixed -- north, south, west, east, then by tile -- so the
same world always answers the same link. A link that cannot be replayed from
the run seed cannot be replayed at all.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import weighted_astar

#: How the material gets across, as written into the journal.
MODE_INSERTER = "inserter"
MODE_BELT = "belt"
MODE_REFUSED = "refused"

#: Why a link was not built. One string per cause: a target nothing can stand
#: beside and a target merely too far away lead to different next actions.
REFUSAL_NO_LANE = "no_free_lane_between_them"
REFUSAL_OVER_BUDGET = "lane_longer_than_the_belt_budget"
REFUSAL_NO_SOURCE_TILE = "no_free_tile_beside_the_source"
REFUSAL_NO_TARGET_TILE = "no_free_tile_beside_the_target"

#: Belts a link may spend before it is refused instead. Twelve is the lane
#: that reaches around a 2x2 drill from its output chest to a feed chest on
#: the far side with room to spare; a caller that wants a trunk line passes
#: its own budget rather than editing this one.
DEFAULT_BELT_BUDGET = 12

#: What one tile carrying ore costs a lane, in belts. A preference and
#: never a veto, on the terms ``planning.placement`` already states: a tile
#: with ore under it is free of entities and was therefore chosen exactly
#: like bare dirt, and the world read of 2026-09-23 found twenty of
#: twenty-two burner inserters standing on resource tiles. Priced at more
#: than one belt so a one-tile detour is worth taking, and low enough that a
#: lane whose only way through is ore is still built rather than refused.
RESOURCE_TILE_COST = 1.6

#: Belts a whole trunk may spend, over every target it serves. Larger than
#: one link's budget and deliberately not unbounded: the belt is carried in
#: the agent's inventory and every tile of it is walked to, so a trunk that
#: crosses the map costs the generation the time it was going to smelt in.
DEFAULT_TRUNK_BELT_BUDGET = 48

#: Unit steps, and the FLE ``Direction`` name for each. Ordered north, south,
#: west, east and used as the tie-break everywhere in this module, so the
#: answer never depends on the iteration order of a set.
_STEPS: tuple[tuple[tuple[int, int], str], ...] = (
    ((0, -1), "UP"),
    ((0, 1), "DOWN"),
    ((-1, 0), "LEFT"),
    ((1, 0), "RIGHT"),
)

#: How far the A* may wander outside the box spanned by the two endpoints.
#: Bounded on purpose: an unbounded grid lets one blocked lane expand tens of
#: thousands of nodes before it admits there is no way through.
_LANE_MARGIN = 12


@dataclass(frozen=True)
class ArmPlacement:
    """One inserter: where it stands, and the two tiles it spans.

    ``direction`` is the way the arm moves items, which is the direction FLE
    rotates it to and the tile it drops on. The pickup tile is the opposite
    neighbour. Both are recorded rather than recomputed by the caller,
    because a caller that derives the drop tile from the side it asked for is
    the defect this module exists to avoid.
    """

    position: tuple[float, float]
    direction: str
    picks_from: GridPoint
    drops_at: GridPoint


@dataclass(frozen=True)
class DeliveryLink:
    """How one source reaches one target, and the evidence behind it."""

    mode: str
    lift: ArmPlacement | None = None
    drop: ArmPlacement | None = None
    path: tuple[GridPoint, ...] = ()
    reason: str | None = None

    @property
    def builds(self) -> bool:
        return self.mode in {MODE_INSERTER, MODE_BELT}

    @property
    def belt_count(self) -> int:
        return len(self.path)

    def to_dict(self) -> dict[str, object]:
        """The link as the journal records it."""
        return {
            "mode": self.mode,
            "reason": self.reason,
            "belt_count": self.belt_count,
            "lift": None if self.lift is None else _arm_dict(self.lift),
            "drop": None if self.drop is None else _arm_dict(self.drop),
            "path": [{"x": tile.x, "y": tile.y} for tile in self.path],
        }


def _arm_dict(arm: ArmPlacement) -> dict[str, object]:
    return {
        "position": {"x": arm.position[0], "y": arm.position[1]},
        "direction": arm.direction,
        "picks_from": {"x": arm.picks_from.x, "y": arm.picks_from.y},
        "drops_at": {"x": arm.drops_at.x, "y": arm.drops_at.y},
    }


def tile_centre(tile: GridPoint) -> tuple[float, float]:
    """Centre of a one-tile entity standing on ``tile``."""
    return (tile.x + 0.5, tile.y + 0.5)


def _arm_on(tile: GridPoint, step: tuple[int, int], name: str) -> ArmPlacement:
    return ArmPlacement(
        position=tile_centre(tile),
        direction=name,
        picks_from=GridPoint(tile.x - step[0], tile.y - step[1]),
        drops_at=GridPoint(tile.x + step[0], tile.y + step[1]),
    )


def _ordered(tiles: Collection[GridPoint]) -> tuple[GridPoint, ...]:
    return tuple(sorted(tiles, key=lambda tile: (tile.y, tile.x)))


def _single_arm(
    source: frozenset[GridPoint],
    target: frozenset[GridPoint],
    blocked: frozenset[GridPoint],
    costly: frozenset[GridPoint] = frozenset(),
) -> ArmPlacement | None:
    """The one inserter that spans both footprints, if such a tile exists.

    Bare ground first and ore last, on the same terms as every other
    placement: the tile an arm takes out of a patch is a tile no drill will
    ever use, and when two tiles both span the footprints the choice between
    them is free.
    """
    best: tuple[tuple[int, int, int], ArmPlacement] | None = None
    for tile in _ordered(source):
        for step, name in _STEPS:
            stand = GridPoint(tile.x + step[0], tile.y + step[1])
            landing = GridPoint(stand.x + step[0], stand.y + step[1])
            if landing not in target:
                continue
            if stand in source or stand in target or stand in blocked:
                continue
            key = (1 if stand in costly else 0, stand.y, stand.x)
            if best is None or key < best[0]:
                best = (key, _arm_on(stand, step, name))
    return None if best is None else best[1]


def _arm_seats(
    footprint: frozenset[GridPoint],
    other: frozenset[GridPoint],
    blocked: frozenset[GridPoint],
    *,
    outward: bool,
) -> tuple[tuple[ArmPlacement, GridPoint], ...]:
    """Every free tile an arm could stand on beside ``footprint``.

    ``outward`` picks which way the arm faces: an arm lifting *out of* the
    source drops on the far tile, an arm dropping *into* the target picks up
    from it. The far tile is the one the belt lane has to reach, and it is
    returned beside the arm so the caller never recomputes it.
    """
    seats: list[tuple[ArmPlacement, GridPoint]] = []
    for tile in _ordered(footprint):
        for step, name in _STEPS:
            stand = GridPoint(tile.x + step[0], tile.y + step[1])
            far = GridPoint(stand.x + step[0], stand.y + step[1])
            if stand in footprint or stand in other or stand in blocked:
                continue
            if far in footprint or far in other or far in blocked:
                continue
            if outward:
                arm = _arm_on(stand, step, name)
            else:
                back = (-step[0], -step[1])
                name_back = next(
                    label for offset, label in _STEPS if offset == back
                )
                arm = _arm_on(stand, back, name_back)
            seats.append((arm, far))
    return tuple(seats)


def _lane(
    start: GridPoint,
    goal: GridPoint,
    blocked: frozenset[GridPoint],
    *,
    budget: int,
    costly: frozenset[GridPoint] = frozenset(),
) -> tuple[GridPoint, ...] | None:
    """Belt tiles from ``start`` to ``goal``, or None when there is no lane."""
    route = _route(start, goal, blocked, costly=costly)
    if route is None:
        return None
    if len(route) > max(0, int(budget)):
        return None
    return route


def _route(
    start: GridPoint,
    goal: GridPoint,
    blocked: frozenset[GridPoint],
    *,
    costly: frozenset[GridPoint] = frozenset(),
) -> tuple[GridPoint, ...] | None:
    """The tiles a lane would cover, unpriced, or None when there is no way.

    Kept apart from the budget check so a caller can tell the two refusals
    apart: a lane that does not exist and a lane that costs more belt than
    the caller will spend lead to different next actions, and a target told
    "no lane" would have a budget widened that was never the problem.
    """
    left = min(start.x, goal.x) - _LANE_MARGIN
    right = max(start.x, goal.x) + _LANE_MARGIN
    top = min(start.y, goal.y) - _LANE_MARGIN
    bottom = max(start.y, goal.y) + _LANE_MARGIN

    def in_bounds(tile: GridPoint) -> bool:
        return left <= tile.x <= right and top <= tile.y <= bottom

    route = weighted_astar(
        start,
        goal,
        is_blocked=lambda tile: tile in blocked,
        in_bounds=in_bounds,
        extra_cost=(
            None
            if not costly
            else lambda tile: RESOURCE_TILE_COST if tile in costly else 0.0
        ),
    )
    if route is None:
        return None
    return tuple(route.path)


def plan_delivery(
    *,
    source_tiles: Collection[GridPoint],
    target_tiles: Collection[GridPoint],
    blocked: Collection[GridPoint],
    belt_budget: int = DEFAULT_BELT_BUDGET,
    costly: Collection[GridPoint] = (),
) -> DeliveryLink:
    """Decide how material leaves ``source_tiles`` and enters ``target_tiles``.

    ``blocked`` is every tile something is standing on, the two footprints
    included or not -- they are excluded here either way, because a link may
    not place anything on top of the two things it is joining.

    One arm is preferred over a lane: it costs one entity, it cannot be
    routed through anything, and it never drops material on the ground. When
    no single tile spans both, the lane is planned from the tile the first
    arm drops on to the tile the second arm picks up from, so the belt ends
    exactly where an arm is waiting rather than one tile short.
    """
    source = frozenset(source_tiles)
    target = frozenset(target_tiles)
    if not source or not target:
        return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_NO_LANE)

    obstacles = frozenset(blocked) - source - target
    ground = frozenset(costly)

    arm = _single_arm(source, target, obstacles, ground)
    if arm is not None:
        return DeliveryLink(mode=MODE_INSERTER, lift=arm)

    lifts = _arm_seats(source, target, obstacles, outward=True)
    if not lifts:
        return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_NO_SOURCE_TILE)
    drops = _arm_seats(target, source, obstacles, outward=False)
    if not drops:
        return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_NO_TARGET_TILE)

    # Every arm tile is itself an obstacle to the lane, or the belt would be
    # routed through the inserter that feeds it. So are the two footprints:
    # they are excluded from ``obstacles`` because an arm is decided against
    # them, and a lane laid over either of them is a belt the engine refuses
    # with ``entity already exists at the target position``.
    seats = frozenset(
        GridPoint(int(arm.position[0] - 0.5), int(arm.position[1] - 0.5))
        for arm, _ in (*lifts, *drops)
    ) | source | target

    best: tuple[tuple[float, ...], DeliveryLink] | None = None
    for lift, lane_start in lifts:
        lift_seat = GridPoint(
            int(lift.position[0] - 0.5), int(lift.position[1] - 0.5)
        )
        for drop, lane_goal in drops:
            drop_seat = GridPoint(
                int(drop.position[0] - 0.5), int(drop.position[1] - 0.5)
            )
            lane_blocked = (obstacles | seats) - {lane_start, lane_goal}
            path = _lane(
                lane_start,
                lane_goal,
                lane_blocked,
                budget=belt_budget,
                costly=ground,
            )
            if path is None:
                continue
            # Belts and ore in one number: a tile of ore costs what
            # ``RESOURCE_TILE_COST`` says a tile of ore costs, so a short
            # detour around a patch wins and a long one does not.
            key = (
                len(path)
                + RESOURCE_TILE_COST
                * _ground_price((*path, lift_seat, drop_seat), ground),
                lift_seat.y,
                lift_seat.x,
                drop_seat.y,
                drop_seat.x,
            )
            if best is None or key < best[0]:
                best = (
                    key,
                    DeliveryLink(
                        mode=MODE_BELT,
                        lift=lift,
                        drop=drop,
                        path=path,
                    ),
                )
    if best is not None:
        return best[1]
    return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_OVER_BUDGET)


@dataclass(frozen=True)
class BeltStep:
    """One belt tile of a trunk, and the way it carries.

    The direction belongs to the finished line and not to the branch that
    laid the tile: a tile whose branch pointed it at the arm beside it stops
    the material there, and every branch after it starves.
    """

    tile: GridPoint
    direction: str

    def to_dict(self) -> dict[str, object]:
        return {"x": self.tile.x, "y": self.tile.y, "direction": self.direction}


@dataclass(frozen=True)
class TrunkBranch:
    """One target of a trunk: how it is served, and what it cost.

    ``steps`` is the belt this branch added to the line, in flow order, and
    it is empty for a target an arm reaches off the belt another branch
    already laid. ``lift`` is set on the one branch that started the trunk,
    and on a branch an arm spans straight out of the source.
    """

    target: int
    mode: str
    lift: ArmPlacement | None = None
    drop: ArmPlacement | None = None
    steps: tuple[BeltStep, ...] = ()
    reason: str | None = None

    @property
    def builds(self) -> bool:
        return self.mode in {MODE_INSERTER, MODE_BELT}

    @property
    def belt_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, object]:
        """The branch as the journal records it."""
        return {
            "target": self.target,
            "mode": self.mode,
            "reason": self.reason,
            "belt_count": self.belt_count,
            "lift": None if self.lift is None else _arm_dict(self.lift),
            "drop": None if self.drop is None else _arm_dict(self.drop),
            "path": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class TrunkPlan:
    """One line out of one source, and every target hanging off it."""

    branches: tuple[TrunkBranch, ...] = ()
    steps: tuple[BeltStep, ...] = ()

    @property
    def path(self) -> tuple[GridPoint, ...]:
        """The whole line, in flow order."""
        return tuple(step.tile for step in self.steps)

    @property
    def belt_count(self) -> int:
        return len(self.steps)

    @property
    def served(self) -> int:
        return sum(1 for branch in self.branches if branch.builds)

    def to_dict(self) -> dict[str, object]:
        return {
            "belt_count": self.belt_count,
            "served": self.served,
            "branches": [branch.to_dict() for branch in self.branches],
        }


def _seat_of(arm: ArmPlacement) -> GridPoint:
    """The tile an arm stands on, from the centre it was planned at."""
    return GridPoint(int(arm.position[0] - 0.5), int(arm.position[1] - 0.5))


def _ground_price(
    tiles: Collection[GridPoint],
    costly: Collection[GridPoint],
) -> int:
    """How many of these tiles carry ore. Counted, never inferred."""
    if not costly:
        return 0
    return sum(1 for tile in tiles if tile in costly)


def _direction_between(start: GridPoint, end: GridPoint) -> str | None:
    """The FLE ``Direction`` name carrying from one tile to its neighbour."""
    delta = (end.x - start.x, end.y - start.y)
    for step, name in _STEPS:
        if step == delta:
            return name
    return None


def _branch_arm(
    target: frozenset[GridPoint],
    laid: frozenset[GridPoint],
    blocked: frozenset[GridPoint],
    costly: frozenset[GridPoint] = frozenset(),
) -> ArmPlacement | None:
    """The arm that takes material off a trunk tile into ``target``.

    Cheaper than any lane there is -- one inserter, no belt -- and the whole
    reason a trunk serves more targets than the same budget spent on
    separate links. The arm stands beside the target and picks up from a
    tile the line already covers; a seat on the line itself would put the
    inserter on top of the belt feeding it.
    """
    best: tuple[tuple[int, int, int], ArmPlacement] | None = None
    for tile in _ordered(target):
        for step, _name in _STEPS:
            stand = GridPoint(tile.x + step[0], tile.y + step[1])
            far = GridPoint(stand.x + step[0], stand.y + step[1])
            if stand in target or stand in blocked or stand in laid:
                continue
            if far not in laid:
                continue
            back = (-step[0], -step[1])
            name_back = next(label for offset, label in _STEPS if offset == back)
            key = (1 if stand in costly else 0, stand.y, stand.x)
            if best is None or key < best[0]:
                best = (key, _arm_on(stand, back, name_back))
    return None if best is None else best[1]


def _extension(
    tail: GridPoint,
    target: frozenset[GridPoint],
    source: frozenset[GridPoint],
    blocked: frozenset[GridPoint],
    *,
    laid: frozenset[GridPoint],
    budget: int,
    costly: frozenset[GridPoint] = frozenset(),
) -> tuple[ArmPlacement, tuple[GridPoint, ...]] | str:
    """Carry the line from its tail to an arm beside ``target``.

    Extended from the tail and never from the middle: a belt carries one
    way, so a tile in the middle pointed at a new segment would stop serving
    the segment it already carries. Answers the refusal by name when it
    cannot, because "no lane" and "the lane costs more than the budget" are
    different readings of the same world.
    """
    drops = _arm_seats(target, source, blocked, outward=False)
    if not drops:
        return REFUSAL_NO_TARGET_TILE
    seats = frozenset(_seat_of(arm) for arm, _ in drops)
    best: tuple[tuple[float, ...], ArmPlacement, tuple[GridPoint, ...]] | None = None
    reachable = False
    for drop, goal in drops:
        lane_blocked = (blocked | seats | laid) - {tail, goal}
        route = _route(tail, goal, lane_blocked, costly=costly)
        if route is None or not route or route[0] != tail:
            continue
        reachable = True
        segment = route[1:]
        if len(laid) + len(segment) > max(0, int(budget)):
            continue
        seat = _seat_of(drop)
        key = (
            len(segment) + RESOURCE_TILE_COST * _ground_price((*segment, seat), costly),
            seat.y,
            seat.x,
        )
        if best is None or key < best[0]:
            best = (key, drop, segment)
    if best is not None:
        return (best[1], best[2])
    return REFUSAL_OVER_BUDGET if reachable else REFUSAL_NO_LANE


def plan_trunk(
    *,
    source_tiles: Collection[GridPoint],
    targets: Collection[Collection[GridPoint]],
    blocked: Collection[GridPoint],
    belt_budget: int = DEFAULT_TRUNK_BELT_BUDGET,
    costly: Collection[GridPoint] = (),
) -> TrunkPlan:
    """One line out of ``source_tiles``, serving as many targets as it can.

    Targets are served in the order they arrive, and the order is the
    caller's decision: the line grows from its own tail, so a caller that
    hands over its furthest target first pays for the walk back. Each target
    is answered in the cheapest way still available -- one arm spanning
    source and target, an arm off the line already laid, then an extension
    of the line -- and a target none of the three reaches is refused by name
    while the trunk built for the others stays.

    ``blocked`` is every tile something stands on. The source and every
    target join it whether the caller listed them or not: an arm is decided
    against those footprints by the tiles they cover, but no belt and no
    inserter may be placed on top of one, and a lane routed across the chest
    it draws from is a belt the engine refuses.
    """
    source = frozenset(source_tiles)
    wanted = tuple(frozenset(target) for target in targets)
    if not source or not wanted:
        return TrunkPlan()
    ground = frozenset(costly)

    occupied = set(blocked) | source
    for target in wanted:
        occupied |= target

    laid: list[GridPoint] = []
    lifted = False
    branches: list[TrunkBranch] = []
    spans: list[tuple[int, int]] = []

    for index, target in enumerate(wanted):
        if not target:
            branches.append(
                TrunkBranch(target=index, mode=MODE_REFUSED, reason=REFUSAL_NO_LANE)
            )
            spans.append((0, 0))
            continue
        free = frozenset(occupied)

        arm = _single_arm(source, target, free, ground)
        if arm is not None:
            branches.append(
                TrunkBranch(target=index, mode=MODE_INSERTER, lift=arm)
            )
            spans.append((len(laid), 0))
            occupied.add(_seat_of(arm))
            continue

        if laid:
            branch_arm = _branch_arm(target, frozenset(laid), free, ground)
            if branch_arm is not None:
                branches.append(
                    TrunkBranch(target=index, mode=MODE_BELT, drop=branch_arm)
                )
                spans.append((len(laid), 0))
                occupied.add(_seat_of(branch_arm))
                continue

        if not lifted:
            link = plan_delivery(
                source_tiles=source,
                target_tiles=target,
                blocked=free,
                belt_budget=belt_budget,
                costly=ground,
            )
            if not link.builds:
                branches.append(
                    TrunkBranch(
                        target=index, mode=MODE_REFUSED, reason=link.reason
                    )
                )
                spans.append((len(laid), 0))
                continue
            branches.append(
                TrunkBranch(
                    target=index,
                    mode=MODE_BELT,
                    lift=link.lift,
                    drop=link.drop,
                )
            )
            spans.append((0, len(link.path)))
            laid.extend(link.path)
            occupied |= set(link.path)
            for placed in (link.lift, link.drop):
                if placed is not None:
                    occupied.add(_seat_of(placed))
            lifted = True
            continue

        outcome = _extension(
            laid[-1],
            target,
            source,
            free,
            laid=frozenset(laid),
            budget=belt_budget,
            costly=ground,
        )
        if isinstance(outcome, str):
            branches.append(
                TrunkBranch(target=index, mode=MODE_REFUSED, reason=outcome)
            )
            spans.append((len(laid), 0))
            continue
        drop, segment = outcome
        spans.append((len(laid), len(segment)))
        branches.append(TrunkBranch(target=index, mode=MODE_BELT, drop=drop))
        laid.extend(segment)
        occupied |= set(segment)
        occupied.add(_seat_of(drop))

    steps = _flow(tuple(laid), branches)
    placed = tuple(
        TrunkBranch(
            target=branch.target,
            mode=branch.mode,
            lift=branch.lift,
            drop=branch.drop,
            steps=steps[start : start + length],
            reason=branch.reason,
        )
        for branch, (start, length) in zip(branches, spans, strict=True)
    )
    return TrunkPlan(branches=placed, steps=steps)


def _flow(
    path: tuple[GridPoint, ...],
    branches: Collection[TrunkBranch],
) -> tuple[BeltStep, ...]:
    """Point every tile of the finished line at the tile that follows it.

    The last tile has no follower, so it points at the arm that picks up
    from it; material reaching the end of a belt stops there either way, and
    a tile aimed at the arm is the shape the engine reads as a feed.
    """
    if not path:
        return ()
    tail_seat: GridPoint | None = None
    for branch in branches:
        arm = branch.drop
        if arm is not None and arm.picks_from == path[-1]:
            tail_seat = _seat_of(arm)
            break
    steps: list[BeltStep] = []
    for index, tile in enumerate(path):
        if index + 1 < len(path):
            direction = _direction_between(tile, path[index + 1])
        else:
            direction = (
                None if tail_seat is None else _direction_between(tile, tail_seat)
            )
        steps.append(BeltStep(tile=tile, direction=direction or _STEPS[3][1]))
    return tuple(steps)
