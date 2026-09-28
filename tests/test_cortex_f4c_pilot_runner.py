from __future__ import annotations

from copy import deepcopy

import pytest

from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    HarnessValidationError,
)
from factorio_ai_lab.cortex.causal_pilot_runtime import (
    PROTOCOL_EXECUTORS,
    execute_candidate,
    pilot_arm_order,
    pilot_task_from_manifest,
    runner_preflight_task,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    build_protocol_manifest,
)
from factorio_ai_lab.cortex.causal_treatment import CANDIDATE_BINDINGS


def test_all_frozen_candidates_have_protocol_executors():
    assert set(PROTOCOL_EXECUTORS) == set(CANDIDATE_BINDINGS)


def test_runner_preflight_executes_every_candidate_without_protocol_seed():
    manifest = build_protocol_manifest()
    budget = HarnessBudget.from_manifest(manifest)

    for frozen in manifest["task_design"]["pilot_tasks"]:
        task = runner_preflight_task(frozen)
        assert task["partition"] == "runner_preflight"
        assert task["seed"] is None
        for candidate in task["spec"]["candidate_classes"]:
            result = execute_candidate(task, candidate, budget)
            assert set(result.hard_postconditions) == set(
                task["spec"]["hard_postconditions"]
            )
            assert result.proposed_actions >= result.action_count
            assert result.proposed_actions >= 1
            assert result.action_count >= 0
            assert result.observed_game_ticks >= 0


def test_pilot_seed_firewall_and_counterbalanced_order():
    manifest = build_protocol_manifest()
    tasks = manifest["task_design"]["pilot_tasks"]
    orders = []
    for frozen in tasks:
        task = pilot_task_from_manifest(manifest, frozen["seed"])
        orders.append(pilot_arm_order(manifest, task))

    assert orders.count((MEMORY_ON, MEMORY_ABLATED)) == 4
    assert orders.count((MEMORY_ABLATED, MEMORY_ON)) == 4
    with pytest.raises(HarnessValidationError, match="not in the frozen pilot"):
        pilot_task_from_manifest(manifest, 20261221)
    with pytest.raises(HarnessValidationError, match="not in the frozen pilot"):
        pilot_task_from_manifest(manifest, 20261101)


def test_protocol_runtime_rejects_evaluation_partition():
    manifest = build_protocol_manifest()
    task = deepcopy(manifest["task_design"]["evaluation_tasks"][0])
    budget = HarnessBudget.from_manifest(manifest)
    candidate = task["spec"]["candidate_classes"][0]
    with pytest.raises(
        HarnessValidationError,
        match="accepts runner_preflight or pilot only",
    ):
        execute_candidate(task, candidate, budget)
