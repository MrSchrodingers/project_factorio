from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import RouteResult


@dataclass(frozen=True)
class RoutingMetrics:
    success: bool
    route_length: int
    turns: int
    expanded_nodes: int
    cost: float


def count_turns(path: tuple[GridPoint, ...]) -> int:
    if len(path) < 3:
        return 0

    turns = 0
    previous = (
        path[1].x - path[0].x,
        path[1].y - path[0].y,
    )
    for left, right in pairwise(path[1:]):
        current = (right.x - left.x, right.y - left.y)
        if current != previous:
            turns += 1
        previous = current
    return turns


def routing_metrics(result: RouteResult | None) -> RoutingMetrics:
    if result is None:
        return RoutingMetrics(
            success=False,
            route_length=0,
            turns=0,
            expanded_nodes=0,
            cost=float("inf"),
        )

    return RoutingMetrics(
        success=True,
        route_length=max(0, len(result.path) - 1),
        turns=count_turns(result.path),
        expanded_nodes=result.expanded_nodes,
        cost=result.cost,
    )
