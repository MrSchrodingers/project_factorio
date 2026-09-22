from __future__ import annotations

import argparse
import csv
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.metrics.routing import routing_metrics
from factorio_ai_lab.planning.astar import (
    RoutingWeights,
    blocked_from,
    rectangular_bounds,
    weighted_astar,
)


@dataclass(frozen=True)
class SweepRow:
    seed: int
    turn_penalty: float
    success: bool
    route_length: int
    turns: int
    expanded_nodes: int
    planner_cost: float
    wall_ms: float


def generated_obstacles(
    seed: int,
    *,
    width: int,
    height: int,
    density: float,
    start: GridPoint,
    goal: GridPoint,
) -> set[GridPoint]:
    if not 0.0 <= density < 1.0:
        raise ValueError("density deve estar em [0, 1)")

    rng = random.Random(seed)
    blocked: set[GridPoint] = set()
    for y in range(height):
        for x in range(width):
            point = GridPoint(x, y)
            if point in {start, goal}:
                continue
            if rng.random() < density:
                blocked.add(point)
    return blocked


def run_sweep(
    *,
    seeds: range,
    turn_penalties: tuple[float, ...],
    width: int = 31,
    height: int = 31,
    obstacle_density: float = 0.18,
) -> list[SweepRow]:
    start = GridPoint(0, height // 2)
    goal = GridPoint(width - 1, height // 2)
    rows: list[SweepRow] = []

    for seed in seeds:
        blocked = generated_obstacles(
            seed,
            width=width,
            height=height,
            density=obstacle_density,
            start=start,
            goal=goal,
        )
        for penalty in turn_penalties:
            t0 = perf_counter()
            result = weighted_astar(
                start,
                goal,
                is_blocked=blocked_from(blocked),
                in_bounds=rectangular_bounds(width, height),
                weights=RoutingWeights(turn=penalty),
            )
            elapsed_ms = (perf_counter() - t0) * 1000.0
            metrics = routing_metrics(result)
            rows.append(
                SweepRow(
                    seed=seed,
                    turn_penalty=penalty,
                    success=metrics.success,
                    route_length=metrics.route_length,
                    turns=metrics.turns,
                    expanded_nodes=metrics.expanded_nodes,
                    planner_cost=metrics.cost,
                    wall_ms=elapsed_ms,
                )
            )
    return rows


def write_csv(rows: list[SweepRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("runs/routing_sweep.csv"))
    parser.add_argument(
        "--turn-penalties",
        type=float,
        nargs="+",
        default=[0.0, 0.1, 0.25, 0.5, 1.0, 2.0],
    )
    args = parser.parse_args()

    rows = run_sweep(
        seeds=range(args.seeds),
        turn_penalties=tuple(args.turn_penalties),
    )
    write_csv(rows, args.output)

    print(f"rows={len(rows)} output={args.output}")
    for penalty in args.turn_penalties:
        group = [row for row in rows if row.turn_penalty == penalty]
        solved = [row for row in group if row.success]
        success_rate = len(solved) / len(group)
        mean_length = sum(row.route_length for row in solved) / len(solved) if solved else float("nan")
        mean_turns = sum(row.turns for row in solved) / len(solved) if solved else float("nan")
        mean_expanded = (
            sum(row.expanded_nodes for row in solved) / len(solved) if solved else float("nan")
        )
        print(
            f"turn={penalty:.3f} success={success_rate:.3f} "
            f"length={mean_length:.3f} turns={mean_turns:.3f} "
            f"expanded={mean_expanded:.1f}"
        )


if __name__ == "__main__":
    main()
