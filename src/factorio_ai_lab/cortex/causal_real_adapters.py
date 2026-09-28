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

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionCondition,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ActionStatus,
    ConditionOperator,
    ConditionState,
)
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
from factorio_ai_lab.cortex.structural import DirectProcessingRecipe, ProcessingBranch
from factorio_ai_lab.cortex.structural_execute import (
    StructuralTransactionalAdapter,
    compile_structural_action,
)
from factorio_ai_lab.cortex.structural_prepare import prepare_structural_branch
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.learning.repair_loop import (
    DEFICIT_FUEL_STARVED,
    INTENT_INSERT_FUEL,
    INTENT_PLACE_PROCESSING,
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
from factorio_ai_lab.planning.delivery import (
    MODE_INSERTER,
    ArmPlacement,
    DeliveryLink,
)
from factorio_ai_lab.planning.dependency_plan import DependencyPlan, DependencyPlanner
from factorio_ai_lab.planning.placement import OUTCOME_BUILD, PlacementPlan
from factorio_ai_lab.planning.production_dag import ProductionDagPlanner
from factorio_ai_lab.planning.resupply import FuelSource, plan_supply
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

SPATIAL_ADAPTER_VERSION = "cortex_f4c_spatial_real_adapter_v1"
SPATIAL_TOOL_SURFACE = ("planning.astar.weighted_astar",)
FUEL_ADAPTER_VERSION = "cortex_f4c_fuel_real_adapter_v1"
FUEL_TOOL_SURFACE = (
    "learning.repair_loop.plan_repairs",
    "planning.resupply.plan_supply",
)
STRUCTURAL_ADAPTER_VERSION = "cortex_f4c_structural_real_adapter_v1"
STRUCTURAL_TOOL_SURFACE = (
    "cortex.structural_prepare.prepare_structural_branch",
    "cortex.structural_execute.compile_structural_action",
    "cortex.structural_execute.StructuralTransactionalAdapter",
    "integrations.fle.TransactionalFLEExecutor",
)
PRODUCTION_ADAPTER_VERSION = "cortex_f4c_production_real_adapter_v1"
PRODUCTION_TOOL_SURFACE = (
    "planning.production_dag.ProductionDagPlanner",
    "planning.dependency_plan.DependencyPlanner",
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

@dataclass(frozen=True)
class _DisposableStructuralAction:
    agent_idx: int
    code: str
    game_state: dict[str, Any] | None


def _disposable_structural_action_factory(
    agent_idx: int,
    code: str,
    game_state: dict[str, Any] | None,
) -> _DisposableStructuralAction:
    return _DisposableStructuralAction(agent_idx, code, deepcopy(game_state))


class _DisposableStructuralEnvironment:
    """Tiny checkpointed environment used only for NON-PROTOCOL preflight."""

    def __init__(
        self,
        *,
        initial_producers: int,
        initial_output: float,
        initial_dead_ends: int,
        processor_exists_after: bool,
        dead_end_delta: int,
    ) -> None:
        self.processor_exists_after = processor_exists_after
        self.dead_end_delta = dead_end_delta
        self.initial = {
            "producers_reaching_processor": int(initial_producers),
            "physical_processing_coverage": 0.5,
            "processor_exists": None,
            "processor_output": float(initial_output),
            "dead_end_count": int(initial_dead_ends),
        }
        self.state = deepcopy(self.initial)
        self.last_action: _DisposableStructuralAction | None = None

    def reset(self, *, options=None, seed=None):
        del seed
        game_state = None if options is None else options.get("game_state")
        self.state = deepcopy(self.initial if game_state is None else game_state)
        return {"state": deepcopy(self.state)}

    def step(self, action: _DisposableStructuralAction):
        self.last_action = action
        self.state["producers_reaching_processor"] += 1
        self.state["physical_processing_coverage"] = 2 / 3
        self.state["processor_exists"] = self.processor_exists_after
        self.state["processor_output"] = float(self.state["processor_output"]) + 1.0
        self.state["dead_end_count"] += self.dead_end_delta
        return (
            {"raw_text": action.code},
            1.0,
            False,
            False,
            {
                "output_game_state": deepcopy(self.state),
                "error_occurred": False,
                "ticks": 120,
            },
        )

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class StructuralRepairFixture:
    """Disposable transactional structural world for adapter preflight."""

    initial_producers_reaching_processor: int
    initial_processor_output: float
    initial_dead_end_count: int
    processor_exists_after: bool
    dead_end_delta: int

    @classmethod
    def from_task(cls, task: dict[str, Any]) -> StructuralRepairFixture:
        if task.get("family") != "structural_flow_repair":
            raise HarnessValidationError("task family must be structural_flow_repair")
        if task.get("partition") not in _ALLOWED_PREFLIGHT_PARTITIONS:
            raise HarnessValidationError(
                "structural adapter preflight accepts NON-PROTOCOL "
                "adapter_preflight only"
            )
        if task.get("seed") is not None:
            raise HarnessValidationError(
                "structural adapter preflight forbids protocol/experimental seeds"
            )
        spec = task.get("spec")
        if not isinstance(spec, dict):
            raise HarnessValidationError("structural task spec missing")
        required = {
            "candidate_classes",
            "hard_postconditions",
            "initial_producers_reaching_processor",
            "initial_processor_output",
            "initial_dead_end_count",
            "processor_exists_after",
            "dead_end_delta",
        }
        missing = sorted(required - set(spec))
        if missing:
            raise HarnessValidationError(
                "structural task spec missing fields: " + ", ".join(missing)
            )

        producers = spec["initial_producers_reaching_processor"]
        output = spec["initial_processor_output"]
        dead_ends = spec["initial_dead_end_count"]
        dead_end_delta = spec["dead_end_delta"]
        processor_exists = spec["processor_exists_after"]
        if (
            isinstance(producers, bool)
            or not isinstance(producers, int)
            or producers < 0
        ):
            raise HarnessValidationError(
                "initial_producers_reaching_processor must be non-negative int"
            )
        if (
            isinstance(output, bool)
            or not isinstance(output, (int, float))
            or float(output) < 0
        ):
            raise HarnessValidationError(
                "initial_processor_output must be non-negative number"
            )
        if (
            isinstance(dead_ends, bool)
            or not isinstance(dead_ends, int)
            or dead_ends < 0
        ):
            raise HarnessValidationError(
                "initial_dead_end_count must be non-negative int"
            )
        if (
            isinstance(dead_end_delta, bool)
            or not isinstance(dead_end_delta, int)
            or dead_end_delta < 0
        ):
            raise HarnessValidationError("dead_end_delta must be non-negative int")
        if not isinstance(processor_exists, bool):
            raise HarnessValidationError("processor_exists_after must be bool")
        return cls(
            initial_producers_reaching_processor=int(producers),
            initial_processor_output=float(output),
            initial_dead_end_count=int(dead_ends),
            processor_exists_after=processor_exists,
            dead_end_delta=int(dead_end_delta),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_producers_reaching_processor": (
                self.initial_producers_reaching_processor
            ),
            "initial_processor_output": self.initial_processor_output,
            "initial_dead_end_count": self.initial_dead_end_count,
            "processor_exists_after": self.processor_exists_after,
            "dead_end_delta": self.dead_end_delta,
        }


def _structural_dependency_plan() -> DependencyPlan:
    return DependencyPlan(
        target_item="stone-furnace",
        target_count=1.0,
        target_rate_per_s=None,
        steps=(),
        raw_requirements={},
        unresolved_requirements={},
        machine_reservations={},
        machine_requirements={},
        capacity=(),
        raw_rate_per_s=None,
        blockers=(),
        missing_technologies=(),
        raw_sources_declared=True,
        available={"stone-furnace": 1.0},
    )


def _structural_branch(task_id: str) -> ProcessingBranch:
    request = ActionRequest(
        action_id=f"{task_id}:structural",
        family=ActionFamily.PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        provenance=ActionProvenance(
            requested_by="cortex-f4c-adapter-preflight",
            source_component="causal_real_adapters",
            code_revision=STRUCTURAL_ADAPTER_VERSION,
        ),
        targets=("producer-1",),
    )
    placement = PlacementPlan(
        outcome=OUTCOME_BUILD,
        position=(30.0, 85.0),
        shift=(0, 0),
        scanned=1,
        resource_tiles=0,
        resource_cost=0.0,
        resource_names=(),
        resource_unsurveyed=0,
    )
    delivery = DeliveryLink(
        mode=MODE_INSERTER,
        lift=ArmPlacement(
            position=(28.5, 84.5),
            direction="RIGHT",
            picks_from=GridPoint(27, 84),
            drops_at=GridPoint(29, 84),
        ),
    )
    postconditions = (
        ActionCondition(
            name="producers_reaching_processor",
            operator=ConditionOperator.INCREASE,
            state=ConditionState.UNKNOWN,
            hard=True,
        ),
        ActionCondition(
            name="dead_end_count",
            operator=ConditionOperator.UNCHANGED,
            state=ConditionState.UNKNOWN,
            hard=True,
        ),
    )
    return ProcessingBranch(
        material="iron-ore",
        product="iron-plate",
        recipe=DirectProcessingRecipe(
            material="iron-ore",
            recipe_name="iron-plate",
            category="smelting",
            products=("iron-plate",),
        ),
        processor="stone-furnace",
        producers=("producer-1",),
        buffers=("buffer-1",),
        source_buffer="buffer-1",
        machine_dependency=_structural_dependency_plan(),
        placement=placement,
        delivery=delivery,
        request=request,
        preconditions=(),
        postconditions=postconditions,
    )


class StructuralFlowPairedAdapter:
    """Paired binding over the existing structural transactional stack."""

    def __init__(self, fixture: StructuralRepairFixture) -> None:
        self.fixture = fixture
        self.state: dict[str, Any] = {
            "adapter_version": STRUCTURAL_ADAPTER_VERSION,
            "fixture": fixture.to_dict(),
            "arm_history": [],
        }
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if not isinstance(checkpoint, dict):
            raise HarnessValidationError("structural checkpoint must be a mapping")
        if checkpoint.get("fixture") != self.fixture.to_dict():
            raise HarnessValidationError("structural checkpoint fixture mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def _validate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        expected = StructuralRepairFixture.from_task(task)
        if expected != self.fixture:
            raise HarnessValidationError(
                "task structural fixture differs from disposable adapter fixture"
            )
        spec = task["spec"]
        candidates = tuple(map(str, spec.get("candidate_classes") or ()))
        if "structural_transactional_processing" not in candidates:
            raise HarnessValidationError(
                "candidate surface does not expose structural_transactional_processing"
            )
        expected_hard = {
            "processor_exists",
            "producer_reaches_processor",
            "processor_output_increases",
            "no_new_dead_end",
        }
        actual_hard = set(map(str, spec.get("hard_postconditions") or ()))
        if actual_hard != expected_hard:
            raise HarnessValidationError(
                "structural hard-postcondition contract mismatch"
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
                query_id=f"real-adapter-preflight:{task.get('task_id', 'structural')}",
                text=(
                    "structural flow repair processing producer buffer "
                    "transaction rollback dead end"
                ),
                scope=ValidityScope(),
                limit=5,
            )
        )

        started = time.perf_counter()
        branch = _structural_branch(str(task.get("task_id") or "structural"))
        preparation = prepare_structural_branch(branch)
        if not preparation.ready or preparation.prepared is None:
            raise HarnessValidationError("structural branch preparation refused")
        compilation = compile_structural_action(
            preparation.prepared,
            settle_seconds=1,
        )
        if not compilation.ready or compilation.compiled is None:
            raise HarnessValidationError("structural action compilation refused")

        env = _DisposableStructuralEnvironment(
            initial_producers=self.fixture.initial_producers_reaching_processor,
            initial_output=self.fixture.initial_processor_output,
            initial_dead_ends=self.fixture.initial_dead_end_count,
            processor_exists_after=self.fixture.processor_exists_after,
            dead_end_delta=self.fixture.dead_end_delta,
        )
        tx = TransactionalFLEExecutor(
            env,
            action_factory=_disposable_structural_action_factory,
        )
        tx.reset(seed=0, game_state=deepcopy(env.initial))

        def measure(_prepared):
            return deepcopy(env.state)

        result = StructuralTransactionalAdapter().execute(
            preparation.prepared,
            authority=ActionAuthority.EXECUTE,
            executor=tx,
            measure=measure,
            settle_seconds=1,
        )
        elapsed = time.perf_counter() - started

        final = deepcopy(tx.game_state)
        before = env.initial
        committed = result.status is ActionStatus.ACCEPTED
        processor_exists = committed and final.get("processor_exists") is True
        producer_reaches = (
            committed
            and int(final.get("producers_reaching_processor", -1))
            > int(before["producers_reaching_processor"])
        )
        output_after = final.get("processor_output")
        processor_output_increases = (
            committed
            and isinstance(output_after, (int, float))
            and not isinstance(output_after, bool)
            and float(output_after) > float(before["processor_output"])
        )
        final_dead_ends = final.get("dead_end_count")
        no_new_dead_end = (
            isinstance(final_dead_ends, int)
            and not isinstance(final_dead_ends, bool)
            and final_dead_ends <= int(before["dead_end_count"])
        )
        hard = {
            "processor_exists": bool(processor_exists),
            "producer_reaches_processor": bool(producer_reaches),
            "processor_output_increases": bool(processor_output_increases),
            "no_new_dead_end": bool(no_new_dead_end),
        }

        step_ticks = result.measurements.get("executor_step_ticks")
        observed_ticks = (
            int(step_ticks)
            if isinstance(step_ticks, (int, float))
            and not isinstance(step_ticks, bool)
            and step_ticks >= 0
            else 0
        )
        retrieved_ids = [
            row["memory_id"]
            for row in retrieval.to_dict().get("results", [])
            if isinstance(row, dict) and row.get("memory_id")
        ]
        memory.quarantine_write(
            {
                "kind": "real_adapter_preflight_trace",
                "adapter_version": STRUCTURAL_ADAPTER_VERSION,
                "task_id": task.get("task_id"),
                "condition": memory.condition,
                "retrieved_memory_ids": retrieved_ids,
                "preparation_ready": preparation.ready,
                "compilation_ready": compilation.ready,
                "transaction_status": result.status.value,
                "changed_world": result.changed_world,
                "hard_postconditions": hard,
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "retrieved_count": len(retrieval.results),
                "transaction_status": result.status.value,
                "hard_postconditions": hard,
            }
        )
        return ArmObservation(
            hard_postconditions=hard,
            action_count=1 if committed else 0,
            observed_game_ticks=observed_ticks,
            invalid_or_refused_actions=0 if committed else 1,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=3,
            wall_clock_seconds=elapsed,
            llm_calls=0,
            candidate_surface=tuple(map(str, spec["candidate_classes"])),
            tool_surface=STRUCTURAL_TOOL_SURFACE,
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )

def _production_item(name: str, amount: float) -> dict[str, Any]:
    return {"name": name, "type": "item", "amount": amount}


def _production_recipe(
    name: str,
    *,
    energy: float,
    category: str,
    ingredients: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "name": name,
        "categories": [category],
        "ingredients": ingredients,
        "products": products,
        "enabled": True,
        "enabled_by_default": True,
        "hidden_from_player_crafting": False,
        "energy": energy,
    }


def _production_catalog_payload() -> dict[str, Any]:
    """Small deterministic runtime catalog for NON-PROTOCOL adapter preflight."""

    item = _production_item
    recipe = _production_recipe
    return {
        "connected": True,
        "factorio_version": "2.0.73",
        "recipes": [
            recipe(
                "iron-plate",
                energy=3.2,
                category="smelting",
                ingredients=[item("iron-ore", 1)],
                products=[item("iron-plate", 1)],
            ),
            recipe(
                "copper-plate",
                energy=3.2,
                category="smelting",
                ingredients=[item("copper-ore", 1)],
                products=[item("copper-plate", 1)],
            ),
            recipe(
                "iron-gear-wheel",
                energy=0.5,
                category="crafting",
                ingredients=[item("iron-plate", 2)],
                products=[item("iron-gear-wheel", 1)],
            ),
            recipe(
                "copper-cable",
                energy=0.5,
                category="crafting",
                ingredients=[item("copper-plate", 1)],
                products=[item("copper-cable", 2)],
            ),
            recipe(
                "electronic-circuit",
                energy=0.5,
                category="crafting",
                ingredients=[
                    item("iron-plate", 1),
                    item("copper-cable", 3),
                ],
                products=[item("electronic-circuit", 1)],
            ),
            recipe(
                "transport-belt",
                energy=0.5,
                category="crafting",
                ingredients=[
                    item("iron-plate", 1),
                    item("iron-gear-wheel", 1),
                ],
                products=[item("transport-belt", 2)],
            ),
            recipe(
                "inserter",
                energy=0.5,
                category="crafting",
                ingredients=[
                    item("iron-plate", 1),
                    item("iron-gear-wheel", 1),
                    item("electronic-circuit", 1),
                ],
                products=[item("inserter", 1)],
            ),
            recipe(
                "logistic-science-pack",
                energy=6.0,
                category="crafting",
                ingredients=[
                    item("transport-belt", 1),
                    item("inserter", 1),
                ],
                products=[item("logistic-science-pack", 1)],
            ),
        ],
        "technologies": [],
        "machines": [
            {
                "name": "assembling-machine-1",
                "type": "assembling-machine",
                "crafting_categories": ["crafting"],
                "crafting_speed": 0.5,
                "crafting_speed_status": "measured",
                "mining_speed_status": "absent",
            },
            {
                "name": "stone-furnace",
                "type": "furnace",
                "crafting_categories": ["smelting"],
                "crafting_speed": 1.0,
                "crafting_speed_status": "measured",
                "mining_speed_status": "absent",
            },
        ],
        "belts": [],
    }


@dataclass(frozen=True)
class ProductionTransitionFixture:
    """Read-only production transition fixture for adapter preflight."""

    target_item: str
    target_count: int
    target_rate_per_s: float
    material_budget: tuple[tuple[str, float], ...]
    available: tuple[tuple[str, float], ...]
    raw_sources: tuple[str, ...]
    validated_capabilities: tuple[str, ...]
    catalog_profile: str

    @classmethod
    def from_task(cls, task: dict[str, Any]) -> ProductionTransitionFixture:
        if task.get("family") != "production_transition_planning":
            raise HarnessValidationError(
                "task family must be production_transition_planning"
            )
        if task.get("partition") not in _ALLOWED_PREFLIGHT_PARTITIONS:
            raise HarnessValidationError(
                "production adapter preflight accepts NON-PROTOCOL "
                "adapter_preflight only"
            )
        if task.get("seed") is not None:
            raise HarnessValidationError(
                "production adapter preflight forbids protocol/experimental seeds"
            )
        spec = task.get("spec")
        if not isinstance(spec, dict):
            raise HarnessValidationError("production task spec missing")
        required = {
            "candidate_classes",
            "hard_postconditions",
            "target_item",
            "target_count",
            "target_rate_per_s",
            "material_budget",
            "available",
            "raw_sources",
            "validated_capabilities",
            "catalog_profile",
        }
        missing = sorted(required - set(spec))
        if missing:
            raise HarnessValidationError(
                "production task spec missing fields: " + ", ".join(missing)
            )

        target_item = spec["target_item"]
        if not isinstance(target_item, str) or not target_item:
            raise HarnessValidationError("target_item must be a non-empty string")
        target_count = spec["target_count"]
        if (
            isinstance(target_count, bool)
            or not isinstance(target_count, int)
            or target_count <= 0
        ):
            raise HarnessValidationError("target_count must be a positive integer")
        target_rate = spec["target_rate_per_s"]
        if (
            isinstance(target_rate, bool)
            or not isinstance(target_rate, (int, float))
            or float(target_rate) <= 0
        ):
            raise HarnessValidationError(
                "target_rate_per_s must be a positive number"
            )

        def numeric_mapping(name: str) -> tuple[tuple[str, float], ...]:
            raw = spec[name]
            if not isinstance(raw, dict) or not raw:
                raise HarnessValidationError(f"{name} must be a non-empty mapping")
            rows: list[tuple[str, float]] = []
            for key, value in raw.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or float(value) < 0
                ):
                    raise HarnessValidationError(
                        f"{name} must map non-empty strings to non-negative numbers"
                    )
                rows.append((key, float(value)))
            return tuple(sorted(rows))

        material_budget = numeric_mapping("material_budget")
        available = numeric_mapping("available")
        raw_sources_raw = spec["raw_sources"]
        if (
            not isinstance(raw_sources_raw, list)
            or not raw_sources_raw
            or any(
                not isinstance(value, str) or not value
                for value in raw_sources_raw
            )
        ):
            raise HarnessValidationError("raw_sources must be a non-empty string list")
        capabilities_raw = spec["validated_capabilities"]
        if (
            not isinstance(capabilities_raw, list)
            or not capabilities_raw
            or any(
                not isinstance(value, str) or not value
                for value in capabilities_raw
            )
        ):
            raise HarnessValidationError(
                "validated_capabilities must be a non-empty string list"
            )
        profile = spec["catalog_profile"]
        if profile != "minimal_logistic_science_v1":
            raise HarnessValidationError("unsupported production catalog profile")
        budget_names = {name for name, _ in material_budget}
        raw_sources = tuple(sorted(set(raw_sources_raw)))
        missing_budget = sorted(set(raw_sources) - budget_names)
        if missing_budget:
            raise HarnessValidationError(
                "material_budget missing declared raw source(s): "
                + ", ".join(missing_budget)
            )
        return cls(
            target_item=target_item,
            target_count=int(target_count),
            target_rate_per_s=float(target_rate),
            material_budget=material_budget,
            available=available,
            raw_sources=raw_sources,
            validated_capabilities=tuple(capabilities_raw),
            catalog_profile=profile,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_item": self.target_item,
            "target_count": self.target_count,
            "target_rate_per_s": self.target_rate_per_s,
            "material_budget": dict(self.material_budget),
            "available": dict(self.available),
            "raw_sources": list(self.raw_sources),
            "validated_capabilities": list(self.validated_capabilities),
            "catalog_profile": self.catalog_profile,
        }


