import unittest

from factorio_ai_lab.planning.progression import (
    EARLY_GAME_ENGINEERING_GOALS,
    EngineeringGoal,
    EngineeringState,
    ProductionEngineeringPlanner,
)


class ProductionEngineeringPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = ProductionEngineeringPlanner(
            EARLY_GAME_ENGINEERING_GOALS
        )

    def test_initial_goal_is_iron_backbone(self) -> None:
        state = EngineeringState()
        goal = self.planner.next_goal(state)
        self.assertIsNotNone(goal)
        assert goal is not None
        self.assertEqual(goal.goal_id, "iron_backbone")

    def test_copper_becomes_priority_after_iron_backbone(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone"}),
            item_rates={"iron-plate": 2.0},
        )
        ranked = self.planner.ranked_frontier(state)
        self.assertEqual(
            [candidate.goal.goal_id for candidate in ranked],
            ["copper_mining", "steam_power"],
        )

    def test_repeated_copper_failures_open_alternate_branch(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone"}),
            item_rates={"iron-plate": 2.0},
            stalled_attempts={"copper_mining": 4},
        )
        goal = self.planner.next_goal(state)
        self.assertIsNotNone(goal)
        assert goal is not None
        self.assertEqual(goal.goal_id, "steam_power")

    def test_metrics_can_infer_completed_goals(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone", "copper_mining"}),
            item_rates={
                "iron-plate": 2.0,
                "copper-ore": 3.0,
                "copper-plate": 1.5,
            },
        )
        inferred = self.planner.inferred_achieved(state)
        self.assertIn("copper_smelting", inferred)
        frontier = {
            candidate.goal.goal_id
            for candidate in self.planner.ranked_frontier(state)
        }
        self.assertIn("automation_science", frontier)
        self.assertIn("steam_power", frontier)

    def test_research_and_entities_are_completion_signals(self) -> None:
        state = EngineeringState(
            achieved=frozenset(
                {
                    "iron_backbone",
                    "copper_mining",
                    "copper_smelting",
                    "automation_science",
                }
            ),
            item_rates={
                "iron-plate": 2.0,
                "copper-ore": 2.0,
                "copper-plate": 2.0,
                "automation-science-pack": 0.2,
            },
            entity_counts={"lab": 1},
            researched=frozenset({"automation"}),
        )
        inferred = self.planner.inferred_achieved(state)
        self.assertIn("lab_automation", inferred)

    def test_invalid_dependency_graph_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProductionEngineeringPlanner(
                (
                    EngineeringGoal(
                        goal_id="broken",
                        label="Broken",
                        kind="test",
                        prerequisites=frozenset({"missing"}),
                    ),
                )
            )


if __name__ == "__main__":
    unittest.main()
