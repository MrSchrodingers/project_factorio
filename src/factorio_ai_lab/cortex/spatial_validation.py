"""Observed-world spatial validation for bounded Cortex Options.

This module validates route feasibility without mutating Factorio and without
using FLE's asynchronous request_path/get_path primitive. It combines the
project's weighted A* planner with canonical dashboard observations:

- water tiles from FactorioObserver.map_snapshot().terrain_runs;
- trees, rocks and cliffs from map_snapshot().natural;
- player-built entity footprints from the observed WORLD.

Fast-mode executors may teleport the synthetic FLE character, but every such
reposition remains admissible only after this planner proves a collision-free
route through the observed map.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import RoutingWeights, weighted_astar
from factorio_ai_lab.planning.footprints import blocked_tiles

VALIDATOR_VERSION = "observed_weighted_astar_v1"
DEFAULT_TURN_PENALTY = 0.10


@dataclass(frozen=True)
class ObservedRouteValidation:
    start: GridPoint
    goal: GridPoint
    path: tuple[GridPoint, ...]
    cost: float
    expanded_nodes: int
    bounds: tuple[int, int, int, int]
    water_blocked: int
    natural_blocked: int
    entity_blocked: int
    turn_penalty: float = DEFAULT_TURN_PENALTY

    @property
    def path_waypoints(self) -> int:
        return len(self.path)

    @property
    def path_length_tiles(self) -> int:
        return max(0, len(self.path) - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator": VALIDATOR_VERSION,
            "start": {"x": self.start.x, "y": self.start.y},
            "goal": {"x": self.goal.x, "y": self.goal.y},
            "path_waypoints": self.path_waypoints,
            "path_length_tiles": self.path_length_tiles,
            "cost": self.cost,
            "expanded_nodes": self.expanded_nodes,
            "bounds": {
                "min_x": self.bounds[0],
                "min_y": self.bounds[1],
                "max_x": self.bounds[2],
                "max_y": self.bounds[3],
            },
            "water_blocked": self.water_blocked,
            "natural_blocked": self.natural_blocked,
            "entity_blocked": self.entity_blocked,
            "turn_penalty": self.turn_penalty,
        }


def _grid_position(position: tuple[float, float]) -> GridPoint:
    return GridPoint(
        math.floor(float(position[0])),
        math.floor(float(position[1])),
    )


def _observed_bounds(snapshot: Mapping[str, Any]) -> tuple[int, int, int, int]:
    bounds = snapshot.get("bounds")
    if not isinstance(bounds, Mapping):
        raise TypeError("map snapshot has no bounds")
    left_top = bounds.get("left_top")
    right_bottom = bounds.get("right_bottom")
    if not isinstance(left_top, Mapping) or not isinstance(right_bottom, Mapping):
        raise TypeError("map snapshot bounds are incomplete")
    try:
        min_x = math.floor(float(left_top["x"]))
        min_y = math.floor(float(left_top["y"]))
        max_x = math.ceil(float(right_bottom["x"])) - 1
        max_y = math.ceil(float(right_bottom["y"])) - 1
    except (KeyError, TypeError, ValueError) as exc:
        raise TypeError("map snapshot bounds are non-numeric") from exc
    if max_x < min_x or max_y < min_y:
        raise ValueError("map snapshot bounds are empty")
    return min_x, min_y, max_x, max_y


def _water_tiles(snapshot: Mapping[str, Any]) -> set[GridPoint]:
    blocked: set[GridPoint] = set()
    rows = snapshot.get("terrain_runs")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return blocked
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if "water" not in str(row.get("name") or ""):
            continue
        try:
            y = math.floor(float(row["y"]))
            x1 = math.floor(float(row["x1"]))
            x2 = math.floor(float(row["x2"]))
        except (KeyError, TypeError, ValueError):
            continue
        if x2 < x1:
            x1, x2 = x2, x1
        blocked.update(GridPoint(x, y) for x in range(x1, x2 + 1))
    return blocked


def _natural_tiles(snapshot: Mapping[str, Any]) -> set[GridPoint]:
    blocked: set[GridPoint] = set()
    rows = snapshot.get("natural")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return blocked
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        position = row.get("position")
        if not isinstance(position, Mapping):
            continue
        try:
            x = math.floor(float(position["x"]))
            y = math.floor(float(position["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        blocked.add(GridPoint(x, y))
    return blocked


def validate_observed_route(
    *,
    start: tuple[float, float],
    goal: tuple[float, float],
    map_snapshot: Mapping[str, Any],
    world_entities: Sequence[Mapping[str, Any]],
    footprints: Mapping[str, tuple[int, int]] | None = None,
    turn_penalty: float = DEFAULT_TURN_PENALTY,
) -> ObservedRouteValidation | None:
    """Return an observed-world A* route, or None when no route is feasible."""
    if map_snapshot.get("connected") is not True:
        raise RuntimeError("canonical map snapshot is unavailable")

    bounds = _observed_bounds(map_snapshot)
    start_grid = _grid_position(start)
    goal_grid = _grid_position(goal)
    min_x, min_y, max_x, max_y = bounds
    for label, point in (("start", start_grid), ("goal", goal_grid)):
        if not (min_x <= point.x <= max_x and min_y <= point.y <= max_y):
            raise ValueError(
                f"{label} {point!r} lies outside observed bounds {bounds!r}"
            )

    water = _water_tiles(map_snapshot)
    natural = _natural_tiles(map_snapshot)
    entities = blocked_tiles(world_entities, footprints)
    blocked = water | natural | entities

    result = weighted_astar(
        start_grid,
        goal_grid,
        is_blocked=blocked.__contains__,
        in_bounds=lambda point: (
            min_x <= point.x <= max_x and min_y <= point.y <= max_y
        ),
        weights=RoutingWeights(turn=float(turn_penalty)),
    )
    if result is None:
        return None
    return ObservedRouteValidation(
        start=start_grid,
        goal=goal_grid,
        path=result.path,
        cost=float(result.cost),
        expanded_nodes=int(result.expanded_nodes),
        bounds=bounds,
        water_blocked=len(water),
        natural_blocked=len(natural),
        entity_blocked=len(entities),
        turn_penalty=float(turn_penalty),
    )
