from __future__ import annotations

from copy import deepcopy

import pytest

from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    HarnessValidationError,
    MemoryAccess,
    execute_pair,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    build_protocol_manifest,
)
from factorio_ai_lab.cortex.causal_real_adapters import (
    FUEL_TOOL_SURFACE,
    SPATIAL_TOOL_SURFACE,
    STRUCTURAL_TOOL_SURFACE,
    FuelRecoveryFixture,
    FuelRecoveryPairedAdapter,
    SpatialRoutingFixture,
    SpatialRoutingPairedAdapter,
    StructuralFlowPairedAdapter,
    StructuralRepairFixture,
)
from factorio_ai_lab.cortex.memory import (
    CognitiveMemoryStore,
    MemoryItem,
    MemoryKind,
    MemoryOccurrence,
    ValidityScope,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    load_memory_records,
    memory_database_snapshot,
)


def _memory(path) -> None:
    item = MemoryItem(
        kind=MemoryKind.PROCEDURAL,
        key="spatial-routing-fixture",
        content={
            "lesson": "weighted astar routing with obstacle-aware detours",
            "action_key": "weighted_astar",
        },
        scope=ValidityScope(),
    )
    occurrence = MemoryOccurrence(
        memory_id=item.memory_id,
        source="real-adapter-test",
        source_sha256="fixture",
        source_locator="fixture:spatial",
        observed_at="2026-01-01T00:00:00+00:00",
        payload={"fixture": True},
        batch_id="fixture",
        qualified=True,
        reward=1.0,
    )
    with CognitiveMemoryStore(path) as store:
        store.ingest(item, occurrence)


def _task() -> dict:
    return {
        "task_id": "adapter-preflight:spatial-routing:test",
        "partition": "adapter_preflight",
        "family": "spatial_logistics_routing",
        "seed": None,
        "generator_version": "cortex_f4c_adapter_preflight_v1",
        "spec": {
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
            "grid": [18, 18],
            "start": [1, 1],
            "goal": [16, 16],
            "obstacle_rectangles": [
                [7, 0, 2, 7],
                [7, 10, 2, 8],
                [12, 6, 2, 5],
            ],
            "turn_penalty": 0.2,
            "underground_budget": 2,
        },
    }


