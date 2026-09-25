from __future__ import annotations

import ast
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.executive import (
    BeliefState,
    ExecutiveGoal,
    FeasibilityState,
    GoalDiagnosis,
    GoalKind,
    GoalStack,
    ScoreTablePolicy,
    assess_hard_feasibility,
    run_shadow_executive,
)
from factorio_ai_lab.learning.repair_loop import (
    CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
    DEFICIT_OUTPUT_UNPROCESSED,
    DEFICIT_POWER_STARVED,
    DIAGNOSIS_FROM_TOPOLOGY,
    Deficit,
    Diagnosis,
    RepairObservation,
)

ROOT = Path(__file__).parents[1]


def _goal() -> ExecutiveGoal:
    return ExecutiveGoal(
        goal_id="restore-material-flow",
        objective="restore a producer-to-processor material path",
        kind=GoalKind.REPAIR,
        priority=10.0,
    )


def _chain_diagnosis(goal: ExecutiveGoal) -> GoalDiagnosis:
    return GoalDiagnosis(
        goal_id=goal.goal_id,
        diagnosis=Diagnosis(
            deficit=Deficit(
                kind=DEFICIT_OUTPUT_UNPROCESSED,
                source="historical_repair_ledger",
                severity=2.0,
                entities=("u1", "u2"),
                measurement={"observed": True},
            ),
            cause=CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
            basis="observed_historical_symptom",
        ),
        basis="historical repair record",
    )


def test_goal_stack_is_immutable_and_parent_ordered() -> None:
    root = ExecutiveGoal(
        goal_id="factory-health",
        objective="restore factory health",
        kind=GoalKind.ENGINEERING,
    )
    child = ExecutiveGoal(
        goal_id="restore-flow",
        objective="restore material flow",
        kind=GoalKind.REPAIR,
        parent_goal_id=root.goal_id,
    )

    stack = GoalStack((root,))
    pushed = stack.push(child)

    assert stack.active is root
    assert pushed.active is child
    assert pushed.without_active() == stack
    with pytest.raises(ValueError, match="parent must precede"):
        GoalStack((child,))


def test_same_goal_exposes_two_alternatives_and_policy_changes_choice() -> None:
    goal = _goal()
    diagnosis = _chain_diagnosis(goal)
    belief = BeliefState(
        belief_id="belief-1",
        source="historical-repair-ledger",
    )
    observation = RepairObservation(graph={})
    stack = GoalStack((goal,))

    prefer_build = run_shadow_executive(
        run_id="f3a-build",
        belief=belief,
        goal_stack=stack,
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=ScoreTablePolicy(
            policy_id="prefer-build",
            scores={
                "placement:place_processing_for_buffered_output": 2.0,
                "rebuild:reroute_producer_logistics": 1.0,
            },
        ),
    )
    prefer_reroute = run_shadow_executive(
        run_id="f3a-reroute",
        belief=belief,
        goal_stack=stack,
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=ScoreTablePolicy(
            policy_id="prefer-reroute",
            scores={
                "placement:place_processing_for_buffered_output": 1.0,
                "rebuild:reroute_producer_logistics": 2.0,
            },
        ),
    )

    build_candidates = prefer_build.generation.candidates
    reroute_candidates = prefer_reroute.generation.candidates
    assert len(build_candidates) == 2
    assert [row.candidate_id for row in build_candidates] == [
        row.candidate_id for row in reroute_candidates
    ]
    assert {row.action.tool for row in build_candidates} == {
        "placement",
        "rebuild",
    }
    assert prefer_build.decision.selected_action_key == (
        "placement:place_processing_for_buffered_output"
    )
    assert prefer_reroute.decision.selected_action_key == (
        "rebuild:reroute_producer_logistics"
    )
    assert (
        prefer_build.decision.prediction_before_action.metric
        == "producers_reaching_processor"
    )
    assert (
        prefer_build.decision.prediction_before_action.direction
        == "increase"
    )
    assert prefer_build.world_mutation is False
    assert prefer_build.execute_authorized is False
    assert prefer_reroute.world_mutation is False
    assert prefer_reroute.execute_authorized is False


def test_hard_feasibility_fails_closed_when_required_resource_is_unknown() -> None:
    goal = ExecutiveGoal(
        goal_id="restore-power",
        objective="restore power to a machine",
        kind=GoalKind.REPAIR,
    )
    diagnosis = GoalDiagnosis(
        goal_id=goal.goal_id,
        diagnosis=Diagnosis(
            deficit=Deficit(
                kind=DEFICIT_POWER_STARVED,
                source="graph_nodes",
                severity=1.0,
                entities=("assembler",),
            ),
            cause="no_power",
            basis=DIAGNOSIS_FROM_TOPOLOGY,
        ),
        basis="test",
    )
    observation = RepairObservation(graph={"nodes": [], "edges": []})
    run = run_shadow_executive(
        run_id="power-unknown",
        belief=BeliefState(
            belief_id="belief-power",
            source="test",
            resource_availability={"power": None},
        ),
        goal_stack=GoalStack((goal,)),
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=ScoreTablePolicy(
            policy_id="score-all",
            scores={},
            default_score=1.0,
        ),
    )

    assert len(run.generation.candidates) == 1
    assessment = run.feasibility[0]
    assert assessment.state is FeasibilityState.INFEASIBLE
    assert assessment.hard_failures == ("resource_unknown:power",)
    assert run.decision.selected_candidate_id is None
    assert run.decision.refusal == "no_feasible_scored_candidate"


def test_feasibility_accepts_explicitly_available_requirement() -> None:
    goal = ExecutiveGoal(
        goal_id="restore-power",
        objective="restore power",
        kind=GoalKind.REPAIR,
    )
    diagnosis = GoalDiagnosis(
        goal_id=goal.goal_id,
        diagnosis=Diagnosis(
            deficit=Deficit(
                kind=DEFICIT_POWER_STARVED,
                source="graph_nodes",
                severity=1.0,
                entities=("assembler",),
            ),
            cause="no_power",
            basis=DIAGNOSIS_FROM_TOPOLOGY,
        ),
        basis="test",
    )
    observation = RepairObservation(graph={"nodes": [], "edges": []})
    run = run_shadow_executive(
        run_id="power-known",
        belief=BeliefState(
            belief_id="belief-power",
            source="test",
            resource_availability={"power": True},
        ),
        goal_stack=GoalStack((goal,)),
        goal_diagnosis=diagnosis,
        observation=observation,
        policy=ScoreTablePolicy(
            policy_id="score-all",
            scores={},
            default_score=1.0,
        ),
    )

    assert run.feasibility[0].state is FeasibilityState.FEASIBLE
    assert run.decision.selected_candidate_id is not None
    assert run.decision.prediction_before_action is not None


def test_executive_kernel_has_no_execution_or_legacy_runner_import() -> None:
    path = ROOT / "src/factorio_ai_lab/cortex/executive.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    forbidden = {
        "factorio_ai_lab.cortex.option_execute",
        "factorio_ai_lab.cortex.structural_execute",
        "factorio_ai_lab.experiments.curriculum_runner",
        "factorio_ai_lab.integrations.fle",
        "factorio_rcon",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not (imports & forbidden)


def test_assess_hard_feasibility_does_not_infer_unneeded_resources() -> None:
    goal = _goal()
    run = run_shadow_executive(
        run_id="chain",
        belief=BeliefState(
            belief_id="b",
            source="test",
        ),
        goal_stack=GoalStack((goal,)),
        goal_diagnosis=_chain_diagnosis(goal),
        observation=RepairObservation(graph={}),
        policy=ScoreTablePolicy(
            policy_id="default",
            scores={},
            default_score=0.0,
        ),
    )
    candidate = run.generation.candidates[0]
    assert assess_hard_feasibility(candidate, run.belief).feasible is True