def _production_dependency_order_valid(dag: Any, plan: DependencyPlan) -> bool:
    dag_order = {node.item: index for index, node in enumerate(dag.nodes)}
    for node in dag.nodes:
        consumer = dag_order[node.item]
        for ingredient in node.recipe.ingredients:
            dependency = dag_order.get(ingredient.item)
            if dependency is not None and dependency >= consumer:
                return False

    plan_order = {step.item: index for index, step in enumerate(plan.steps)}
    for step in plan.steps:
        for consumer in step.required_by:
            consumer_index = plan_order.get(consumer)
            if consumer_index is not None and plan_order[step.item] >= consumer_index:
                return False
    return bool(dag.nodes) and dag.nodes[-1].item == dag.target_item


class ProductionTransitionPairedAdapter:
    """Paired binding over ProductionDagPlanner + DependencyPlanner."""

    def __init__(self, fixture: ProductionTransitionFixture) -> None:
        self.fixture = fixture
        self.state: dict[str, Any] = {
            "adapter_version": PRODUCTION_ADAPTER_VERSION,
            "fixture": fixture.to_dict(),
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
            raise HarnessValidationError("production checkpoint must be a mapping")
        if checkpoint.get("fixture") != self.fixture.to_dict():
            raise HarnessValidationError("production checkpoint fixture mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def _validate_task(self, task: dict[str, Any]) -> dict[str, Any]:
        expected = ProductionTransitionFixture.from_task(task)
        if expected != self.fixture:
            raise HarnessValidationError(
                "task production fixture differs from disposable adapter fixture"
            )
        spec = task["spec"]
        candidates = tuple(map(str, spec.get("candidate_classes") or ()))
        if "production_dag_dependency_plan" not in candidates:
            raise HarnessValidationError(
                "candidate surface does not expose production_dag_dependency_plan"
            )
        expected_hard = {
            "required_material_budget_satisfied",
            "dependency_order_valid",
            "target_stage_functional",
            "no_validated_capability_regresses",
        }
        actual_hard = set(map(str, spec.get("hard_postconditions") or ()))
        if actual_hard != expected_hard:
            raise HarnessValidationError(
                "production hard-postcondition contract mismatch"
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
                query_id=f"real-adapter-preflight:{task.get('task_id', 'production')}",
                text=(
                    "production transition dependency dag material budget "
                    "logistic science machine capacity"
                ),
                scope=ValidityScope(),
                limit=5,
            )
        )

        started = time.perf_counter()
        catalog = RuntimeFactorioCatalog(_production_catalog_payload())
        dag = ProductionDagPlanner(
            catalog.recipe_provider,
            raw_items=set(self.fixture.raw_sources),
        ).plan(
            self.fixture.target_item,
            self.fixture.target_rate_per_s,
        )
        plan = DependencyPlanner(catalog, researched=()).plan(
            self.fixture.target_item,
            self.fixture.target_count,
            rate_per_s=self.fixture.target_rate_per_s,
            available=dict(self.fixture.available),
            raw_sources=self.fixture.raw_sources,
        )
        elapsed = time.perf_counter() - started

        material_budget = dict(self.fixture.material_budget)
        material_budget_satisfied = all(
            name in material_budget
            and float(material_budget[name]) + 1e-12 >= float(required)
            for name, required in plan.raw_requirements.items()
        )
        dependency_order_valid = _production_dependency_order_valid(dag, plan)
        target_capacity = plan.capacity_for(self.fixture.target_item)
        target_stage_functional = (
            plan.feasible
            and not plan.unresolved_requirements
            and dag.node(self.fixture.target_item) is not None
            and plan.step(self.fixture.target_item) is not None
            and target_capacity is not None
            and target_capacity.machines is not None
            and target_capacity.machines > 0
        )
        before_capabilities = {
            name: True for name in self.fixture.validated_capabilities
        }
        no_regression = self.state["validated_capabilities"] == before_capabilities
        hard = {
            "required_material_budget_satisfied": bool(
                material_budget_satisfied
            ),
            "dependency_order_valid": bool(dependency_order_valid),
            "target_stage_functional": bool(target_stage_functional),
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
                "adapter_version": PRODUCTION_ADAPTER_VERSION,
                "task_id": task.get("task_id"),
                "condition": memory.condition,
                "retrieved_memory_ids": retrieved_ids,
                "dag": dag.to_dict(),
                "dependency_plan": plan.as_dict(),
                "material_budget": material_budget,
                "hard_postconditions": hard,
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "retrieved_count": len(retrieval.results),
                "raw_requirements": dict(plan.raw_requirements),
                "hard_postconditions": hard,
            }
        )
        return ArmObservation(
            hard_postconditions=hard,
            action_count=0,
            observed_game_ticks=0,
            invalid_or_refused_actions=0,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=2,
            wall_clock_seconds=elapsed,
            llm_calls=0,
            candidate_surface=tuple(map(str, spec["candidate_classes"])),
            tool_surface=PRODUCTION_TOOL_SURFACE,
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )

