from __future__ import annotations

from pathlib import Path

from factorio_ai_lab.cortex.f5_trajectory import (
    F5TypedTransition,
    append_transition,
)
from factorio_ai_lab.cortex.f5d_autonomy import (
    PersistentUCBPolicy,
    compact_state,
    decide,
    reward_components,
    select_supported_candidate,
)


def _snapshot() -> dict:
    return {
        "tick":100,
        "entity_count":3,
        "entities":[
            {
                "name":"burner-mining-drill",
                "type":"mining-drill",
                "unit_number":1,
                "position":{"x":0,"y":0},
                "status":"no_fuel",
                "direction":0,
            },
            {
                "name":"wooden-chest",
                "type":"container",
                "unit_number":2,
                "position":{"x":0,"y":-1.5},
                "status":"normal",
                "contents":[{"name":"coal","count":100}],
                "direction":0,
            },
            {
                "name":"stone-furnace",
                "type":"furnace",
                "unit_number":3,
                "position":{"x":4,"y":0},
                "status":"no_ingredients",
                "direction":0,
            },
        ],
        "production":{"produced":{"coal":10},"consumed":{"coal":1}},
    }


def test_f5d_decision_is_generated_from_live_deficit(tmp_path: Path) -> None:
    policy=PersistentUCBPolicy(tmp_path/"policy.json")
    decision=decide(_snapshot(),policy)

    assert decision.candidates
    assert decision.selected is not None
    assert decision.selected.symptom.startswith("fuel_starved:")
    assert decision.selected.action.key=="resupply:insert_fuel_from_world_container"


def test_f5d_policy_updates_only_from_measured_reward(tmp_path: Path) -> None:
    policy=PersistentUCBPolicy(tmp_path/"policy.json")
    symptom="fuel_starved:fuel_starved"
    arm="resupply:insert_fuel_from_world_container"

    before,_=policy.score(symptom,arm,severity=1.0)
    assert before==float("inf")

    policy.update(symptom,arm,2.5)
    reloaded=PersistentUCBPolicy(tmp_path/"policy.json")
    row=reloaded.snapshot()["arms"][f"{symptom}|{arm}"]
    assert row["pulls"]==1
    assert row["mean_reward"]==2.5


def test_f5d_transition_matches_frozen_schema_surface(tmp_path: Path) -> None:
    state={"tick":1}
    transition=F5TypedTransition(
        transition_id="t1",
        state=state,
        candidate_options=(),
        memory_retrieval={},
        selected_option=None,
        expected_effect=None,
        authority_level="A0",
        execution_trace={"executed":False},
        postconditions={},
        capability_delta={},
        resource_cost={},
        rollback={},
        reward_components={},
        next_state=state,
        metadata={},
    )
    path=tmp_path/"trajectory.jsonl"
    digest=append_transition(path,transition)
    assert len(digest)==64
    assert '"schema_version":"cortex_f5_trajectory_schema_v1"' in path.read_text()


def test_compact_state_exposes_learning_reward_surface(tmp_path: Path) -> None:
    policy=PersistentUCBPolicy(tmp_path/"policy.json")
    decision=decide(_snapshot(),policy)
    state=compact_state(_snapshot(),decision.graph)

    assert state["tick"]==100
    assert state["factory_metrics"]["fuel_starved_entities"]>=1
    assert state["production"]["coal"]==10.0

def test_supported_selection_keeps_unbound_priority_visible(tmp_path: Path) -> None:
    policy=PersistentUCBPolicy(tmp_path/"policy.json")
    decision=decide(_snapshot(),policy)
    supported=select_supported_candidate(
        decision,
        ["resupply:insert_fuel_from_world_container"],
    )

    assert decision.selected is not None
    assert supported is not None
    assert supported.action.key=="resupply:insert_fuel_from_world_container"


def test_reward_penalizes_manual_repair_actions() -> None:
    before={
        "factory_metrics":{
            "fuel_starved_entities":3,
            "power_starved_entities":0,
            "producers_reaching_processor":0,
            "isolated_producers":0,
        },
        "production":{"logistic_science":2},
    }
    after={
        "factory_metrics":{
            "fuel_starved_entities":0,
            "power_starved_entities":0,
            "producers_reaching_processor":0,
            "isolated_producers":0,
        },
        "production":{"logistic_science":2},
    }
    reward=reward_components(before,after,manual_logistics_actions=3)
    assert reward["fuel_recovery"]==3
    assert reward["manual_logistics_cost"]==-0.75
    assert reward["total"]==5.25

def test_f5d_runner_freezes_richest_endogenous_coal_source() -> None:
    import importlib.util

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5d_autonomy.py"
    )
    spec=importlib.util.spec_from_file_location("f5d_runner_test",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    snapshot={
        "entities":[
            {
                "name":"burner-mining-drill",
                "unit_number":1,
                "position":{"x":0.0,"y":0.0},
                "status":"no_fuel",
            },
            {
                "name":"wooden-chest",
                "unit_number":2,
                "position":{"x":1.0,"y":1.0},
                "status":"normal",
                "contents":[{"name":"coal","count":150}],
            },
            {
                "name":"wooden-chest",
                "unit_number":3,
                "position":{"x":2.0,"y":2.0},
                "status":"normal",
                "contents":[{"name":"coal","count":800}],
            },
        ]
    }
    source,targets=module._freeze_refuel_inputs(snapshot,["u1"])

    assert source["coal_before"]==800
    assert (source["x"],source["y"])==(2.0,2.0)
    assert targets==[
        {
            "node_id":"u1",
            "entity_name":"burner-mining-drill",
            "x":0.0,
            "y":0.0,
        }
    ]


def test_f5d_execute_preflight_forbids_dirty_checkout(monkeypatch) -> None:
    import importlib.util

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5d_autonomy.py"
    )
    spec=importlib.util.spec_from_file_location("f5d_runner_preflight_test",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module,
        "code_revision",
        lambda:{"dirty":True,"commit":"abc","branch":"research/cortex-v1"},
    )
    try:
        module._execution_preflight()
    except RuntimeError as exc:
        assert "clean committed checkout" in str(exc)
    else:
        raise AssertionError("dirty F5-D checkout must not execute")

