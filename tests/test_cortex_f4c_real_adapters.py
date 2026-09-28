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
    SPATIAL_TOOL_SURFACE,
    SpatialRoutingFixture,
    SpatialRoutingPairedAdapter,
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
