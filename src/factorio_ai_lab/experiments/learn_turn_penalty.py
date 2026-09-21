from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.experiments.routing_sweep import generated_obstacles
from factorio_ai_lab.learning.bandit import UCB1Bandit
from factorio_ai_lab.metrics.routing import routing_metrics
from factorio_ai_lab.planning.astar import (
    RoutingWeights,
    blocked_from,
    rectangular_bounds,
    weighted_astar,
)


def reward_for_route(
    *,
    success: bool,
    route_length: int,
    turns: int,
    expanded_nodes: int,
) -> float:
    if not success:
        return -2.0
    return (
        1.0
        - 0.010 * route_length
        - 0.015 * turns
        - 0.0005 * expanded_nodes
    )


def run_learning(
    *,
    episodes: int,
    arms: tuple[float, ...],
    width: int = 31,
    height: int = 31,
    obstacle_density: float = 0.18,
    exploration: float = 2.0,
) -> tuple[list[dict], dict]:
    bandit = UCB1Bandit(arms, exploration=exploration)
    start = GridPoint(0, height // 2)
    goal = GridPoint(width - 1, height // 2)
    history: list[dict] = []

    for episode in range(episodes):
        penalty = bandit.select()
        blocked = generated_obstacles(
            episode,
            width=width,
            height=height,
            density=obstacle_density,
            start=start,
            goal=goal,
        )
        result = weighted_astar(
            start,
            goal,
            is_blocked=blocked_from(blocked),
            in_bounds=rectangular_bounds(width, height),
            weights=RoutingWeights(turn=penalty),
        )
        metrics = routing_metrics(result)
        reward = reward_for_route(
            success=metrics.success,
            route_length=metrics.route_length,
            turns=metrics.turns,
            expanded_nodes=metrics.expanded_nodes,
        )
        bandit.update(penalty, reward)

        history.append(
            {
                "episode": episode,
                "turn_penalty": penalty,
                "reward": reward,
                "success": metrics.success,
                "route_length": metrics.route_length,
                "turns": metrics.turns,
                "expanded_nodes": metrics.expanded_nodes,
                "planner_cost": metrics.cost,
            }
        )

    stats = bandit.stats()
    summary = {
        "episodes": episodes,
        "exploration": exploration,
        "best_observed": bandit.best_observed(),
        "arms": {
            str(arm): asdict(stats[arm])
            for arm in bandit.arms
        },
    }
    return history, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=300)
    parser.add_argument(
        "--arms",
        type=float,
        nargs="+",
        default=[0.0, 0.1, 0.25, 0.5, 1.0, 2.0],
    )
    parser.add_argument("--exploration", type=float, default=2.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/turn_penalty_learning.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("runs/turn_penalty_learning_summary.json"),
    )
    args = parser.parse_args()

    history, summary = run_learning(
        episodes=args.episodes,
        arms=tuple(args.arms),
        exploration=args.exploration,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in history:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    args.summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
