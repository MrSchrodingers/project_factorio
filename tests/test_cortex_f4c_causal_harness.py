from __future__ import annotations

from copy import deepcopy

import pytest

from factorio_ai_lab.cortex.causal_harness import (
    OUTCOME_EXTRACTOR_VERSION,
    ArmObservation,
    HarnessBudget,
    MemoryAccess,
    checkpoint_digest,
    execute_pair,
    recompute_pair_delta,
    score_observation,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    build_protocol_manifest,
)
from factorio_ai_lab.cortex.memory import (
    CognitiveMemoryStore,
    MemoryItem,
    MemoryKind,
    MemoryOccurrence,
    ValidityScope,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    MemoryQuery,
    load_memory_records,
    memory_database_snapshot,
)


def _memory(path) -> None:
    item = MemoryItem(
        kind=MemoryKind.PROCEDURAL,
        key="fixture-repair",
        content={"action_key": "fixture_action"},
        scope=ValidityScope(),
    )
    occurrence = MemoryOccurrence(
        memory_id=item.memory_id,
        source="fixture",
        source_sha256="fixture",
        source_locator="fixture:1",
        observed_at="2026-01-01T00:00:00+00:00",
        payload={"fixture": True},
        batch_id="fixture",
        qualified=True,
        reward=1.0,
    )
    with CognitiveMemoryStore(path) as store:
        store.ingest(item, occurrence)


def _task() -> dict:
    manifest = build_protocol_manifest()
    task = deepcopy(manifest["task_design"]["evaluation_tasks"][0])
    task["task_id"] = "preflight:test"
    task["partition"] = "synthetic_preflight"
    task["seed"] = None
    return task


class FakeWorld:
    def __init__(self) -> None:
        self.state = {"counter": 0, "trace": []}
        self.starts: list[str] = []

    def capture_checkpoint(self):
        return deepcopy(self.state)

    def restore_checkpoint(self, checkpoint):
        self.state = deepcopy(checkpoint)

    def state_digest(self):
        return checkpoint_digest(self.state)

    def run_arm(self, task, memory, budget):
        self.starts.append(self.state_digest())
        result = memory.retrieve(
            MemoryQuery(
                query_id="fixture",
                text="fixture repair",
                scope=ValidityScope(),
                limit=1,
            )
        )
        memory.quarantine_write({"retrieved": len(result.results)})
        self.state["counter"] += 1
        self.state["trace"].append(memory.condition)
        return ArmObservation(
            hard_postconditions={
                str(name): True
                for name in task["spec"]["hard_postconditions"]
            },
            action_count=2,
            observed_game_ticks=120,
            invalid_or_refused_actions=0,
            proposed_actions=2,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.1,
            llm_calls=0,
            candidate_surface=tuple(task["spec"]["candidate_classes"]),
            tool_surface=("same-tool",),
            outcome_extractor_version=OUTCOME_EXTRACTOR_VERSION,
        )


def test_retrieval_only_ablation_returns_empty_without_mutating_source(
    tmp_path,
):
    path = tmp_path / "memory.sqlite3"
    _memory(path)
    records = load_memory_records(path)
    before = memory_database_snapshot(path)
    query = MemoryQuery(
        query_id="q",
        text="fixture repair",
        scope=ValidityScope(),
        limit=1,
    )

    on = MemoryAccess(MEMORY_ON, records, max_queries=4)
    ablated = MemoryAccess(MEMORY_ABLATED, records, max_queries=4)
    assert len(on.retrieve(query).results) == 1
    assert ablated.retrieve(query).results == ()
    on.quarantine_write({"x": 1})
    ablated.quarantine_write({"x": 2})

    assert memory_database_snapshot(path) == before
    assert on.to_dict()["source_write_attempted"] is False
    assert ablated.to_dict()["source_write_attempted"] is False


