from factorio_ai_lab.cortex.causal_treatment import (
    CANDIDATE_BINDINGS,
    rank_candidates,
    treatment_retrieval_limit,
)


def test_treatment_keeps_surface_but_memory_changes_score_path():
    candidates = [
        "local_refuel",
        "route_endogenous_fuel",
        "establish_power_generation",
    ]
    rows = [
        {
            "key": "fuel_starved:no_fuel|resupply:insert_fuel_from_world_container",
            "content": {
                "action_key": "resupply:insert_fuel_from_world_container"
            },
            "scores": {"total": 0.9},
        }
    ]
    on = rank_candidates(candidates, rows)
    ablated = rank_candidates(candidates, ())

    assert on.candidates == ablated.candidates == tuple(candidates)
    assert on.memory_influence > 0.0
    assert ablated.memory_influence == 0.0
    assert sum(row.memory_support for row in on.scores) > 0.0
    assert all(row.memory_support == 0.0 for row in ablated.scores)


def test_all_frozen_candidate_bindings_are_unique_and_planner_bound():
    expected = {
        "placement_processing",
        "reroute_logistics",
        "rebuild_segment",
        "local_refuel",
        "route_endogenous_fuel",
        "establish_power_generation",
        "weighted_astar",
        "detour_with_underground",
        "alternate_corridor",
        "buffer_first",
        "power_first",
        "route_first",
        "craft_chain_first",
    }
    assert set(CANDIDATE_BINDINGS) == expected
    assert all(row.planner_bound for row in CANDIDATE_BINDINGS.values())
    assert not all(
        row.protocol_executable for row in CANDIDATE_BINDINGS.values()
    )


def test_treatment_retrieval_limit_scales_with_frozen_surface():
    assert treatment_retrieval_limit(["a", "b", "c"]) == 9
    assert treatment_retrieval_limit(["a", "b", "c", "d"]) == 12
