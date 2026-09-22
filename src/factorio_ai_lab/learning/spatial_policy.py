from __future__ import annotations

import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import CARDINAL, RoutingWeights, weighted_astar

LOCAL_RADIUS = 2
LOCAL_SIZE = 2 * LOCAL_RADIUS + 1
FEATURE_DIM = LOCAL_SIZE * LOCAL_SIZE + 2 + len(CARDINAL)
ACTION_DIM = len(CARDINAL)


@dataclass(frozen=True)
class SpatialPolicyMetrics:
    training_examples: int
    validation_examples: int
    validation_accuracy: float
    rollout_tasks: int
    rollout_success_rate: float
    mean_cost_ratio_to_astar: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "training_examples": self.training_examples,
            "validation_examples": self.validation_examples,
            "validation_accuracy": self.validation_accuracy,
            "rollout_tasks": self.rollout_tasks,
            "rollout_success_rate": self.rollout_success_rate,
            "mean_cost_ratio_to_astar": self.mean_cost_ratio_to_astar,
        }


def route_cost(
    path: tuple[GridPoint, ...],
    *,
    turn_penalty: float,
) -> float:
    if len(path) <= 1:
        return 0.0
    cost = 0.0
    previous: tuple[int, int] | None = None
    for current, nxt in pairwise(path):
        direction = (nxt.x - current.x, nxt.y - current.y)
        cost += 1.0
        if previous is not None and direction != previous:
            cost += turn_penalty
        previous = direction
    return cost


def encode_state(
    *,
    point: GridPoint,
    goal: GridPoint,
    is_blocked: Callable[[GridPoint], bool],
    in_bounds: Callable[[GridPoint], bool],
    previous_direction: tuple[int, int] | None,
) -> np.ndarray:
    features: list[float] = []
    for dy in range(-LOCAL_RADIUS, LOCAL_RADIUS + 1):
        for dx in range(-LOCAL_RADIUS, LOCAL_RADIUS + 1):
            probe = GridPoint(point.x + dx, point.y + dy)
            occupied = not in_bounds(probe) or (
                probe != goal and is_blocked(probe)
            )
            features.append(1.0 if occupied else 0.0)

    delta_x = goal.x - point.x
    delta_y = goal.y - point.y
    distance = max(1.0, float(abs(delta_x) + abs(delta_y)))
    features.extend(
        [
            float(delta_x) / distance,
            float(delta_y) / distance,
        ]
    )
    for direction in CARDINAL:
        features.append(1.0 if previous_direction == direction else 0.0)
    return np.asarray(features, dtype=np.float64)


def action_index(
    current: GridPoint,
    nxt: GridPoint,
) -> int:
    delta = (nxt.x - current.x, nxt.y - current.y)
    return CARDINAL.index(delta)