def test_pair_restores_exact_checkpoint_and_recomputes_delta(tmp_path):
    path = tmp_path / "memory.sqlite3"
    _memory(path)
    manifest = build_protocol_manifest()
    budget = HarnessBudget.from_manifest(manifest)
    records = load_memory_records(path)
    world = FakeWorld()
    checkpoint = world.state_digest()

    pair = execute_pair(
        task=_task(),
        first_condition=MEMORY_ON,
        second_condition=MEMORY_ABLATED,
        adapter=world,
        memory_records=records,
        memory_snapshot=lambda: memory_database_snapshot(path),
        budget=budget,
    )

    assert pair["valid"] is True
    assert world.starts == [checkpoint, checkpoint]
    assert (
        pair["arms"][MEMORY_ON]["budget"]
        == pair["arms"][MEMORY_ABLATED]["budget"]
    )
    assert (
        pair["arms"][MEMORY_ON]["observation"]["candidate_surface"]
        == pair["arms"][MEMORY_ABLATED]["observation"]["candidate_surface"]
    )
    assert pair["arms"][MEMORY_ON]["memory"]["retrievals"][0][
        "result"
    ]["results"]
    assert (
        pair["arms"][MEMORY_ABLATED]["memory"]["retrievals"][0][
            "result"
        ]["results"]
        == []
    )
    assert recompute_pair_delta(pair) == pair["delta_J"] == 0.0


def test_missing_primary_component_is_invalid_not_zero():
    manifest = build_protocol_manifest()
    budget = HarnessBudget.from_manifest(manifest)
    task = _task()
    hard = {
        str(name): True for name in task["spec"]["hard_postconditions"]
    }
    hard[next(iter(hard))] = None
    score = score_observation(
        task,
        ArmObservation(
            hard_postconditions=hard,
            action_count=1,
            observed_game_ticks=1,
            invalid_or_refused_actions=0,
            proposed_actions=1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.1,
            llm_calls=0,
            candidate_surface=tuple(task["spec"]["candidate_classes"]),
            tool_surface=("same-tool",),
        ),
        budget,
    )
    assert score.valid is False
    assert score.j is None
    assert any(
        item.startswith("missing_postcondition:")
        for item in score.invalid_reasons
    )


def test_budget_overrun_fails_closed():
    manifest = build_protocol_manifest()
    budget = HarnessBudget.from_manifest(manifest)
    task = _task()
    score = score_observation(
        task,
        ArmObservation(
            hard_postconditions={
                str(name): True
                for name in task["spec"]["hard_postconditions"]
            },
            action_count=budget.max_actions + 1,
            observed_game_ticks=1,
            invalid_or_refused_actions=0,
            proposed_actions=budget.max_actions + 1,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.1,
            llm_calls=0,
            candidate_surface=tuple(task["spec"]["candidate_classes"]),
            tool_surface=("same-tool",),
        ),
        budget,
    )
    assert score.valid is False
    assert "budget_overrun:actions" in score.invalid_reasons


def test_no_actions_on_unsatisfied_task_has_invalid_action_rate_one():
    manifest = build_protocol_manifest()
    budget = HarnessBudget.from_manifest(manifest)
    task = _task()
    score = score_observation(
        task,
        ArmObservation(
            hard_postconditions={
                str(name): False
                for name in task["spec"]["hard_postconditions"]
            },
            action_count=0,
            observed_game_ticks=0,
            invalid_or_refused_actions=0,
            proposed_actions=0,
            initially_unsatisfied=True,
            decisions=1,
            wall_clock_seconds=0.1,
            llm_calls=0,
            candidate_surface=tuple(task["spec"]["candidate_classes"]),
            tool_surface=("same-tool",),
        ),
        budget,
    )
    assert score.valid is True
    assert score.invalid_action_rate == 1.0


def test_memory_access_enforces_retrieval_budget(tmp_path):
    path = tmp_path / "memory.sqlite3"
    _memory(path)
    access = MemoryAccess(
        MEMORY_ABLATED,
        load_memory_records(path),
        max_queries=1,
    )
    query = MemoryQuery(
        query_id="q",
        text="fixture",
        scope=ValidityScope(),
    )
    access.retrieve(query)
    with pytest.raises(ValueError, match="retrieval query budget exceeded"):
        access.retrieve(query)
