from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import inf

from factorio_ai_lab.domain.state import GridPoint


@dataclass(frozen=True)
class RoutingWeights:
    step: float = 1.0
    turn: float = 0.35
    occupied: float = 8.0


@dataclass(frozen=True)
class RouteResult:
    path: tuple[GridPoint, ...]
    cost: float
    expanded_nodes: int


Direction = tuple[int, int]
CARDINAL: tuple[Direction, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
DEFAULT_ROUTING_WEIGHTS = RoutingWeights()


def weighted_astar(
    start: GridPoint,
    goal: GridPoint,
    *,
    is_blocked: Callable[[GridPoint], bool],
    in_bounds: Callable[[GridPoint], bool],
    weights: RoutingWeights = DEFAULT_ROUTING_WEIGHTS,
    extra_cost: Callable[[GridPoint], float] | None = None,
) -> RouteResult | None:
    """A* em grade 4-conexa com penalidade de curvas e custos contextuais."""
    if start == goal:
        return RouteResult((start,), 0.0, 0)

    serial = count()
    start_state = (start, None)
    frontier: list[tuple[float, int, float, GridPoint, Direction | None]] = []
    heappush(frontier, (start.manhattan(goal) * weights.step, next(serial), 0.0, start, None))

    best: dict[tuple[GridPoint, Direction | None], float] = {start_state: 0.0}
    parent: dict[
        tuple[GridPoint, Direction | None],
        tuple[GridPoint, Direction | None] | None,
    ] = {start_state: None}

    expanded = 0
    final_state: tuple[GridPoint, Direction | None] | None = None

    while frontier:
        _, _, g, point, previous_direction = heappop(frontier)
        state = (point, previous_direction)
        if g != best.get(state):
            continue

        expanded += 1
        if point == goal:
            final_state = state
            break

        for direction in CARDINAL:
            nxt = GridPoint(point.x + direction[0], point.y + direction[1])
            if not in_bounds(nxt) or (nxt != goal and is_blocked(nxt)):
                continue

            turn_cost = 0.0
            if previous_direction is not None and direction != previous_direction:
                turn_cost = weights.turn

            environmental = 0.0 if extra_cost is None else float(extra_cost(nxt))
            if environmental < 0:
                raise ValueError("extra_cost deve ser não-negativo")

            candidate = g + weights.step + turn_cost + environmental
            nxt_state = (nxt, direction)
            if candidate >= best.get(nxt_state, inf):
                continue

            best[nxt_state] = candidate
            parent[nxt_state] = state
            heuristic = nxt.manhattan(goal) * weights.step
            heappush(frontier, (candidate + heuristic, next(serial), candidate, nxt, direction))

    if final_state is None:
        return None

    reverse_path: list[GridPoint] = []
    cursor: tuple[GridPoint, Direction | None] | None = final_state
    while cursor is not None:
        reverse_path.append(cursor[0])
        cursor = parent[cursor]
    reverse_path.reverse()

    return RouteResult(tuple(reverse_path), best[final_state], expanded)


def rectangular_bounds(width: int, height: int) -> Callable[[GridPoint], bool]:
    if width <= 0 or height <= 0:
        raise ValueError("width e height devem ser positivos")

    def predicate(point: GridPoint) -> bool:
        return 0 <= point.x < width and 0 <= point.y < height

    return predicate


def blocked_from(points: Iterable[GridPoint]) -> Callable[[GridPoint], bool]:
    blocked = frozenset(points)
    return blocked.__contains__
