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

Ground is not uniform. A tile carrying ore is free of entities, so it was
chosen exactly like bare dirt: the world read of 2026-09-23 found nine burner
inserters standing on resource tiles across three patches, and every one of
them is a tile no drill will ever use. A candidate is now priced by the ground
it takes -- measured bare ground first, then ground the survey never covered,
then resource tiles, the scarcest last -- and the plan records how many
resource tiles it spent. The price is a preference and never a veto: a stage
whose only reachable tiles carry ore builds on ore and says so, because
refusing there costs the whole stage. A drill is charged nothing, since a
drill off the ore mines nothing.

The scan is ordered by ring, then by Manhattan distance, then by ``(dx, dy)``
and never by the iteration order of a set or a dict, so the same anchor in
the same world always answers the same tile. A placement that cannot be
replayed from the run seed cannot be replayed at all.
"""

from __future__ import annotations

import math
from collections import Counter
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

#: The tiles taken carry a resource and no bare ground was within reach.
#: Kept distinct from a plain shift: the ore under those tiles is spent
#: either way, and a decision that does not say so cannot be measured later.
REASON_RESOURCE_SPENT = "built_on_resource_tiles"

#: ``type`` of a resource entity, as ``find_entities_filtered{type=
#: "resource"}`` and ``_save_entity_state`` report it.
RESOURCE_ENTITY_TYPE = "resource"

#: Base-game resource prototypes, read only when a row carries no ``type``.
#: A row that is neither typed nor named here stays in the blocking world,
#: which is the conservative side: an unrecognised entity is an obstacle.
RESOURCE_PROTOTYPE_NAMES = frozenset(
    {"coal", "copper-ore", "crude-oil", "iron-ore", "stone", "uranium-ore"}
)

#: Entities that have to stand on the resource they consume. A rule over
#: names, because what is planned here is a prototype name and not a live
#: entity: Factorio 2.0 has two mining drills and the pumpjack.
RESOURCE_SEEKING_NAMES = frozenset({"pumpjack"})
_RESOURCE_SEEKING_SUFFIX = "-mining-drill"

#: Ranks of the ground under a candidate, worst last: measured bare ground,
#: ground the survey never covered, measured resource. The rank is carried
#: apart from the price because "unknown" is not a quantity of tiles.
_RANK_BARE = 0
_RANK_UNSURVEYED = 1
_RANK_RESOURCE = 2


def is_resource_entity(entity: Mapping[str, Any]) -> bool:
    """Whether one surveyed row is ore under the ground, not a machine."""
    raw = entity.get("type")
    if isinstance(raw, str) and raw.strip().strip('"') == RESOURCE_ENTITY_TYPE:
        return True
    return entity_name(entity) in RESOURCE_PROTOTYPE_NAMES


def seeks_resource(entity: str) -> bool:
    """Whether the entity mines the ground it stands on.

    A drill moved off the ore places and mines nothing, so the ground price
    never applies to one: it is charged nothing for the tiles it takes and
    the scan answers exactly what it answered before there was a price.
    """
    name = (entity or "").strip().strip('"')
    return name.endswith(_RESOURCE_SEEKING_SUFFIX) or name in RESOURCE_SEEKING_NAMES


@dataclass(frozen=True)
class _ResourceReading:
    """What the ground under one candidate is, and what it would cost."""

    rank: int
    cost: float
    tiles: int
    unsurveyed: int
    names: tuple[str, ...]


@dataclass(frozen=True)
class ResourceSurvey:
    """The ore one world read reported, and the window that read covered.

    ``tiles`` maps a tile to the resource standing on it. ``surveyed`` is
    the box the read covered, in world coordinates; outside it a tile is
    unknown rather than bare. A read with no window declared knows where ore
    is and nowhere that ore is absent, which is the weaker of the two
    statements and the only one that cannot turn absence into zero.
    """

    tiles: Mapping[GridPoint, str] = field(default_factory=dict)
    surveyed: tuple[float, float, float, float] | None = None

    @classmethod
    def from_entities(
        cls,
        entities: Iterable[Any],
        surveyed: tuple[float, float, float, float] | None = None,
    ) -> ResourceSurvey:
        """Index the resource rows of a world read by the tile they cover."""
        tiles: dict[GridPoint, str] = {}
        for entity in entities or ():
            if not isinstance(entity, Mapping):
                continue
            name = entity_name(entity)
            if not name:
                continue
            for tile in entity_tiles(entity):
                tiles[tile] = name
        return cls(tiles=tiles, surveyed=surveyed)

    def covers(self, tile: GridPoint) -> bool:
        """Whether this read says anything at all about the tile."""
        if tile in self.tiles:
            return True
        return self.surveyed is not None and _inside((tile,), self.surveyed)

    def weights(self) -> dict[str, float]:
        """What one tile of each resource costs, derived from this read.

        A tile is priced by how scarce its resource is in the survey: the
        most abundant resource costs 1.0 and every other one costs the ratio
        of the tile counts, so a coal tile out of forty weighs more than an
        iron tile out of six hundred. Two limits, stated rather than hidden:
        the count is the surveyed window and not the patch on the map, so a
        patch read in part looks scarcer than it is; and the amount left in
        each tile, which the payload does carry, is not part of the weight,
        because this prices ground rather than ore.
        """
        counts = Counter(self.tiles.values())
        if not counts:
            return {}
        most = max(counts.values())
        return {name: most / count for name, count in sorted(counts.items())}

    def read(
        self,
        tiles: Iterable[GridPoint],
        weights: Mapping[str, float],
    ) -> _ResourceReading:
        """Price one candidate footprint against this read.

        Summed over the tiles in scan order rather than set order: the same
        survey has to answer the same float for the same candidate.
        """
        spent: list[str] = []
        cost = 0.0
        unsurveyed = 0
        for tile in _ordered(tiles):
            name = self.tiles.get(tile)
            if name is not None:
                spent.append(name)
                cost += float(weights.get(name, 1.0))
            elif not self.covers(tile):
                unsurveyed += 1
        if spent:
            rank = _RANK_RESOURCE
        elif unsurveyed:
            rank = _RANK_UNSURVEYED
        else:
            rank = _RANK_BARE
        return _ResourceReading(
            rank=rank,
            cost=cost,
            tiles=len(spent),
            unsurveyed=unsurveyed,
            names=tuple(sorted(set(spent))),
        )


@dataclass(frozen=True)
class WorldSurvey:
    """The world as one RCON read reported it, with resolved footprints.

    Carried as one value because the two halves have to come from the same
    read: footprints resolved against a later world would describe entities
    that are no longer standing.
    """

    entities: tuple[Mapping[str, Any], ...] = ()
    footprints: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    resources: ResourceSurvey | None = None

    @classmethod
    def from_entities(
        cls,
        entities: Iterable[Any],
        footprints: Mapping[str, tuple[int, int]] | None = None,
        surveyed: tuple[float, float, float, float] | None = None,
    ) -> WorldSurvey:
        """Split one world read into what blocks and what lies under it.

        Resource rows must not reach ``entities``: ore obstructs nothing,
        and counting it as an obstacle would mark every tile of a patch
        taken and refuse the patch entire.

        A read holding no resource row at all answers ``resources=None``. A
        read that asked for no resources and a world with no ore in it look
        identical from here, the first is what this loop has been doing, and
        a survey that guessed between them would be the absence-is-zero
        failure this layer exists to keep out.
        """
        standing: list[Mapping[str, Any]] = []
        ore: list[Mapping[str, Any]] = []
        for entity in entities or ():
            if not isinstance(entity, Mapping):
                continue
            (ore if is_resource_entity(entity) else standing).append(entity)
        return cls(
            entities=tuple(standing),
            footprints=dict(footprints or {}),
            resources=(
                ResourceSurvey.from_entities(ore, surveyed=surveyed) if ore else None
            ),
        )


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

    #: Resource tiles the placement occupies, measured. None when no
    #: resource survey reached the decision, which is not the same reading
    #: as zero: it is "nobody looked under these tiles".
    resource_tiles: int | None = None
    #: The scarcity-weighted price the choice charged. 0.0 for an entity
    #: that mines what it stands on, None on the same terms as above.
    resource_cost: float | None = None
    #: Which resources were occupied, sorted.
    resource_names: tuple[str, ...] = ()
    #: Tiles of this placement the survey never covered.
    resource_unsurveyed: int | None = None

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
            "resource_tiles": self.resource_tiles,
            "resource_cost": self.resource_cost,
            "resource_names": list(self.resource_names),
            "resource_unsurveyed": self.resource_unsurveyed,
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


def _build_on(
    *,
    position: tuple[float, float],
    offset: tuple[int, int],
    tiles: tuple[GridPoint, ...],
    scanned: int,
    reading: _ResourceReading | None,
    priced: bool,
) -> PlacementPlan:
    """One build plan, with what the ground under it was measured to be."""
    reason = REASON_ANCHOR_FREE if offset == (0, 0) else REASON_SHIFTED
    if priced and reading is not None and reading.tiles:
        reason = REASON_RESOURCE_SPENT
    return PlacementPlan(
        outcome=OUTCOME_BUILD,
        position=position,
        reason=reason,
        shift=offset,
        tiles=tiles,
        scanned=scanned,
        resource_tiles=None if reading is None else reading.tiles,
        resource_cost=(
            None if reading is None else (reading.cost if priced else 0.0)
        ),
        resource_names=() if reading is None else reading.names,
        resource_unsurveyed=None if reading is None else reading.unsurveyed,
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
    resources: ResourceSurvey | None = None,
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

    ``resources`` is the ore the same read reported. A tile carrying ore is
    not equivalent to bare ground -- a machine standing on it holds a tile
    no drill can use -- so the scan takes the cheapest ground it can reach
    and falls back on the scan order between equals. The price covers the
    reserved tiles too, since the chest of a cell spends a tile exactly as
    the machine does. It is never a veto, it never applies to an entity that
    mines what it stands on, and without a survey nothing is preferred and
    nothing is claimed: an unread tile is unknown, not bare.
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
    priced = resources is not None and not seeks_resource(entity)
    weights = {} if resources is None else resources.weights()
    offsets = scan_offsets(reach)
    best: tuple[tuple[int, float, int], PlacementPlan] | None = None
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
        reading = None if resources is None else resources.read(needed, weights)
        candidate = _build_on(
            position=position,
            offset=(dx, dy),
            tiles=_ordered(tiles),
            scanned=scanned,
            reading=reading,
            priced=priced,
        )
        if reading is None or not priced:
            return candidate
        key = (reading.rank, reading.cost, scanned)
        if best is None or key < best[0]:
            best = (key, candidate)
        if reading.rank == _RANK_BARE:
            break
    if best is not None:
        return best[1]
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
        resources=survey.resources,
    )
