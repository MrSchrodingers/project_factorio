from __future__ import annotations

import argparse

from factorio_ai_lab.agents.router import TaskKind, route_task
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.metrics.objectives import ObjectiveVector, scalar_score
from factorio_ai_lab.planning.astar import blocked_from, rectangular_bounds, weighted_astar


def baseline() -> None:
    obstacles = {GridPoint(2, 1), GridPoint(2, 2), GridPoint(2, 3)}
    route = weighted_astar(
        GridPoint(0, 2),
        GridPoint(5, 2),
        is_blocked=blocked_from(obstacles),
        in_bounds=rectangular_bounds(6, 5),
    )
    objective = ObjectiveVector(
        throughput=16.0,
        material_cost=20.0,
        area=30.0,
        energy=100.0,
        route_length=0.0 if route is None else len(route.path) - 1,
        failures=0,
        deadlocks=0,
        milestones=1.0,
    )

    print("Factorio AI Lab baseline")
    print(f"route={None if route is None else route.path}")
    print(f"route_cost={None if route is None else route.cost:.3f}")
    print(f"score={scalar_score(objective):.6f}")
    print(f"spatial_engine={route_task(TaskKind.SPATIAL_ROUTE).engine}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["baseline"])
    args = parser.parse_args()
    if args.command == "baseline":
        baseline()


if __name__ == "__main__":
    main()
