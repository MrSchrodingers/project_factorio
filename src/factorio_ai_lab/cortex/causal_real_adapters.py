"""Real F4-C task-family adapters used only through the paired harness.

This module starts with the spatial logistics adapter because it can exercise
the existing weighted_astar production planner without mutating Factorio.
The fixture is explicitly NON-PROTOCOL: no pilot/evaluation/confirmatory seed
is accepted by this preflight adapter.
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    OUTCOME_EXTRACTOR_VERSION,
    ArmObservation,
    HarnessBudget,
    HarnessValidationError,
    MemoryAccess,
    checkpoint_digest,
)
from factorio_ai_lab.cortex.memory import ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import MemoryQuery
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import (
    RoutingWeights,
    blocked_from,
    rectangular_bounds,
    weighted_astar,
)

SPATIAL_ADAPTER_VERSION = "cortex_f4c_spatial_real_adapter_v1"
SPATIAL_TOOL_SURFACE = ("planning.astar.weighted_astar",)
_ALLOWED_PREFLIGHT_PARTITIONS = frozenset({"adapter_preflight"})


def _point(raw: Any, *, name: str) -> GridPoint:
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in raw)
    ):
        raise HarnessValidationError(f"{name} must be [int, int]")
    return GridPoint(int(raw[0]), int(raw[1]))


def _grid(raw: Any) -> tuple[int, int]:
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in raw)
    ):
        raise HarnessValidationError("grid must be [width, height]")
    width, height = int(raw[0]), int(raw[1])
    if width <= 0 or height <= 0:
        raise HarnessValidationError("grid dimensions must be positive")
    return width, height


def _blocked_cells(rectangles: Any) -> tuple[GridPoint, ...]:
    if not isinstance(rectangles, list):
        raise HarnessValidationError("obstacle_rectangles must be a list")
    blocked: set[GridPoint] = set()
    for index, raw in enumerate(rectangles):
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) != 4
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in raw
            )
        ):
            raise HarnessValidationError(
                f"obstacle_rectangles[{index}] must be [x, y, w, h]"
            )
        x, y, width, height = map(int, raw)
        if width <= 0 or height <= 0:
            raise HarnessValidationError(
                f"obstacle_rectangles[{index}] dimensions must be positive"
            )
        for px in range(x, x + width):
            for py in range(y, y + height):
                blocked.add(GridPoint(px, py))
    return tuple(sorted(blocked, key=lambda point: (point.x, point.y)))


@dataclass(frozen=True)
class SpatialRoutingFixture:
    """Disposable non-protocol routing world for adapter preflight."""

    width: int
    height: int
    start: GridPoint
    goal: GridPoint
    blocked: tuple[GridPoint, ...]
    resource_budget_tiles: int

    @classmethod
    def from_task(
        cls,
        task: dict[str, Any],
        *,
        resource_budget_tiles: int,
    ) -> SpatialRoutingFixture:
        if task.get("family") != "spatial_logistics_routing":
            raise HarnessValidationError("task family must be spatial_logistics_routing")
        if task.get("partition") not in _ALLOWED_PREFLIGHT_PARTITIONS:
            raise HarnessValidationError(
                "spatial adapter preflight accepts NON-PROTOCOL adapter_preflight only"
            )
        if task.get("seed") is not None:
            raise HarnessValidationError(
                "spatial adapter preflight forbids protocol/experimental seeds"
            )
        spec = task.get("spec")
        if not isinstance(spec, dict):
            raise HarnessValidationError("spatial task spec missing")
        width, height = _grid(spec.get("grid"))
        start = _point(spec.get("start"), name="start")
        goal = _point(spec.get("goal"), name="goal")
        in_bounds = rectangular_bounds(width, height)
        if not in_bounds(start) or not in_bounds(goal):
            raise HarnessValidationError("start/goal must be inside the grid")
        blocked = _blocked_cells(spec.get("obstacle_rectangles"))
        blocked_set = frozenset(blocked)
        if start in blocked_set or goal in blocked_set:
            raise HarnessValidationError("start/goal cannot be blocked")
        if resource_budget_tiles <= 0:
            raise HarnessValidationError("resource_budget_tiles must be positive")
        return cls(
            width=width,
            height=height,
            start=start,
            goal=goal,
            blocked=blocked,
            resource_budget_tiles=int(resource_budget_tiles),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "start": [self.start.x, self.start.y],
            "goal": [self.goal.x, self.goal.y],
            "blocked": [[point.x, point.y] for point in self.blocked],
            "resource_budget_tiles": self.resource_budget_tiles,
        }


class SpatialRoutingPairedAdapter:
    """Paired-harness adapter backed by the existing weighted A* planner."""

    def __init__(self, fixture: SpatialRoutingFixture) -> None:
        self.fixture = fixture
        self.state: dict[str, Any] = {
            "adapter_version": SPATIAL_ADAPTER_VERSION,
            "fixture": fixture.to_dict(),
            "arm_history": [],
        }
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if not isinstance(checkpoint, dict):
            raise HarnessValidationError("spatial checkpoint must be a mapping")
        fixture = checkpoint.get("fixture")
        if fixture != self.fixture.to_dict():
            raise HarnessValidationError("spatial checkpoint fixture mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def _validate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        if task.get("family") != "spatial_logistics_routing":
            raise HarnessValidationError("unexpected task family")
        if task.get("partition") not in _ALLOWED_PREFLIGHT_PARTITIONS:
            raise HarnessValidationError(
                "real-adapter preflight cannot consume pilot/evaluation tasks"
            )
        if task.get("seed") is not None:
            raise HarnessValidationError(
                "real-adapter preflight cannot consume experimental seeds"
            )
        spec = task.get("spec")
        if not isinstance(spec, dict):
            raise HarnessValidationError("task spec missing")
        required = {
            "candidate_classes",
            "hard_postconditions",
            "grid",
            "start",
            "goal",
            "obstacle_rectangles",
        }
        missing = sorted(required - set(spec))
        if missing:
            raise HarnessValidationError(
                "spatial task spec missing fields: " + ", ".join(missing)
            )
        expected = SpatialRoutingFixture.from_task(
            task,
            resource_budget_tiles=self.fixture.resource_budget_tiles,
        )
        if expected != self.fixture:
            raise HarnessValidationError(
                "task geometry differs from disposable adapter fixture"
            )
        candidates = tuple(map(str, spec.get("candidate_classes") or ()))
        if "weighted_astar" not in candidates:
            raise HarnessValidationError(
                "candidate surface does not expose weighted_astar"
            )
        return spec

    def run_arm(
        self,
        task: dict[str, Any],
        memory: MemoryAccess,
        budget: HarnessBudget,
    ) -> ArmObservation:
        spec = self._validate_task(task)
        start_digest = self.state_digest()
        self.arm_start_digests.append(start_digest)

        query = MemoryQuery(
            query_id=f"real-adapter-preflight:{task.get('task_id', 'spatial')}",
            text=(
                "spatial logistics routing weighted astar belt obstacles "
                "alternate corridor detour"
            ),
            scope=ValidityScope(),
            limit=5,
        )
        retrieval = memory.retrieve(query)

        started = time.perf_counter()
        turn_cost = float(spec.get("turn_penalty", 0.0) or 0.0)
        if turn_cost < 0:
            raise HarnessValidationError("turn_penalty must be non-negative")
        blocked = frozenset(self.fixture.blocked)
        result = weighted_astar(
            self.fixture.start,
            self.fixture.goal,
            is_blocked=blocked_from(blocked),
            in_bounds=rectangular_bounds(self.fixture.width, self.fixture.height),
            weights=RoutingWeights(turn=turn_cost),
        )
        elapsed = time.perf_counter() - started

        path = () if result is None else result.path
        route_found = result is not None
        collision_free = route_found and all(
            point not in blocked
            or point in {self.fixture.start, self.fixture.goal}
            for point in path
        )
        endpoints_connected = (
            route_found
            and bool(path)
            and path[0] == self.fixture.start
            and path[-1] == self.fixture.goal
            and all(
                first.manhattan(second) == 1
                for first, second in pairwise(path)
            )
        )
        used_tiles = max(0, len(path) - 1) if route_found else None
        within_budget = (
            used_tiles is not None
            and used_tiles <= self.fixture.resource_budget_tiles
        )

        hard = {
            "route_found": bool(route_found),
            "route_collision_free": bool(collision_free),
            "endpoints_connected": bool(endpoints_connected),
            "route_within_resource_budget": bool(within_budget),
        }
        expected_hard = tuple(map(str, spec.get("hard_postconditions") or ()))
        if set(expected_hard) != set(hard):
            raise HarnessValidationError(
                "spatial hard-postcondition contract mismatch"
            )

        retrieved_ids = [
            row["memory_id"]
            for row in retrieval.to_dict().get("results", [])
            if isinstance(row, dict) and row.get("memory_id")
        ]
        memory.quarantine_write(
            {
                "kind": "real_adapter_preflight_trace",
                "adapter_version": SPATIAL_ADAPTER_VERSION,
                "task_id": task.get("task_id"),
                "condition": memory.condition,
                "retrieved_memory_ids": retrieved_ids,
                "route_found": route_found,
                "route_tiles": used_tiles,
                "route_cost": None if result is None else result.cost,
                "expanded_nodes": (
                    None if result is None else result.expanded_nodes
                ),
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "route_tiles": used_tiles,
                "retrieved_count": len(retrieval.results),
            }
        )

        return ArmObservation(
            hard_postconditions=hard,
            action_count=0,
            observed_game_ticks=0,
            invalid_or_refused_actions=0,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=elapsed,
            llm_calls=0,
            candidate_surface=tuple(map(str, spec["candidate_classes"])),
            tool_surface=SPATIAL_TOOL_SURFACE,
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )
