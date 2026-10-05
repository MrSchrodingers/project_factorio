from __future__ import annotations

from factorio_ai_lab.dashboard.state import DashboardState

CAPABILITIES=[
    "iron_extraction",
    "coal_self_sufficiency",
    "iron_smelting",
    "steam_power",
    "copper_chain",
    "automation_science",
    "powered_manufacturing",
    "electric_mining",
    "logistic_science",
]


def _state() -> DashboardState:
    state=DashboardState.__new__(DashboardState)
    return state


def _context(*,achieved: list[str],next_capability: str | None) -> dict:
    return {
        "kind":"global",
        "cortex_phase":{
            "phase":"F5",
            "phase5_checkpoint":"F5-C",
            "phase5_next_checkpoint":"F5-C",
            "resume":{
                "action":"deterministic F5-C capability sequence is complete",
                "do_not_start_another_seed":True,
            },
            "phase5_protocol":{
                "validated":True,
                "authority_level":"A0",
                "continuous_authority":False,
                "legacy_evolution_loop":"off",
                "capabilities":CAPABILITIES,
                "capability_total":9,
                "achieved_capabilities":achieved,
                "next_capability":next_capability,
            },
        },
    }


def test_research_data_projects_completed_f5_instead_of_legacy_state(
    monkeypatch,
) -> None:
    state=_state()
    context=_context(achieved=CAPABILITIES,next_capability=None)
    monkeypatch.setattr(state,"experiment_context_data",lambda:context)

    research=state.research_data()

    assert research["schema_version"]=="dashboard_f5_research_projection_v1"
    assert research["source"]=="cortex_phase_state"
    assert research["status"]=="completed"
    assert research["current_stage"]["status"]=="completed"
    assert research["phase5_projection"]["complete"] is True
    assert research["phase5_projection"]["achieved_count"]==9
    assert research["phase5_projection"]["next_capability"] is None
    assert research["online_learning"]["status"]=="off"
    assert len(research["curriculum"])==9
    assert all(row["status"]=="completed" for row in research["curriculum"])


def test_progression_is_terminal_when_f5_capability_sequence_is_complete() -> None:
    state=_state()
    research=state._phase5_research_projection(
        context=_context(achieved=CAPABILITIES,next_capability=None)
    )
    assert research is not None

    progression=state.engineering_progression_data(
        world={"entities":[],"production":{}},
        research=research,
    )

    assert progression["source"]=="cortex_phase_state"
    assert progression["validated_achieved"]==CAPABILITIES
    assert progression["achieved_count"]==9
    assert progression["capability_total"]==9
    assert progression["frontier"]==[]
    assert progression["next_goal"] is None
    assert progression["terminal"] is True
    assert progression["arena_mode"]=="f5_c_deterministic_baseline"
    assert progression["technology_mode"]=="real_technology_tree"


def test_progression_exposes_only_the_next_f5_capability() -> None:
    state=_state()
    achieved=CAPABILITIES[:-1]
    research=state._phase5_research_projection(
        context=_context(
            achieved=achieved,
            next_capability="logistic_science",
        )
    )
    assert research is not None

    progression=state.engineering_progression_data(
        world={"entities":[],"production":{}},
        research=research,
    )

    assert progression["terminal"] is False
    assert progression["validated_achieved"]==achieved
    assert progression["next_goal"]["goal_id"]=="logistic_science"
    assert [row["goal_id"] for row in progression["frontier"]]==[
        "logistic_science"
    ]
