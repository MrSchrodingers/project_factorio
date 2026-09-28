"""Deterministic F4-C protocol task-world runtime.

The frozen F4-C tasks are abstract transfer benchmarks.  This runtime executes
those task specifications against project planners without touching the live
Factorio world.  It is the execution substrate used by the pilot/evaluation
pair runner; RCON, FLE environments, leases, grants and continuous authority
are intentionally absent.

Every memory condition sees the same task, candidate surface, tool surface and
budget.  Memory can affect only candidate ranking through causal_treatment.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import pairwise
from math import ceil
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    OUTCOME_EXTRACTOR_VERSION,
    ArmObservation,
    HarnessBudget,
    HarnessValidationError,
    MemoryAccess,
    checkpoint_digest,
)
from factorio_ai_lab.cortex.causal_real_adapters import (
    _production_catalog_payload,
    _production_dependency_order_valid,
)
from factorio_ai_lab.cortex.causal_treatment import (
    CANDIDATE_BINDINGS,
    rank_candidates,
    task_query_text,
    treatment_retrieval_limit,
)
from factorio_ai_lab.cortex.memory import ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import MemoryQuery
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.repair_loop import (
    RepairObservation,
    plan_repairs,
)
from factorio_ai_lab.planning.astar import (
    RoutingWeights,
    blocked_from,
    rectangular_bounds,
    weighted_astar,
)
from factorio_ai_lab.planning.dependency_plan import DependencyPlanner
from factorio_ai_lab.planning.production_dag import ProductionDagPlanner
from factorio_ai_lab.planning.progression import EARLY_GAME_ENGINEERING_GOALS
from factorio_ai_lab.planning.resupply import FuelSource, plan_supply
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

RUNTIME_VERSION = "cortex_f4c_protocol_task_runtime_v1"
ALLOWED_PARTITIONS = frozenset({"runner_preflight", "pilot"})
STAGE_TARGETS = {
    "electric_mining": "electric-mining-drill",
    "automation_science": "automation-science-pack",
    "logistic_science": "logistic-science-pack",
}

FAMILY_TOOL_SURFACES = {
    "structural_flow_repair": (
        "learning.repair_loop.plan_repairs",
        "cortex.structural_prepare.prepare_structural_branch",
        "cortex.structural_execute.StructuralTransactionalAdapter",
        "planning.rebuild.plan_rebuild",
        RUNTIME_VERSION,
    ),
    "fuel_energy_recovery": (
        "learning.repair_loop.plan_repairs",
        "planning.resupply.plan_supply",
        "planning.progression.EARLY_GAME_ENGINEERING_GOALS",
        RUNTIME_VERSION,
    ),
    "spatial_logistics_routing": (
        "planning.astar.weighted_astar",
        "protocol_runtime.underground_detour_abstraction",
        RUNTIME_VERSION,
    ),
    "production_transition_planning": (
        "planning.production_dag.ProductionDagPlanner",
        "planning.dependency_plan.DependencyPlanner",
        "planning.progression.EARLY_GAME_ENGINEERING_GOALS",
        RUNTIME_VERSION,
    ),
}


@dataclass(frozen=True)
class CandidateRuntimeResult:
    hard_postconditions: dict[str, bool]
    action_count: int
    observed_game_ticks: int
    invalid_or_refused_actions: int
    proposed_actions: int
    decisions: int
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_spec(task: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    if task.get("family") != family:
        raise HarnessValidationError(
            f"expected family {family}, got {task.get('family')!r}"
        )
    if task.get("partition") not in ALLOWED_PARTITIONS:
        raise HarnessValidationError(
            "protocol task runtime accepts runner_preflight or pilot only"
        )
    if task.get("partition") == "runner_preflight" and task.get("seed") is not None:
        raise HarnessValidationError("runner_preflight must not carry a seed")
    if task.get("partition") == "pilot":
        seed = task.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise HarnessValidationError("pilot task requires an integer seed")
    spec = task.get("spec")
    if not isinstance(spec, Mapping):
        raise HarnessValidationError("task spec missing")
    return spec


def _bounded_result(
    hard: dict[str, bool],
    *,
    actions: int,
    ticks: int,
    decisions: int,
    budget: HarnessBudget,
    metadata: dict[str, Any],
) -> CandidateRuntimeResult:
    over = (
        actions > budget.max_actions
        or ticks > budget.max_game_ticks
        or decisions > budget.max_decisions
    )
    if over:
        hard = {key: False for key in hard}
    return CandidateRuntimeResult(
        hard_postconditions=hard,
        action_count=max(0, int(actions)),
        observed_game_ticks=max(0, int(ticks)),
        invalid_or_refused_actions=1 if over else 0,
        proposed_actions=max(1, int(actions)),
        decisions=max(1, int(decisions)),
        metadata={**metadata, "budget_overrun": over},
    )


def _structural_kernel(task: Mapping[str, Any]) -> dict[str, Any]:
    spec = _require_spec(task, "structural_flow_repair")
    producer_count = int(spec["producer_count"])
    blocked_links = int(spec["blocked_links"])
    observation = RepairObservation(
        graph={
            "metrics": {
                "isolated_producers": 0,
                "producer_count": producer_count,
                "producers_reaching_processor": max(
                    0,
                    producer_count - blocked_links,
                ),
            }
        }
    )
    plan = plan_repairs(observation)
    candidates = {
        action.key
        for proposal in plan.proposals
        for action in proposal.candidates
    }
    return {
        "repair_plan": plan.to_dict(),
        "kernel_candidate_keys": sorted(candidates),
    }


def _structural_result(
    task: Mapping[str, Any],
    candidate: str,
    budget: HarnessBudget,
) -> CandidateRuntimeResult:
    spec = _require_spec(task, "structural_flow_repair")
    kernel = _structural_kernel(task)
    blocked = int(spec["blocked_links"])
    producers = int(spec["producer_count"])
    distance = int(spec["sink_distance_tiles"])
    buffered = int(spec["buffered_output_units"])
    pressure = str(spec["inventory_pressure"])

    if candidate == "placement_processing":
        actions = 2 + blocked
        ticks = (distance + blocked) * 60
        kernel_ok = (
            "placement:place_processing_for_buffered_output"
            in kernel["kernel_candidate_keys"]
        )
    elif candidate == "reroute_logistics":
        actions = 1 + blocked
        ticks = (distance + 2 * blocked) * 45
        kernel_ok = (
            "rebuild:reroute_producer_logistics"
            in kernel["kernel_candidate_keys"]
        )
    elif candidate == "rebuild_segment":
        actions = 2 + 2 * blocked
        ticks = (distance + producers + blocked) * 75
        kernel_ok = (
            "rebuild:reroute_producer_logistics"
            in kernel["kernel_candidate_keys"]
        )
    else:
        raise HarnessValidationError(f"unknown structural candidate: {candidate}")

    enough_work = buffered > 0 and blocked > 0
    pressure_ok = not (candidate == "rebuild_segment" and pressure == "high")
    success = kernel_ok and enough_work and pressure_ok
    hard = {
        "processor_exists": success,
        "producer_reaches_processor": success,
        "processor_output_increases": success,
        "no_new_dead_end": success,
    }
    return _bounded_result(
        hard,
        actions=actions,
        ticks=ticks,
        decisions=2,
        budget=budget,
        metadata={
            "candidate": candidate,
            "blocked_links": blocked,
            "producer_count": producers,
            "buffered_output_units": buffered,
            "inventory_pressure": pressure,
            **kernel,
        },
    )


def _fuel_result(
    task: Mapping[str, Any],
    candidate: str,
    budget: HarnessBudget,
) -> CandidateRuntimeResult:
    spec = _require_spec(task, "fuel_energy_recovery")
    burners = int(spec["burner_entities"])
    local = int(spec["local_fuel_units"])
    distance = int(spec["coal_distance_tiles"])
    demand_kw = int(spec["power_demand_kw"])
    pressure = str(spec["bootstrap_inventory_pressure"])
    fuel_needed = max(1, 2 * burners)
    anchor = (0.0, 0.0)

    if candidate == "local_refuel":
        supply = plan_supply(
            anchor=anchor,
            fuel_needed=fuel_needed,
            fuel_sources=(
                FuelSource((1.0, 0.0), local, False),
            ),
        )
        supplied = not supply.refused and supply.fuel_planned >= fuel_needed
        actions = 1
        ticks = 60
        no_regression = True
        metadata = {"supply_plan": supply.to_dict()}
    elif candidate == "route_endogenous_fuel":
        supply = plan_supply(
            anchor=anchor,
            fuel_needed=fuel_needed,
            fuel_sources=(
                FuelSource((float(distance), 0.0), fuel_needed, False),
            ),
        )
        supplied = not supply.refused and supply.fuel_planned >= fuel_needed
        actions = 2
        ticks = max(60, distance * 60)
        no_regression = True
        metadata = {"supply_plan": supply.to_dict()}
    elif candidate == "establish_power_generation":
        steam_goal = next(
            goal
            for goal in EARLY_GAME_ENGINEERING_GOALS
            if goal.goal_id == "steam_power"
        )
        engines = max(1, ceil(demand_kw / 900.0))
        actions = 2 + 3 * engines
        ticks = distance * 60 + actions * 120
        supplied = demand_kw <= engines * 900
        no_regression = pressure != "constrained"
        metadata = {
            "engineering_goal": steam_goal.goal_id,
            "engines_required": engines,
            "estimated_capacity_kw": engines * 900,
        }
    else:
        raise HarnessValidationError(f"unknown fuel candidate: {candidate}")

    energy = supplied
    hard = {
        "energy_dependency_resolved": energy,
        "target_chain_resumes": energy,
        "bootstrap_dependency_not_increased": no_regression,
        "no_validated_capability_regresses": no_regression,
    }
    return _bounded_result(
        hard,
        actions=actions,
        ticks=ticks,
        decisions=2,
        budget=budget,
        metadata={
            "candidate": candidate,
            "fuel_needed": fuel_needed,
            "local_fuel_units": local,
            "coal_distance_tiles": distance,
            "power_demand_kw": demand_kw,
            **metadata,
        },
    )


def _spatial_blocked(spec: Mapping[str, Any]) -> frozenset[GridPoint]:
    blocked: set[GridPoint] = set()
    for rectangle in spec["obstacle_rectangles"]:
        x, y, width, height = map(int, rectangle)
        for px in range(x, x + width):
            for py in range(y, y + height):
                blocked.add(GridPoint(px, py))
    return frozenset(blocked)


def _tunnel_tiles(
    blocked: frozenset[GridPoint],
    start: GridPoint,
    goal: GridPoint,
    budget: int,
) -> frozenset[GridPoint]:
    ranked = sorted(
        blocked,
        key=lambda point: (
            start.manhattan(point) + point.manhattan(goal),
            point.y,
            point.x,
        ),
    )
    return frozenset(ranked[: max(0, int(budget))])


def _spatial_result(
    task: Mapping[str, Any],
    candidate: str,
    budget: HarnessBudget,
) -> CandidateRuntimeResult:
    spec = _require_spec(task, "spatial_logistics_routing")
    width, height = map(int, spec["grid"])
    start = GridPoint(*map(int, spec["start"]))
    goal = GridPoint(*map(int, spec["goal"]))
    blocked = _spatial_blocked(spec)
    underground_budget = int(spec["underground_budget"])
    turn_penalty = float(spec["turn_penalty"])
    tunnels: frozenset[GridPoint] = frozenset()
    extra_cost: Callable[[GridPoint], float] | None = None

    if candidate == "weighted_astar":
        pass
    elif candidate == "alternate_corridor":
        near_blocked = {
            GridPoint(point.x + dx, point.y + dy)
            for point in blocked
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
        } - set(blocked)
        extra_cost = lambda point: 0.35 if point in near_blocked else 0.0
    elif candidate == "detour_with_underground":
        tunnels = _tunnel_tiles(
            blocked,
            start,
            goal,
            underground_budget,
        )
    else:
        raise HarnessValidationError(f"unknown spatial candidate: {candidate}")

    effective_blocked = blocked - tunnels
    route = weighted_astar(
        start,
        goal,
        is_blocked=blocked_from(effective_blocked),
        in_bounds=rectangular_bounds(width, height),
        weights=RoutingWeights(turn=turn_penalty),
        extra_cost=extra_cost,
    )
    path = () if route is None else route.path
    found = route is not None
    collision_free = found and all(
        point not in effective_blocked
        or point in {start, goal}
        for point in path
    )
    connected = (
        found
        and bool(path)
        and path[0] == start
        and path[-1] == goal
        and all(a.manhattan(b) == 1 for a, b in pairwise(path))
    )
    path_tiles = max(0, len(path) - 1) if found else 0
    resource_budget = start.manhattan(goal) + 2 * underground_budget
    effective_cost_tiles = path_tiles + 2 * len(tunnels)
    within_budget = found and effective_cost_tiles <= resource_budget
    hard = {
        "route_found": found,
        "route_collision_free": collision_free,
        "endpoints_connected": connected,
        "route_within_resource_budget": within_budget,
    }
    actions = 1 + len(tunnels)
    expanded = 0 if route is None else int(route.expanded_nodes)
    ticks = path_tiles * 60 + min(expanded, 3000)
    return _bounded_result(
        hard,
        actions=actions,
        ticks=ticks,
        decisions=1,
        budget=budget,
        metadata={
            "candidate": candidate,
            "path_tiles": path_tiles,
            "route_cost": None if route is None else route.cost,
            "expanded_nodes": expanded,
            "tunnel_tiles": [
                {"x": point.x, "y": point.y}
                for point in sorted(tunnels, key=lambda p: (p.y, p.x))
            ],
            "resource_budget_tiles": resource_budget,
            "effective_cost_tiles": effective_cost_tiles,
        },
    )


def _pilot_catalog_payload() -> dict[str, Any]:
    payload = deepcopy(_production_catalog_payload())

    def item(name: str, amount: float) -> dict[str, Any]:
        return {"name": name, "type": "item", "amount": amount}

    def recipe(
        name: str,
        ingredients: list[dict[str, Any]],
        products: list[dict[str, Any]],
        *,
        energy: float,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "categories": ["crafting"],
            "ingredients": ingredients,
            "products": products,
            "enabled": True,
            "enabled_by_default": True,
            "hidden_from_player_crafting": False,
            "energy": energy,
        }

    payload["recipes"].extend(
        [
            recipe(
                "automation-science-pack",
                [item("copper-plate", 1), item("iron-gear-wheel", 1)],
                [item("automation-science-pack", 1)],
                energy=5.0,
            ),
            recipe(
                "electric-mining-drill",
                [
                    item("electronic-circuit", 3),
                    item("iron-gear-wheel", 5),
                    item("iron-plate", 10),
                ],
                [item("electric-mining-drill", 1)],
                energy=2.0,
            ),
        ]
    )
    return payload


def _production_reserves(
    candidate: str,
    margin: int,
) -> tuple[int, int]:
    if candidate == "buffer_first":
        return margin, margin
    if candidate == "power_first":
        return 2 * margin, margin
    if candidate == "route_first":
        return margin, max(1, margin // 2)
    if candidate == "craft_chain_first":
        return 0, 0
    raise HarnessValidationError(f"unknown production candidate: {candidate}")


def _production_result(
    task: Mapping[str, Any],
    candidate: str,
    budget: HarnessBudget,
) -> CandidateRuntimeResult:
    spec = _require_spec(task, "production_transition_planning")
    target_stage = str(spec["target_stage"])
    target_item = STAGE_TARGETS.get(target_stage)
    if target_item is None:
        raise HarnessValidationError(f"unknown target stage: {target_stage}")

    margin = int(spec["required_buffer_margin"])
    iron_reserve, copper_reserve = _production_reserves(candidate, margin)
    available = {
        "iron-ore": max(0.0, float(spec["iron_available"]) - iron_reserve),
        "copper-ore": max(
            0.0,
            float(spec["copper_available"]) - copper_reserve,
        ),
        "coal": float(spec["coal_available"]),
        "wood": float(spec["wood_available"]),
        "assembling-machine-1": 20.0,
        "stone-furnace": 20.0,
    }

    catalog = RuntimeFactorioCatalog(_pilot_catalog_payload())
    dag = ProductionDagPlanner(
        catalog.recipe_provider,
        raw_items={"iron-ore", "copper-ore", "coal", "wood"},
    ).plan(target_item, 0.1)
    plan = DependencyPlanner(catalog, researched=()).plan(
        target_item,
        1,
        rate_per_s=0.1,
        available=available,
        raw_sources=("iron-ore", "copper-ore", "coal", "wood"),
    )
    budget_satisfied = not plan.raw_requirements
    order_valid = _production_dependency_order_valid(dag, plan)
    target_capacity = plan.capacity_for(target_item)
    functional = (
        plan.feasible
        and budget_satisfied
        and order_valid
        and plan.step(target_item) is not None
        and target_capacity is not None
        and target_capacity.machines is not None
        and target_capacity.machines > 0
    )
    hard = {
        "required_material_budget_satisfied": budget_satisfied,
        "dependency_order_valid": order_valid,
        "target_stage_functional": functional,
        "no_validated_capability_regresses": True,
    }
    actions = max(1, len(plan.steps))
    ticks = actions * 180 + (iron_reserve + copper_reserve) * 10
    return _bounded_result(
        hard,
        actions=actions,
        ticks=ticks,
        decisions=2,
        budget=budget,
        metadata={
            "candidate": candidate,
            "target_stage": target_stage,
            "target_item": target_item,
            "iron_reserve": iron_reserve,
            "copper_reserve": copper_reserve,
            "available": available,
            "dag": dag.to_dict(),
            "dependency_plan": plan.as_dict(),
        },
    )


CandidateExecutor = Callable[
    [Mapping[str, Any], str, HarnessBudget],
    CandidateRuntimeResult,
]

PROTOCOL_EXECUTORS: dict[str, CandidateExecutor] = {
    "placement_processing": _structural_result,
    "reroute_logistics": _structural_result,
    "rebuild_segment": _structural_result,
    "local_refuel": _fuel_result,
    "route_endogenous_fuel": _fuel_result,
    "establish_power_generation": _fuel_result,
    "weighted_astar": _spatial_result,
    "detour_with_underground": _spatial_result,
    "alternate_corridor": _spatial_result,
    "buffer_first": _production_result,
    "power_first": _production_result,
    "route_first": _production_result,
    "craft_chain_first": _production_result,
}


def execute_candidate(
    task: Mapping[str, Any],
    candidate: str,
    budget: HarnessBudget,
) -> CandidateRuntimeResult:
    executor = PROTOCOL_EXECUTORS.get(candidate)
    if executor is None:
        raise HarnessValidationError(
            f"no protocol executor for candidate {candidate!r}"
        )
    binding = CANDIDATE_BINDINGS.get(candidate)
    if binding is None or binding.family != task.get("family"):
        raise HarnessValidationError(
            f"candidate {candidate!r} does not belong to task family"
        )
    return executor(task, candidate, budget)


class ProtocolTaskWorldAdapter:
    """Paired task-world adapter for pilot execution."""

    def __init__(self, task: Mapping[str, Any]) -> None:
        family = str(task.get("family") or "")
        if family not in FAMILY_TOOL_SURFACES:
            raise HarnessValidationError(f"unknown protocol family: {family}")
        self.task = deepcopy(dict(task))
        self.state: dict[str, Any] = {
            "runtime_version": RUNTIME_VERSION,
            "task_id": self.task.get("task_id"),
            "family": family,
            "arm_history": [],
        }
        self.arm_start_digests: list[str] = []

    def capture_checkpoint(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        if not isinstance(checkpoint, dict):
            raise HarnessValidationError("protocol checkpoint must be a mapping")
        if checkpoint.get("task_id") != self.task.get("task_id"):
            raise HarnessValidationError("protocol checkpoint task mismatch")
        self.state = deepcopy(checkpoint)

    def state_digest(self) -> str:
        return checkpoint_digest(self.state)

    def run_arm(
        self,
        task: dict[str, Any],
        memory: MemoryAccess,
        budget: HarnessBudget,
    ) -> ArmObservation:
        if task != self.task:
            raise HarnessValidationError("adapter task differs from frozen task")
        spec = task.get("spec")
        if not isinstance(spec, Mapping):
            raise HarnessValidationError("task spec missing")
        candidates = tuple(map(str, spec.get("candidate_classes") or ()))
        expected_hard = tuple(map(str, spec.get("hard_postconditions") or ()))
        if not candidates or not expected_hard:
            raise HarnessValidationError("candidate/hard surface missing")
        if any(candidate not in PROTOCOL_EXECUTORS for candidate in candidates):
            raise HarnessValidationError("candidate surface not fully executable")

        self.arm_start_digests.append(self.state_digest())
        retrieval = memory.retrieve(
            MemoryQuery(
                query_id=f"protocol:{task['task_id']}:{memory.condition}",
                text=task_query_text(task),
                scope=ValidityScope(),
                limit=treatment_retrieval_limit(candidates),
            )
        )
        decision = rank_candidates(
            candidates,
            retrieval.to_dict()["results"],
        )
        if decision.selected is None:
            raise HarnessValidationError("treatment selected no candidate")

        started = time.perf_counter()
        result = execute_candidate(task, decision.selected, budget)
        elapsed = time.perf_counter() - started
        if set(result.hard_postconditions) != set(expected_hard):
            raise HarnessValidationError(
                "candidate result hard-postcondition surface mismatch"
            )

        memory.quarantine_write(
            {
                "kind": "f4c_protocol_arm_trace",
                "runtime_version": RUNTIME_VERSION,
                "task_id": task["task_id"],
                "condition": memory.condition,
                "treatment": decision.to_dict(),
                "candidate_result": result.to_dict(),
            }
        )
        self.state["arm_history"].append(
            {
                "condition": memory.condition,
                "selected": decision.selected,
                "memory_influence": decision.memory_influence,
                "hard_postconditions": dict(result.hard_postconditions),
            }
        )
        return ArmObservation(
            hard_postconditions=dict(result.hard_postconditions),
            action_count=result.action_count,
            observed_game_ticks=result.observed_game_ticks,
            invalid_or_refused_actions=result.invalid_or_refused_actions,
            proposed_actions=result.proposed_actions,
            initially_unsatisfied=True,
            decisions=result.decisions,
            wall_clock_seconds=elapsed,
            llm_calls=0,
            candidate_surface=candidates,
            tool_surface=FAMILY_TOOL_SURFACES[str(task["family"])],
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )


def runtime_contract() -> dict[str, Any]:
    return {
        "runtime_version": RUNTIME_VERSION,
        "allowed_partitions": sorted(ALLOWED_PARTITIONS),
        "candidate_executors": sorted(PROTOCOL_EXECUTORS),
        "family_tool_surfaces": {
            key: list(value)
            for key, value in sorted(FAMILY_TOOL_SURFACES.items())
        },
        "live_factorio_world": False,
        "rcon": False,
        "fle_environment": False,
        "continuous_authority": False,
    }

def pilot_task_from_manifest(
    manifest: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    pilot_seeds = tuple(int(value) for value in manifest["seed_partitions"]["pilot"])
    if int(seed) not in pilot_seeds:
        raise HarnessValidationError(
            f"seed {seed} is not in the frozen pilot partition"
        )
    tasks = manifest["task_design"]["pilot_tasks"]
    matches = [task for task in tasks if int(task["seed"]) == int(seed)]
    if len(matches) != 1:
        raise HarnessValidationError(
            f"pilot seed {seed} maps to {len(matches)} tasks"
        )
    task = deepcopy(matches[0])
    if task.get("partition") != "pilot":
        raise HarnessValidationError("frozen pilot task has wrong partition")
    return task


def pilot_arm_order(
    manifest: Mapping[str, Any],
    task: Mapping[str, Any],
) -> tuple[str, str]:
    """Counterbalance the eight pilot pairs 4/4 without touching evaluation."""

    from factorio_ai_lab.cortex.causal_protocol import MEMORY_ABLATED, MEMORY_ON

    tasks = manifest["task_design"]["pilot_tasks"]
    task_id = task.get("task_id")
    indexes = [
        index
        for index, row in enumerate(tasks)
        if row.get("task_id") == task_id
    ]
    if len(indexes) != 1:
        raise HarnessValidationError("pilot task is not uniquely frozen")
    if indexes[0] % 2 == 0:
        return MEMORY_ON, MEMORY_ABLATED
    return MEMORY_ABLATED, MEMORY_ON


def runner_preflight_task(task: Mapping[str, Any]) -> dict[str, Any]:
    clone = deepcopy(dict(task))
    clone["task_id"] = (
        "runner-preflight:"
        + str(task.get("family"))
        + ":"
        + str(task.get("task_id")).rsplit(":", 1)[-1]
    )
    clone["partition"] = "runner_preflight"
    clone["seed"] = None
    return clone

def runner_validation_tasks() -> tuple[dict[str, Any], ...]:
    """Independent NON-PROTOCOL fixtures for pilot-runner validation."""

    return (
        {
            "task_id": "runner-preflight:structural:independent-v1",
            "partition": "runner_preflight",
            "family": "structural_flow_repair",
            "seed": None,
            "generator_version": "cortex_f4c_runner_preflight_v1",
            "spec": {
                "topology": "branched",
                "producer_count": 3,
                "sink_distance_tiles": 12,
                "buffered_output_units": 40,
                "blocked_links": 1,
                "inventory_pressure": "medium",
                "candidate_classes": [
                    "placement_processing",
                    "reroute_logistics",
                    "rebuild_segment",
                ],
                "hard_postconditions": [
                    "processor_exists",
                    "producer_reaches_processor",
                    "processor_output_increases",
                    "no_new_dead_end",
                ],
            },
        },
        {
            "task_id": "runner-preflight:fuel:independent-v1",
            "partition": "runner_preflight",
            "family": "fuel_energy_recovery",
            "seed": None,
            "generator_version": "cortex_f4c_runner_preflight_v1",
            "spec": {
                "terminal_condition": "mixed_energy_deficit",
                "burner_entities": 3,
                "local_fuel_units": 2,
                "coal_distance_tiles": 10,
                "power_demand_kw": 700,
                "bootstrap_inventory_pressure": "low",
                "candidate_classes": [
                    "local_refuel",
                    "route_endogenous_fuel",
                    "establish_power_generation",
                ],
                "hard_postconditions": [
                    "energy_dependency_resolved",
                    "target_chain_resumes",
                    "bootstrap_dependency_not_increased",
                    "no_validated_capability_regresses",
                ],
            },
        },
        {
            "task_id": "runner-preflight:spatial:independent-v1",
            "partition": "runner_preflight",
            "family": "spatial_logistics_routing",
            "seed": None,
            "generator_version": "cortex_f4c_runner_preflight_v1",
            "spec": {
                "grid": [24, 20],
                "start": [2, 2],
                "goal": [20, 16],
                "obstacle_rectangles": [
                    [8, 4, 3, 5],
                    [14, 10, 3, 4],
                    [10, 14, 2, 3],
                ],
                "turn_penalty": 0.3,
                "underground_budget": 2,
                "candidate_classes": [
                    "weighted_astar",
                    "detour_with_underground",
                    "alternate_corridor",
                ],
                "hard_postconditions": [
                    "route_found",
                    "route_collision_free",
                    "endpoints_connected",
                    "route_within_resource_budget",
                ],
            },
        },
        {
            "task_id": "runner-preflight:production:independent-v1",
            "partition": "runner_preflight",
            "family": "production_transition_planning",
            "seed": None,
            "generator_version": "cortex_f4c_runner_preflight_v1",
            "spec": {
                "target_stage": "logistic_science",
                "iron_available": 120,
                "copper_available": 80,
                "wood_available": 20,
                "coal_available": 50,
                "required_buffer_margin": 8,
                "layout_pressure": "moderate",
                "candidate_classes": [
                    "buffer_first",
                    "power_first",
                    "route_first",
                    "craft_chain_first",
                ],
                "hard_postconditions": [
                    "required_material_budget_satisfied",
                    "dependency_order_valid",
                    "target_stage_functional",
                    "no_validated_capability_regresses",
                ],
            },
        },
    )

def pilot_sequence_guard(
    root: Path,
    pilot_sequence: tuple[int, ...] | list[int],
    seed: int,
) -> dict[str, Any]:
    """Require prior pilot reviews and reject out-of-order future evidence."""

    sequence = tuple(int(value) for value in pilot_sequence)
    target = int(seed)
    if target not in sequence:
        raise HarnessValidationError(f"seed {target} is not in pilot sequence")
    index = sequence.index(target)
    prior_reviews: list[dict[str, Any]] = []
    for prior in sequence[:index]:
        review_path = root / "runs" / "f4c_pilot" / str(prior) / "review.json"
        if not review_path.exists():
            raise HarnessValidationError(
                f"prior pilot {prior} has no PASS review"
            )
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessValidationError(
                f"prior pilot {prior} review is unreadable"
            ) from exc
        if (
            not isinstance(review, dict)
            or review.get("status") != "pass"
            or int(review.get("seed", -1)) != prior
        ):
            raise HarnessValidationError(
                f"prior pilot {prior} review is not PASS"
            )
        prior_reviews.append(
            {
                "seed": prior,
                "review_path": str(review_path),
                "review_schema": review.get("schema_version"),
            }
        )

    unexpected_later = [
        later
        for later in sequence[index + 1 :]
        if (
            root / "runs" / "f4c_pilot" / str(later) / "pair.json"
        ).exists()
    ]
    if unexpected_later:
        raise HarnessValidationError(
            "future pilot evidence exists out of order: "
            + ",".join(map(str, unexpected_later))
        )
    return {
        "seed": target,
        "position": index + 1,
        "total": len(sequence),
        "prior_reviews": prior_reviews,
        "future_artifacts_absent": True,
    }

