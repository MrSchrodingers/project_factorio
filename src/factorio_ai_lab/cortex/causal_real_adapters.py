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
from factorio_ai_lab.learning.repair_loop import (
    DEFICIT_FUEL_STARVED,
    INTENT_INSERT_FUEL,
    TOOL_RESUPPLY,
    RepairObservation,
    plan_repairs,
)
from factorio_ai_lab.planning.astar import (
    RoutingWeights,
    blocked_from,
    rectangular_bounds,
    weighted_astar,
)
from factorio_ai_lab.planning.resupply import FuelSource, plan_supply

SPATIAL_ADAPTER_VERSION = "cortex_f4c_spatial_real_adapter_v1"
SPATIAL_TOOL_SURFACE = ("planning.astar.weighted_astar",)
FUEL_ADAPTER_VERSION = "cortex_f4c_fuel_real_adapter_v1"
FUEL_TOOL_SURFACE = (
    "learning.repair_loop.plan_repairs",
    "planning.resupply.plan_supply",
)
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

@dataclass(frozen=True)
class FuelRecoveryFixture:
    """Disposable non-protocol energy-recovery world for adapter preflight."""

    energy_entity_id: str
    target_entity_id: str
    anchor: tuple[float, float]
    fuel_needed: int
    fuel_carried: int
    fuel_sources: tuple[FuelSource, ...]
    validated_capabilities: tuple[str, ...]

    @classmethod
    def from_task(cls, task: dict[str, Any]) -> FuelRecoveryFixture:
        if task.get("family") != "fuel_energy_recovery":
            raise HarnessValidationError("task family must be fuel_energy_recovery")
        if task.get("partition") not in _ALLOWED_PREFLIGHT_PARTITIONS:
            raise HarnessValidationError(
                "fuel adapter preflight accepts NON-PROTOCOL adapter_preflight only"
            )
        if task.get("seed") is not None:
            raise HarnessValidationError(
                "fuel adapter preflight forbids protocol/experimental seeds"
            )
        spec = task.get("spec")
        if not isinstance(spec, dict):
            raise HarnessValidationError("fuel task spec missing")
        required = {
            "candidate_classes",
            "hard_postconditions",
            "energy_entity_id",
            "target_entity_id",
            "anchor",
            "fuel_needed",
            "fuel_carried",
            "fuel_sources",
            "validated_capabilities",
        }
        missing = sorted(required - set(spec))
        if missing:
            raise HarnessValidationError(
                "fuel task spec missing fields: " + ", ".join(missing)
            )
        anchor_raw = spec["anchor"]
        if (
            not isinstance(anchor_raw, (list, tuple))
            or len(anchor_raw) != 2
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in anchor_raw
            )
        ):
            raise HarnessValidationError("anchor must be [number, number]")
        fuel_needed = spec["fuel_needed"]
        fuel_carried = spec["fuel_carried"]
        for name, value in (
            ("fuel_needed", fuel_needed),
            ("fuel_carried", fuel_carried),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise HarnessValidationError(f"{name} must be a non-negative integer")
        sources_raw = spec["fuel_sources"]
        if not isinstance(sources_raw, list):
            raise HarnessValidationError("fuel_sources must be a list")
        sources: list[FuelSource] = []
        for index, row in enumerate(sources_raw):
            if not isinstance(row, dict):
                raise HarnessValidationError(f"fuel_sources[{index}] must be a mapping")
            position = row.get("position")
            if (
                not isinstance(position, (list, tuple))
                or len(position) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in position
                )
            ):
                raise HarnessValidationError(
                    f"fuel_sources[{index}].position must be [number, number]"
                )
            available = row.get("available")
            if (
                isinstance(available, bool)
                or not isinstance(available, int)
                or available < 0
            ):
                raise HarnessValidationError(
                    f"fuel_sources[{index}].available must be a non-negative integer"
                )
            supplies_chain = row.get("supplies_chain", False)
            if not isinstance(supplies_chain, bool):
                raise HarnessValidationError(
                    f"fuel_sources[{index}].supplies_chain must be bool"
                )
            sources.append(
                FuelSource(
                    position=(float(position[0]), float(position[1])),
                    available=int(available),
                    supplies_chain=supplies_chain,
                )
            )
        capabilities_raw = spec["validated_capabilities"]
        if (
            not isinstance(capabilities_raw, list)
            or not capabilities_raw
            or any(not isinstance(value, str) or not value for value in capabilities_raw)
        ):
            raise HarnessValidationError(
                "validated_capabilities must be a non-empty string list"
            )
        energy_id = str(spec["energy_entity_id"])
        target_id = str(spec["target_entity_id"])
        if not energy_id or not target_id or energy_id == target_id:
            raise HarnessValidationError(
                "energy_entity_id and target_entity_id must be distinct"
            )
        return cls(
            energy_entity_id=energy_id,
            target_entity_id=target_id,
            anchor=(float(anchor_raw[0]), float(anchor_raw[1])),
            fuel_needed=int(fuel_needed),
            fuel_carried=int(fuel_carried),
            fuel_sources=tuple(sources),
            validated_capabilities=tuple(capabilities_raw),
        )

    def graph(self) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": self.energy_entity_id,
                    "name": "boiler",
                    "category": "energy",
                    "x": self.anchor[0],
                    "y": self.anchor[1],
                    "status": "no_fuel",
                },
                {
                    "id": self.target_entity_id,
                    "name": "assembling-machine-1",
                    "category": "processing",
                    "x": self.anchor[0] + 2.0,
                    "y": self.anchor[1],
                    "status": "no_power",
                },
            ],
            "edges": [],
            "metrics": {
                "entity_status_observed": True,
                "fuel_starved_entities": 1,
                "power_starved_entities": 1,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy_entity_id": self.energy_entity_id,
            "target_entity_id": self.target_entity_id,
            "anchor": list(self.anchor),
            "fuel_needed": self.fuel_needed,
            "fuel_carried": self.fuel_carried,
            "fuel_sources": [
                {
                    "position": list(source.position),
                    "available": source.available,
                    "supplies_chain": source.supplies_chain,
                }
                for source in self.fuel_sources
            ],
            "validated_capabilities": list(self.validated_capabilities),
        }


class FuelRecoveryPairedAdapter:
    """Paired harness binding over repair_loop + resupply."""

    def __init__(self, fixture: FuelRecoveryFixture) -> None:
        self.fixture = fixture
        self.state: dict[str, Any] = {
            "adapter_version": FUEL_ADAPTER_VERSION,
            "fixture": fixture.to_dict(),
            "entity_status": {
                fixture.energy_entity_id: "no_fuel",
                fixture.target_entity_id: "no_power",
            },
            "validated_capabilities": {
                name: True for name in fixture.validated_capabilities
            },
            "arm_history": [],
        }
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if not isinstance(checkpoint, dict):
            raise HarnessValidationError("fuel checkpoint must be a mapping")
        if checkpoint.get("fixture") != self.fixture.to_dict():
            raise HarnessValidationError("fuel checkpoint fixture mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def _validate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        expected = FuelRecoveryFixture.from_task(task)
        if expected != self.fixture:
            raise HarnessValidationError(
                "task fuel fixture differs from disposable adapter fixture"
            )
        spec = task["spec"]
        candidates = tuple(map(str, spec.get("candidate_classes") or ()))
        if "repair_loop_resupply" not in candidates:
            raise HarnessValidationError(
                "candidate surface does not expose repair_loop_resupply"
            )
        expected_hard = {
            "energy_dependency_resolved",
            "target_chain_resumes",
            "bootstrap_dependency_not_increased",
            "no_validated_capability_regresses",
        }
        actual_hard = set(map(str, spec.get("hard_postconditions") or ()))
        if actual_hard != expected_hard:
            raise HarnessValidationError(
                "fuel hard-postcondition contract mismatch"
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

        retrieval = memory.retrieve(
            MemoryQuery(
                query_id=f"real-adapter-preflight:{task.get('task_id', 'fuel')}",
                text=(
                    "fuel energy recovery boiler no fuel resupply "
                    "restore power target chain"
                ),
                scope=ValidityScope(),
                limit=5,
            )
        )

        started = time.perf_counter()
        repair_plan = plan_repairs(RepairObservation(graph=self.fixture.graph()))
        fuel_steps = [
            step
            for step in repair_plan.steps
            if step.deficit.kind == DEFICIT_FUEL_STARVED
            and step.action.tool == TOOL_RESUPPLY
            and step.action.intent == INTENT_INSERT_FUEL
        ]
        supply_plan = plan_supply(
            anchor=self.fixture.anchor,
            fuel_needed=self.fixture.fuel_needed,
            fuel_carried=self.fixture.fuel_carried,
            fuel_sources=self.fixture.fuel_sources,
        )
        elapsed = time.perf_counter() - started

        fuel_action_available = len(fuel_steps) == 1
        supplied = (
            not supply_plan.refused
            and self.fixture.fuel_carried + supply_plan.fuel_planned
            >= self.fixture.fuel_needed
        )
        draws_from_active_chain = any(
            draw.supplies_chain for draw in supply_plan.fuel_draws
        )
        energy_resolved = fuel_action_available and supplied
        if energy_resolved:
            self.state["entity_status"][self.fixture.energy_entity_id] = "working"
            self.state["entity_status"][self.fixture.target_entity_id] = "working"

        before_capabilities = {
            name: True for name in self.fixture.validated_capabilities
        }
        after_capabilities = dict(self.state["validated_capabilities"])
        no_regression = (
            before_capabilities == after_capabilities
            and not draws_from_active_chain
        )
        target_resumes = (
            energy_resolved
            and self.state["entity_status"][self.fixture.target_entity_id]
            == "working"
        )
        bootstrap_not_increased = not draws_from_active_chain

        hard = {
            "energy_dependency_resolved": bool(energy_resolved),
            "target_chain_resumes": bool(target_resumes),
            "bootstrap_dependency_not_increased": bool(bootstrap_not_increased),
            "no_validated_capability_regresses": bool(no_regression),
        }

        retrieved_ids = [
            row["memory_id"]
            for row in retrieval.to_dict().get("results", [])
            if isinstance(row, dict) and row.get("memory_id")
        ]
        memory.quarantine_write(
            {
                "kind": "real_adapter_preflight_trace",
                "adapter_version": FUEL_ADAPTER_VERSION,
                "task_id": task.get("task_id"),
                "condition": memory.condition,
                "retrieved_memory_ids": retrieved_ids,
                "repair_plan": repair_plan.to_dict(),
                "supply_plan": supply_plan.to_dict(),
                "hard_postconditions": hard,
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "retrieved_count": len(retrieval.results),
                "fuel_planned": supply_plan.fuel_planned,
                "refusals": list(supply_plan.refusals),
                "draws_from_active_chain": draws_from_active_chain,
            }
        )

        invalid_or_refused = int(
            not fuel_action_available or supply_plan.refused or not supplied
        )
        return ArmObservation(
            hard_postconditions=hard,
            action_count=1 if energy_resolved else 0,
            observed_game_ticks=0,
            invalid_or_refused_actions=invalid_or_refused,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=2,
            wall_clock_seconds=elapsed,
            llm_calls=0,
            candidate_surface=tuple(map(str, spec["candidate_classes"])),
            tool_surface=FUEL_TOOL_SURFACE,
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )

