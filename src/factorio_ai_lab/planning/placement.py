"""Where a stage may build when the world already holds a factory.

Lifelong inheritance restores the promoted factory before the curriculum
runs, and every anchor the curriculum computes is deterministic on a fixed
map: ``patch_center`` answers (27.0, 83.0) for this iron patch, and the
placement arms are fixed offsets around it. An heir therefore aims at the
tiles its ancestor already built on, ``place_entity`` answers ``entity
already exists at the target position``, and FLE marks the step failed on
that text. Generation 37 was the first promotion since generation 6, and the
loop restarted into that failure 98 times.

The decision is made over tiles, never over a radius or a comparison of
centres. Footprints come from :mod:`factorio_ai_lab.planning.footprints`,
which resolves the real prototype footprint: a 2x2 drill one tile off shares
half the anchor footprint and still blocks the placement, while a guessed
radius over-blocks small entities and leaves large ones open. Checked against
the live engine on ten positions of the inherited world, the tile decision
and ``surface.can_place_entity`` agree on all ten.

A plan is one of three named outcomes, and all three are results:

``adopt``
    something that already serves the anchor is standing on it. The caller
    measures it apart from what this generation built: an inherited drill is
    the ancestor's achievement, and crediting its flow as production is the
    failure ``InheritedCapabilities`` exists to prevent.
``build``
    the anchor footprint is free, or a scan found free tiles near it.
    ``shift`` says how far from the anchor the plan moved, in tiles.
``refuse``
    nothing within reach is free. A declared refusal is a reading about the
    world; building on top and letting the engine answer is not.

The scan is ordered by ring, then by Manhattan distance, then by ``(dx, dy)``
and never by the iteration order of a set or a dict, so the same anchor in
the same world always answers the same tile. A placement that cannot be
replayed from the run seed cannot be replayed at all.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.footprints import (
    blocked_tiles,
    entity_footprint,
    entity_name,
    entity_tiles,
)

#: The three outcomes, as written into the journal.
OUTCOME_ADOPT = "adopt"
OUTCOME_BUILD = "build"
OUTCOME_REFUSE = "refuse"

#: Why the plan answered what it answered. Readable on purpose: a refusal
#: that cannot be read is indistinguishable from a stage that did nothing.
REASON_ANCHOR_FREE = "anchor_tiles_free"
REASON_SHIFTED = "shifted_to_free_tiles"
REASON_ALREADY_STANDING = "equivalent_entity_already_standing"
REASON_TILES_TAKEN = "no_free_tiles_within_reach"
REASON_FOOTPRINT_UNRESOLVED = "footprint_unresolved"

#: The world could not be surveyed, so the caller is building blind. Kept
#: distinct from a measured decision: a plan taken without reading the world
#: is not evidence that the tiles were free.
REASON_WORLD_UNREAD = "world_not_surveyed"


@dataclass(frozen=True)
class WorldSurvey:
    """The world as one RCON read reported it, with resolved footprints.

    Carried as one value because the two halves have to come from the same
    read: footprints resolved against a later world would describe entities
    that are no longer standing.
    """

    entities: tuple[Mapping[str, Any], ...] = ()
    footprints: Mapping[str, tuple[int, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementPlan:
    """What a stage should do at one anchor, and the evidence behind it."""

    outcome: str
    position: tuple[float, float] | None = None
    reason: str | None = None
    shift: tuple[int, int] | None = None
    adopted_name: str | None = None
    tiles: tuple[GridPoint, ...] = ()
    scanned: int = 0

    @property
    def adopts(self) -> bool:
        return self.outcome == OUTCOME_ADOPT

    @property
    def builds(self) -> bool:
        return self.outcome == OUTCOME_BUILD

    def to_dict(self) -> dict[str, Any]:
        """The plan as the journal records it."""
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "position": (
                None
                if self.position is None
                else {"x": self.position[0], "y": self.position[1]}
            ),
            "shift": (
                None
                if self.shift is None
                else {"x": self.shift[0], "y": self.shift[1]}
            ),
            "adopted_name": self.adopted_name,
            "tiles_scanned": self.scanned,
        }


def _snap_axis(value: float, size: int) -> float:
    if size % 2 == 0:
        return float(math.floor(value + 0.5))
    return float(math.floor(value)) + 0.5


def snap_to_grid(
    position: tuple[float, float],
    footprint: tuple[int, int],
) -> tuple[float, float]:
    """The centre the engine will actually build on.

    Factorio anchors an even-sided entity on a tile boundary and an odd-sided
    one on a tile centre, and ``create_entity`` snaps whatever position it is
    handed. Deciding over the unsnapped centre would reserve one set of tiles
    and let the engine occupy another: the 2x2 drills this curriculum aims at
    x=22.5 and x=31.5 stand at x=23 and x=32 in the live world.
    """
    return (
        _snap_axis(position[0], footprint[0]),
        _snap_axis(position[1], footprint[1]),
    )


def scan_offsets(reach: int) -> tuple[tuple[int, int], ...]:
    """Tile offsets to try, nearest first, in a fixed order.

    Sorted by ring, then by Manhattan distance, then by ``dx`` and ``dy``.
    Every key is a number read off the offset itself, so the order does not
    depend on how the world happened to be enumerated.
    """
    reach = max(0, int(reach))
    offsets = [
        (dx, dy)
        for dx in range(-reach, reach + 1)
        for dy in range(-reach, reach + 1)
    ]
    offsets.sort(
        key=lambda offset: (
            max(abs(offset[0]), abs(offset[1])),
            abs(offset[0]) + abs(offset[1]),
            offset[0],
            offset[1],
        )
    )
    return tuple(offsets)


def entity_position(entity: Mapping[str, Any]) -> tuple[float, float] | None:
    """Centre of one entity as a pair of floats, or None when unreadable."""
    position = entity.get("position")
    if not isinstance(position, Mapping):
        return None
    try:
        return (float(position["x"]), float(position["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _ordered(tiles: Iterable[GridPoint]) -> tuple[GridPoint, ...]:
    return tuple(sorted(tiles, key=lambda tile: (tile.y, tile.x)))


def _inside(tiles: Iterable[GridPoint], region: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = region
    return all(
        tile.x >= left
        and tile.x + 1 <= right
        and tile.y >= top
        and tile.y + 1 <= bottom
        for tile in tiles
    )


def footprint_tiles(
    *,
    entity: str,
    position: tuple[float, float],
    footprints: Mapping[str, tuple[int, int]] | None = None,
    direction: Any = 0,
) -> set[GridPoint]:
    """Tiles ``entity`` would occupy if it were built at ``position``."""
    return entity_tiles(
        {
            "name": entity,
            "position": {"x": position[0], "y": position[1]},
            "direction": direction,
        },
        footprints,
    )


def plan_placement(
    *,
    entity: str,
    anchor: tuple[float, float],
    world: Iterable[Mapping[str, Any]],
    footprints: Mapping[str, tuple[int, int]] | None = None,
    direction: Any = 0,
    adopt_names: Collection[str] | None = None,
    reach: int = 0,
    region: tuple[float, float, float, float] | None = None,
    extra_tiles: Callable[[frozenset[GridPoint]], Iterable[GridPoint]] | None = None,
) -> PlacementPlan:
    """Decide between adopting, building and refusing at one anchor.

    ``adopt_names`` lists the prototypes that count as already serving this
    anchor; it defaults to ``entity`` itself. ``reach`` is how far, in tiles,
    the plan may move away from the anchor to find free tiles. ``region``
    bounds that search in world coordinates -- a mining drill shifted off its
    resource patch places and mines nothing -- and is ignored when the anchor
    already lies outside it, because a stage aiming outside the patch is a
    decision of the stage, not something this layer may silently overrule.

    ``extra_tiles`` names the tiles the placement needs besides the entity's
    own footprint, given that footprint: a mining cell is a drill plus the
    chest it drops into, and tiles free for the drill alone can leave the
    chest on top of a belt, which fails the whole placement. Those tiles must
    be free, and they are not held to ``region``, which is about the resource
    under the entity.
    """
    standing = [candidate for candidate in world if isinstance(candidate, Mapping)]
    template = {
        "name": entity,
        "position": {"x": anchor[0], "y": anchor[1]},
        "direction": direction,
    }
    footprint = entity_footprint(template, footprints)
    origin = snap_to_grid(anchor, footprint)
    anchor_tiles = footprint_tiles(
        entity=entity,
        position=origin,
        footprints=footprints,
        direction=direction,
    )
    if not anchor_tiles:
        return PlacementPlan(
            outcome=OUTCOME_REFUSE,
            reason=REASON_FOOTPRINT_UNRESOLVED,
        )

    wanted = frozenset(adopt_names) if adopt_names is not None else frozenset({entity})
    adoptable: list[tuple[int, float, float, str, tuple[GridPoint, ...]]] = []
    for candidate in standing:
        name = entity_name(candidate)
        if name not in wanted:
            continue
        tiles = entity_tiles(candidate, footprints)
        overlap = tiles & anchor_tiles
        position = entity_position(candidate)
        if not overlap or position is None:
            continue
        adoptable.append((-len(overlap), position[0], position[1], name, _ordered(tiles)))
    if adoptable:
        _, x, y, name, tiles = min(adoptable)
        return PlacementPlan(
            outcome=OUTCOME_ADOPT,
            position=(x, y),
            reason=REASON_ALREADY_STANDING,
            adopted_name=name,
            tiles=tiles,
        )

    taken = blocked_tiles(standing, footprints)
    gate = region if region is not None and _inside(anchor_tiles, region) else None
    offsets = scan_offsets(reach)
    for scanned, (dx, dy) in enumerate(offsets, start=1):
        position = (origin[0] + dx, origin[1] + dy)
        tiles = footprint_tiles(
            entity=entity,
            position=position,
            footprints=footprints,
            direction=direction,
        )
        if gate is not None and not _inside(tiles, gate):
            continue
        needed = set(tiles)
        if extra_tiles is not None:
            needed |= set(extra_tiles(frozenset(tiles)))
        if needed & taken:
            continue
        return PlacementPlan(
            outcome=OUTCOME_BUILD,
            position=position,
            reason=REASON_ANCHOR_FREE if (dx, dy) == (0, 0) else REASON_SHIFTED,
            shift=(dx, dy),
            tiles=_ordered(tiles),
            scanned=scanned,
        )
    return PlacementPlan(
        outcome=OUTCOME_REFUSE,
        reason=REASON_TILES_TAKEN,
        scanned=len(offsets),
    )


def plan_cell_placement(
    survey: WorldSurvey | None,
    anchor: tuple[float, float],
    *,
    entity: str,
    direction: Any = 0,
    adopt_names: Collection[str] | None = None,
    reach: int = 0,
    region: tuple[float, float, float, float] | None = None,
    extra_tiles: Callable[[frozenset[GridPoint]], Iterable[GridPoint]] | None = None,
) -> PlacementPlan:
    """Plan against a survey, or build blind when there is no survey.

    A world that could not be read answers a plan to build on the anchor with
    ``world_not_surveyed`` recorded: that is the construction path the stage
    took before inheritance existed, and refusing every placement over a
    transient RCON failure would stop a generation that could have run.
    """
    if survey is None:
        return PlacementPlan(
            outcome=OUTCOME_BUILD,
            position=anchor,
            reason=REASON_WORLD_UNREAD,
            shift=(0, 0),
        )
    return plan_placement(
        entity=entity,
        anchor=anchor,
        world=survey.entities,
        footprints=survey.footprints,
        direction=direction,
        adopt_names=adopt_names,
        reach=reach,
        region=region,
        extra_tiles=extra_tiles,
    )
