"""Exact tile footprints for the entities that obstruct belt routing.

The A* belt planner has to know the tiles an entity really occupies. Guessing
a square radius per entity name is wrong in both directions: radius 1 marks 9
tiles for a 2x2 burner mining drill, and radius 0 marks a single tile for a
3x3 assembling machine, so the planner routes a belt straight through a
machine and the run only fails later, inside ``place_entity``.

Footprints come from the Factorio runtime prototype table through two
equivalent paths, both reading ``prototypes.entity``:

* :func:`prototype_footprints` indexes the payload of the dashboard
  entity-prototype RCON command (``FactorioObserver.entity_prototypes``);
* ``tile_dimensions``, which ``_save_entity_state`` already attaches to every
  entity it returns, is read straight off the entity.

:data:`DEFAULT_TILE_FOOTPRINTS` is the documented fallback for when the
runtime answers neither: a static copy of the Factorio 2.0 base prototype
table listing every placeable entity whose footprint is not 1x1. A name
missing from all three sources resolves to 1x1, which is what the replaced
radius heuristic already assumed for every name except two hardcoded ones, so
the fallback is never coarser than the behaviour it replaces.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from factorio_ai_lab.domain.state import GridPoint

# Factorio 2.0 stores 16 directions. Only the cardinals matter for a
# footprint, and east/west swap width with height for non-square entities.
_CARDINALS: tuple[int, ...] = (0, 4, 8, 12)
_ROTATING_CARDINALS = frozenset({4, 12})

# The character is not a construction obstacle: the engine displaces it.
_NON_BLOCKING_NAMES = frozenset({"character"})

# Tile centres sit at .5; this guards floor() against float noise in
# positions that arrive as decimal strings from the Lua serializer.
_TILE_EPSILON = 1e-6

DEFAULT_TILE_FOOTPRINTS: dict[str, tuple[int, int]] = {
    "accumulator": (2, 2),
    "arithmetic-combinator": (1, 2),
    "artillery-turret": (3, 3),
    "artillery-wagon": (2, 5),
    "assembling-machine-1": (3, 3),
    "assembling-machine-2": (3, 3),
    "assembling-machine-3": (3, 3),
    "beacon": (3, 3),
    "big-electric-pole": (2, 2),
    "boiler": (3, 2),
    "burner-generator": (3, 5),
    "burner-mining-drill": (2, 2),
    "car": (2, 2),
    "cargo-landing-pad": (8, 8),
    "cargo-wagon": (2, 5),
    "centrifuge": (3, 3),
    "chemical-plant": (3, 3),
    "curved-rail-a": (2, 4),
    "curved-rail-b": (2, 2),
    "decider-combinator": (1, 2),
    "electric-energy-interface": (2, 2),
    "electric-furnace": (3, 3),
    "electric-mining-drill": (3, 3),
    "express-loader": (1, 2),
    "express-splitter": (2, 1),
    "fast-loader": (1, 2),
    "fast-splitter": (2, 1),
    "flamethrower-turret": (2, 3),
    "fluid-wagon": (2, 5),
    "gun-turret": (2, 2),
    "half-diagonal-rail": (2, 2),
    "heat-exchanger": (3, 2),
    "infinity-cargo-wagon": (2, 5),
    "lab": (3, 3),
    "laser-turret": (2, 2),
    "legacy-curved-rail": (4, 8),
    "legacy-straight-rail": (2, 2),
    "loader": (1, 2),
    "locomotive": (2, 6),
    "nuclear-reactor": (5, 5),
    "oil-refinery": (5, 5),
    "power-switch": (2, 2),
    "pump": (1, 2),
    "pumpjack": (3, 3),
    "radar": (3, 3),
    "roboport": (4, 4),
    "rocket-silo": (9, 9),
    "selector-combinator": (1, 2),
    "solar-panel": (3, 3),
    "spidertron": (2, 2),
    "splitter": (2, 1),
    "steam-engine": (3, 5),
    "steam-turbine": (3, 5),
    "steel-furnace": (2, 2),
    "stone-furnace": (2, 2),
    "storage-tank": (3, 3),
    "straight-rail": (2, 2),
    "substation": (2, 2),
    "tank": (2, 3),
    "train-stop": (2, 2),
}


def entity_name(entity: Mapping[str, Any]) -> str:
    """Entity name as a bare string, tolerating the Lua quoting."""
    raw = entity.get("name")
    if not isinstance(raw, str):
        return ""
    return raw.strip().strip('"')


def cardinal_direction(raw: Any) -> int:
    """Snap a 16-way Factorio direction to the nearest cardinal."""
    try:
        direction = int(float(raw or 0)) % 16
    except (TypeError, ValueError):
        return 0
    return min(
        _CARDINALS,
        key=lambda value: min(abs(direction - value), 16 - abs(direction - value)),
    )


def rotate_footprint(width: int, height: int, direction: Any) -> tuple[int, int]:
    """Apply the east/west rotation: a 3x2 boiler is 2x3 when facing east."""
    width = max(1, int(width))
    height = max(1, int(height))
    if width != height and cardinal_direction(direction) in _ROTATING_CARDINALS:
        return height, width
    return width, height


def _footprint_pair(width: Any, height: Any) -> tuple[int, int] | None:
    try:
        tiles = (int(float(width)), int(float(height)))
    except (TypeError, ValueError):
        return None
    if tiles[0] <= 0 or tiles[1] <= 0:
        return None
    return tiles


def prototype_footprints(payload: Any) -> dict[str, tuple[int, int]]:
    """Index ``tile_width``/``tile_height`` by entity name.

    Accepts every shape the dashboard entity-prototype payload takes:
    ``{"by_name": {...}}``, ``{"prototypes": [...]}`` or a bare
    ``name -> prototype`` mapping. Rows without a usable footprint are
    dropped, so the caller falls through to the next source.
    """
    rows: list[Any] = []
    if isinstance(payload, Mapping):
        for key in ("by_name", "prototypes"):
            section = payload.get(key)
            if isinstance(section, Mapping):
                rows.extend(section.values())
            elif isinstance(section, Iterable) and not isinstance(section, (str, bytes)):
                rows.extend(section)
        if not rows:
            rows.extend(payload.values())
    elif isinstance(payload, Iterable) and not isinstance(payload, (str, bytes)):
        rows.extend(payload)

    footprints: dict[str, tuple[int, int]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = entity_name(row)
        tiles = _footprint_pair(row.get("tile_width"), row.get("tile_height"))
        if name and tiles is not None:
            footprints[name] = tiles
    return footprints


def entity_footprint(
    entity: Mapping[str, Any],
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[int, int]:
    """Rotated ``(width, height)`` in tiles for one live entity."""
    name = entity_name(entity)
    tiles: tuple[int, int] | None = None
    if footprints:
        tiles = footprints.get(name)
    if tiles is None:
        dimensions = entity.get("tile_dimensions")
        if isinstance(dimensions, Mapping):
            tiles = _footprint_pair(
                dimensions.get("tile_width"),
                dimensions.get("tile_height"),
            )
    if tiles is None:
        tiles = DEFAULT_TILE_FOOTPRINTS.get(name)
    if tiles is None:
        tiles = (1, 1)
    return rotate_footprint(tiles[0], tiles[1], entity.get("direction"))


def entity_tiles(
    entity: Mapping[str, Any],
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> set[GridPoint]:
    """Every tile the entity occupies, derived from its centre position.

    Factorio anchors an entity at its centre, so a 3x3 machine at (5.5, 5.5)
    covers tiles 4..6 on both axes while a 2x2 one at (5.0, 5.0) covers 4..5.
    """
    position = entity.get("position")
    if not isinstance(position, Mapping):
        return set()
    try:
        centre_x = float(position["x"])
        centre_y = float(position["y"])
    except (KeyError, TypeError, ValueError):
        return set()

    width, height = entity_footprint(entity, footprints)
    left = math.floor(centre_x - width / 2 + _TILE_EPSILON)
    top = math.floor(centre_y - height / 2 + _TILE_EPSILON)
    return {
        GridPoint(left + offset_x, top + offset_y)
        for offset_x in range(width)
        for offset_y in range(height)
    }


def blocked_tiles(
    entities: Iterable[Any],
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> set[GridPoint]:
    """Union of the tiles occupied by every blocking entity."""
    blocked: set[GridPoint] = set()
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        if entity_name(entity) in _NON_BLOCKING_NAMES:
            continue
        blocked |= entity_tiles(entity, footprints)
    return blocked