def test_spatial_real_adapter_runs_existing_weighted_astar_in_pair(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _task()
    fixture = SpatialRoutingFixture.from_task(
        task,
        resource_budget_tiles=48,
    )
    adapter = SpatialRoutingPairedAdapter(fixture)
    checkpoint = adapter.state_digest()
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ON,
        second_condition=MEMORY_ABLATED,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    assert pair["technical_invalidities"] == []
    assert adapter.arm_start_digests == [checkpoint, checkpoint]
    assert pair["source_memory_before"] == pair["source_memory_after"]
    assert (
        pair["arms"][MEMORY_ON]["observation"]["tool_surface"]
        == list(SPATIAL_TOOL_SURFACE)
    )
    assert (
        pair["arms"][MEMORY_ON]["observation"]["candidate_surface"]
        == pair["arms"][MEMORY_ABLATED]["observation"]["candidate_surface"]
    )
    assert all(
        pair["arms"][condition]["observation"]["hard_postconditions"][name]
        for condition in (MEMORY_ON, MEMORY_ABLATED)
        for name in task["spec"]["hard_postconditions"]
    )
    assert pair["arms"][MEMORY_ABLATED]["memory"]["retrievals"][0][
        "result"
    ]["results"] == []
    assert pair["delta_J"] == 0.0


def test_spatial_real_adapter_refuses_protocol_partition(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _task()
    fixture = SpatialRoutingFixture.from_task(
        task,
        resource_budget_tiles=48,
    )
    adapter = SpatialRoutingPairedAdapter(fixture)
    protocol_task = deepcopy(task)
    protocol_task["partition"] = "pilot"
    protocol_task["seed"] = 20261205
    access = MemoryAccess(
        MEMORY_ON,
        load_memory_records(memory_path),
        max_queries=4,
    )
    with pytest.raises(
        HarnessValidationError,
        match="cannot consume pilot/evaluation tasks",
    ):
        adapter.run_arm(
            protocol_task,
            access,
            HarnessBudget.from_manifest(build_protocol_manifest()),
        )


def test_spatial_resource_budget_is_measured_not_invented_as_success(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _task()
    fixture = SpatialRoutingFixture.from_task(
        task,
        resource_budget_tiles=5,
    )
    adapter = SpatialRoutingPairedAdapter(fixture)
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ABLATED,
        second_condition=MEMORY_ON,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    for condition in (MEMORY_ON, MEMORY_ABLATED):
        hard = pair["arms"][condition]["observation"]["hard_postconditions"]
        assert hard["route_found"] is True
        assert hard["route_collision_free"] is True
        assert hard["endpoints_connected"] is True
        assert hard["route_within_resource_budget"] is False
        score = pair["arms"][condition]["score"]
        assert score["valid"] is True
        assert score["functional_success"] == 0.0
        assert score["goal_progress"] == 0.75

def _fuel_task(*, active_chain_source: bool = False) -> dict:
    return {
        "task_id": "adapter-preflight:fuel-recovery:test",
        "partition": "adapter_preflight",
        "family": "fuel_energy_recovery",
        "seed": None,
        "generator_version": "cortex_f4c_adapter_preflight_v1",
        "spec": {
            "candidate_classes": [
                "repair_loop_resupply",
                "idle_container_draw",
                "active_chain_draw_last_resort",
            ],
            "hard_postconditions": [
                "energy_dependency_resolved",
                "target_chain_resumes",
                "bootstrap_dependency_not_increased",
                "no_validated_capability_regresses",
            ],
            "energy_entity_id": "boiler-1",
            "target_entity_id": "assembler-1",
            "anchor": [10.0, 10.0],
            "fuel_needed": 12,
            "fuel_carried": 0,
            "fuel_sources": [
                {
                    "position": [8.0, 10.0],
                    "available": 12,
                    "supplies_chain": active_chain_source,
                }
            ],
            "validated_capabilities": [
                "iron_backbone",
                "copper_chain",
            ],
        },
    }


def test_fuel_real_adapter_binds_repair_loop_and_resupply_in_pair(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _fuel_task()
    fixture = FuelRecoveryFixture.from_task(task)
    adapter = FuelRecoveryPairedAdapter(fixture)
    checkpoint = adapter.state_digest()
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ON,
        second_condition=MEMORY_ABLATED,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    assert pair["technical_invalidities"] == []
    assert adapter.arm_start_digests == [checkpoint, checkpoint]
    assert pair["source_memory_before"] == pair["source_memory_after"]
    assert (
        pair["arms"][MEMORY_ON]["observation"]["tool_surface"]
        == list(FUEL_TOOL_SURFACE)
    )
    assert all(
        pair["arms"][condition]["observation"]["hard_postconditions"][name]
        for condition in (MEMORY_ON, MEMORY_ABLATED)
        for name in task["spec"]["hard_postconditions"]
    )
    assert pair["arms"][MEMORY_ABLATED]["memory"]["retrievals"][0][
        "result"
    ]["results"] == []
    assert pair["delta_J"] == 0.0


def test_fuel_real_adapter_exposes_active_chain_regression_instead_of_hiding_it(
    tmp_path,
):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _fuel_task(active_chain_source=True)
    fixture = FuelRecoveryFixture.from_task(task)
    adapter = FuelRecoveryPairedAdapter(fixture)
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ABLATED,
        second_condition=MEMORY_ON,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    for condition in (MEMORY_ON, MEMORY_ABLATED):
        hard = pair["arms"][condition]["observation"]["hard_postconditions"]
        assert hard["energy_dependency_resolved"] is True
        assert hard["target_chain_resumes"] is True
        assert hard["bootstrap_dependency_not_increased"] is False
        assert hard["no_validated_capability_regresses"] is False
        score = pair["arms"][condition]["score"]
        assert score["valid"] is True
        assert score["functional_success"] == 0.0
        assert score["goal_progress"] == 0.5


def test_fuel_real_adapter_refuses_protocol_partition(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _fuel_task()
    fixture = FuelRecoveryFixture.from_task(task)
    adapter = FuelRecoveryPairedAdapter(fixture)
    protocol_task = deepcopy(task)
    protocol_task["partition"] = "evaluation"
    protocol_task["seed"] = 20261221
    access = MemoryAccess(
        MEMORY_ON,
        load_memory_records(memory_path),
        max_queries=4,
    )

    with pytest.raises(
        HarnessValidationError,
        match="accepts NON-PROTOCOL adapter_preflight only",
    ):
        adapter.run_arm(
            protocol_task,
            access,
            HarnessBudget.from_manifest(build_protocol_manifest()),
        )

def _structural_task(*, dead_end_delta: int = 0) -> dict:
    return {
        "task_id": "adapter-preflight:structural-flow:test",
        "partition": "adapter_preflight",
        "family": "structural_flow_repair",
        "seed": None,
        "generator_version": "cortex_f4c_adapter_preflight_v1",
        "spec": {
            "candidate_classes": [
                "structural_transactional_processing",
                "reroute_existing_flow",
            ],
            "hard_postconditions": [
                "processor_exists",
                "producer_reaches_processor",
                "processor_output_increases",
                "no_new_dead_end",
            ],
            "initial_producers_reaching_processor": 3,
            "initial_processor_output": 0.0,
            "initial_dead_end_count": 0,
            "processor_exists_after": True,
            "dead_end_delta": dead_end_delta,
        },
    }


def test_structural_real_adapter_uses_transactional_stack_and_commits_pair(
    tmp_path,
):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _structural_task()
    fixture = StructuralRepairFixture.from_task(task)
    adapter = StructuralFlowPairedAdapter(fixture)
    checkpoint = adapter.state_digest()
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ON,
        second_condition=MEMORY_ABLATED,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    assert pair["technical_invalidities"] == []
    assert adapter.arm_start_digests == [checkpoint, checkpoint]
    assert pair["source_memory_before"] == pair["source_memory_after"]
    assert (
        pair["arms"][MEMORY_ON]["observation"]["tool_surface"]
        == list(STRUCTURAL_TOOL_SURFACE)
    )
    assert all(
        pair["arms"][condition]["observation"]["hard_postconditions"][name]
        for condition in (MEMORY_ON, MEMORY_ABLATED)
        for name in task["spec"]["hard_postconditions"]
    )
    assert pair["arms"][MEMORY_ABLATED]["memory"]["retrievals"][0][
        "result"
    ]["results"] == []
    assert pair["delta_J"] == 0.0


def test_structural_real_adapter_rolls_back_new_dead_end(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _structural_task(dead_end_delta=1)
    fixture = StructuralRepairFixture.from_task(task)
    adapter = StructuralFlowPairedAdapter(fixture)
    pair = execute_pair(
        task=task,
        first_condition=MEMORY_ABLATED,
        second_condition=MEMORY_ON,
        adapter=adapter,
        memory_records=load_memory_records(memory_path),
        memory_snapshot=lambda: memory_database_snapshot(memory_path),
        budget=HarnessBudget.from_manifest(build_protocol_manifest()),
    )

    assert pair["valid"] is True
    for condition in (MEMORY_ON, MEMORY_ABLATED):
        observation = pair["arms"][condition]["observation"]
        hard = observation["hard_postconditions"]
        assert hard["processor_exists"] is False
        assert hard["producer_reaches_processor"] is False
        assert hard["processor_output_increases"] is False
        assert hard["no_new_dead_end"] is True
        assert observation["action_count"] == 0
        assert observation["invalid_or_refused_actions"] == 1
        score = pair["arms"][condition]["score"]
        assert score["valid"] is True
        assert score["functional_success"] == 0.0
        assert score["goal_progress"] == 0.25


def test_structural_real_adapter_refuses_protocol_partition(tmp_path):
    memory_path = tmp_path / "memory.sqlite3"
    _memory(memory_path)
    task = _structural_task()
    fixture = StructuralRepairFixture.from_task(task)
    adapter = StructuralFlowPairedAdapter(fixture)
    protocol_task = deepcopy(task)
    protocol_task["partition"] = "pilot"
    protocol_task["seed"] = 20261201
    access = MemoryAccess(
        MEMORY_ON,
        load_memory_records(memory_path),
        max_queries=4,
    )

    with pytest.raises(
        HarnessValidationError,
        match="accepts NON-PROTOCOL adapter_preflight only",
    ):
        adapter.run_arm(
            protocol_task,
            access,
            HarnessBudget.from_manifest(build_protocol_manifest()),
        )

