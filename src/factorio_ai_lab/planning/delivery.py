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
) -> ArmPlacement | None:
    """The one inserter that spans both footprints, if such a tile exists."""
    for tile in _ordered(source):
        for step, name in _STEPS:
            stand = GridPoint(tile.x + step[0], tile.y + step[1])
            landing = GridPoint(stand.x + step[0], stand.y + step[1])
            if landing not in target:
                continue
            if stand in source or stand in target or stand in blocked:
                continue
            return _arm_on(stand, step, name)
    return None


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
) -> tuple[GridPoint, ...] | None:
    """Belt tiles from ``start`` to ``goal``, or None when there is no lane."""
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
    )
    if route is None:
        return None
    if len(route.path) > max(0, int(budget)):
        return None
    return tuple(route.path)


def plan_delivery(
    *,
    source_tiles: Collection[GridPoint],
    target_tiles: Collection[GridPoint],
    blocked: Collection[GridPoint],
    belt_budget: int = DEFAULT_BELT_BUDGET,
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

    arm = _single_arm(source, target, obstacles)
    if arm is not None:
        return DeliveryLink(mode=MODE_INSERTER, lift=arm)

    lifts = _arm_seats(source, target, obstacles, outward=True)
    if not lifts:
        return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_NO_SOURCE_TILE)
    drops = _arm_seats(target, source, obstacles, outward=False)
    if not drops:
        return DeliveryLink(mode=MODE_REFUSED, reason=REFUSAL_NO_TARGET_TILE)

    # Every arm tile is itself an obstacle to the lane, or the belt would be
    # routed through the inserter that feeds it.
    seats = frozenset(
        GridPoint(int(arm.position[0] - 0.5), int(arm.position[1] - 0.5))
        for arm, _ in (*lifts, *drops)
    )

    best: tuple[tuple[int, int, int, int, int], DeliveryLink] | None = None
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
            )
            if path is None:
                continue
            key = (
                len(path),
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