class SpatialPolicy:
    def __init__(
        self,
        *,
        hidden_size: int = 48,
        seed: int = 20260921,
    ) -> None:
        self.hidden_size = hidden_size
        self.seed = seed
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0.0, math.sqrt(2.0 / FEATURE_DIM), (FEATURE_DIM, hidden_size))
        self.b1 = np.zeros(hidden_size, dtype=np.float64)
        self.w2 = rng.normal(0.0, math.sqrt(2.0 / hidden_size), (hidden_size, ACTION_DIM))
        self.b2 = np.zeros(ACTION_DIM, dtype=np.float64)

    def logits(self, x: np.ndarray) -> np.ndarray:
        hidden = np.maximum(0.0, x @ self.w1 + self.b1)
        return hidden @ self.w2 + self.b2

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        epochs: int = 80,
        learning_rate: float = 0.015,
        batch_size: int = 256,
    ) -> None:
        rng = np.random.default_rng(self.seed + 17)
        n = len(x)
        if n < 16:
            raise ValueError("at least 16 route decisions are required")

        for _ in range(epochs):
            order = rng.permutation(n)
            for start in range(0, n, batch_size):
                indices = order[start : start + batch_size]
                xb = x[indices]
                yb = y[indices]
                pre = xb @ self.w1 + self.b1
                hidden = np.maximum(0.0, pre)
                logits = hidden @ self.w2 + self.b2
                logits -= logits.max(axis=1, keepdims=True)
                exp = np.exp(logits)
                probabilities = exp / exp.sum(axis=1, keepdims=True)
                probabilities[np.arange(len(yb)), yb] -= 1.0
                grad_logits = probabilities / len(yb)

                grad_w2 = hidden.T @ grad_logits
                grad_b2 = grad_logits.sum(axis=0)
                grad_hidden = grad_logits @ self.w2.T
                grad_hidden[pre <= 0] = 0.0
                grad_w1 = xb.T @ grad_hidden
                grad_b1 = grad_hidden.sum(axis=0)

                self.w2 -= learning_rate * grad_w2
                self.b2 -= learning_rate * grad_b2
                self.w1 -= learning_rate * grad_w1
                self.b1 -= learning_rate * grad_b1

    def accuracy(self, x: np.ndarray, y: np.ndarray) -> float:
        prediction = np.argmax(self.logits(x), axis=1)
        return float(np.mean(prediction == y))

    def rollout(
        self,
        start: GridPoint,
        goal: GridPoint,
        *,
        is_blocked: Callable[[GridPoint], bool],
        in_bounds: Callable[[GridPoint], bool],
        max_steps: int = 512,
    ) -> tuple[GridPoint, ...] | None:
        current = start
        previous: tuple[int, int] | None = None
        path = [current]
        visits: dict[tuple[GridPoint, tuple[int, int] | None], int] = {}

        for _ in range(max_steps):
            if current == goal:
                return tuple(path)
            state = (current, previous)
            visits[state] = visits.get(state, 0) + 1
            if visits[state] > 2:
                return None

            encoded = encode_state(
                point=current,
                goal=goal,
                is_blocked=is_blocked,
                in_bounds=in_bounds,
                previous_direction=previous,
            )
            scores = self.logits(encoded[None, :])[0]
            ranked = np.argsort(scores)[::-1]
            chosen: tuple[int, int] | None = None
            for index in ranked:
                direction = CARDINAL[int(index)]
                nxt = GridPoint(current.x + direction[0], current.y + direction[1])
                if not in_bounds(nxt) or (nxt != goal and is_blocked(nxt)):
                    continue
                next_state = (nxt, direction)
                if visits.get(next_state, 0) >= 2:
                    continue
                chosen = direction
                break
            if chosen is None:
                return None

            current = GridPoint(current.x + chosen[0], current.y + chosen[1])
            previous = chosen
            path.append(current)

        return tuple(path) if current == goal else None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            w1=self.w1,
            b1=self.b1,
            w2=self.w2,
            b2=self.b2,
            hidden_size=np.asarray([self.hidden_size]),
            seed=np.asarray([self.seed]),
        )

    @classmethod
    def load(cls, path: Path) -> SpatialPolicy:
        payload = np.load(path)
        model = cls(
            hidden_size=int(payload["hidden_size"][0]),
            seed=int(payload["seed"][0]),
        )
        model.w1 = payload["w1"]
        model.b1 = payload["b1"]
        model.w2 = payload["w2"]
        model.b2 = payload["b2"]
        return model


def examples_from_path(
    path: tuple[GridPoint, ...],
    *,
    blocked: set[GridPoint],
    in_bounds: Callable[[GridPoint], bool],
) -> tuple[list[np.ndarray], list[int]]:
    x: list[np.ndarray] = []
    y: list[int] = []
    previous: tuple[int, int] | None = None
    is_blocked = blocked.__contains__
    goal = path[-1]
    for current, nxt in pairwise(path):
        x.append(
            encode_state(
                point=current,
                goal=goal,
                is_blocked=is_blocked,
                in_bounds=in_bounds,
                previous_direction=previous,
            )
        )
        y.append(action_index(current, nxt))
        previous = (nxt.x - current.x, nxt.y - current.y)
    return x, y


def random_route_tasks(
    *,
    count: int,
    width: int,
    height: int,
    obstacle_probability: float,
    seed: int,
    turn_penalty: float,
) -> list[tuple[GridPoint, GridPoint, set[GridPoint], tuple[GridPoint, ...], float]]:
    rng = random.Random(seed)
    tasks = []
    attempts = 0
    while len(tasks) < count and attempts < count * 20:
        attempts += 1
        start = GridPoint(rng.randrange(width), rng.randrange(height))
        goal = GridPoint(rng.randrange(width), rng.randrange(height))
        if start.manhattan(goal) < max(5, min(width, height) // 3):
            continue
        blocked: set[GridPoint] = set()
        for yy in range(height):
            for xx in range(width):
                point = GridPoint(xx, yy)
                if point in {start, goal}:
                    continue
                if rng.random() < obstacle_probability:
                    blocked.add(point)
        in_bounds = lambda point, w=width, h=height: 0 <= point.x < w and 0 <= point.y < h
        route = weighted_astar(
            start,
            goal,
            is_blocked=blocked.__contains__,
            in_bounds=in_bounds,
            weights=RoutingWeights(turn=turn_penalty),
        )
        if route is None:
            continue
        tasks.append((start, goal, blocked, route.path, route.cost))
    return tasks


def load_real_paths(path: Path) -> list[tuple[GridPoint, ...]]:
    paths: list[tuple[GridPoint, ...]] = []
    if not path.exists():
        return paths
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not row.get("accepted"):
            continue
        raw_path = row.get("path")
        if not isinstance(raw_path, list) or len(raw_path) < 2:
            continue
        points = tuple(
            GridPoint(
                round(float(point["x"]) - 0.5),
                round(float(point["y"]) - 0.5),
            )
            for point in raw_path
            if isinstance(point, dict) and "x" in point and "y" in point
        )
        if len(points) >= 2:
            paths.append(points)
    return paths
