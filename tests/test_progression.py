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

    def test_coal_self_sufficiency_is_priority_after_iron_backbone(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone"}),
            item_rates={"iron-plate": 2.0},
        )
        ranked = self.planner.ranked_frontier(state)
        self.assertEqual(
            [candidate.goal.goal_id for candidate in ranked],
            ["coal_mining", "copper_mining"],
        )

    def test_repeated_coal_failures_open_copper_branch(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone"}),
            item_rates={"iron-plate": 2.0},
            stalled_attempts={"coal_mining": 4},
        )
        goal = self.planner.next_goal(state)
        self.assertIsNotNone(goal)
        assert goal is not None
        self.assertEqual(goal.goal_id, "copper_mining")

    def test_metrics_can_infer_completed_goals(self) -> None:
        state = EngineeringState(
            achieved=frozenset({"iron_backbone", "coal_mining", "copper_mining"}),
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
        self.assertNotIn("automation_science", frontier)
        self.assertIn("steam_power", frontier)
        self.assertIn("electronics_trigger", frontier)

    def test_research_and_entities_are_completion_signals(self) -> None:
        state = EngineeringState(
            achieved=frozenset(
                {
                    "iron_backbone",
                    "coal_mining",
                    "copper_mining",
                    "copper_smelting",
                    "steam_power",
                    "electronics_trigger",
                    "lab_bootstrap",
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
            researched=frozenset(
                {"electronics", "automation-science-pack", "automation"}
            ),
        )
        inferred = self.planner.inferred_achieved(state)
        self.assertIn("lab_automation", inferred)

    def test_factorio_2_trigger_sequence_precedes_red_science(self) -> None:
        state = EngineeringState(
            achieved=frozenset(
                {
                    "iron_backbone",
                    "coal_mining",
                    "copper_mining",
                    "copper_smelting",
                    "steam_power",
                }
            ),
            item_rates={
                "iron-plate": 2.0,
                "copper-plate": 2.0,
            },
        )
        goal_ids = {
            candidate.goal.goal_id
            for candidate in self.planner.ranked_frontier(state)
        }
        self.assertIn("electronics_trigger", goal_ids)
        self.assertNotIn("lab_bootstrap", goal_ids)
        self.assertNotIn("automation_science", goal_ids)

        state_with_electronics = EngineeringState(
            achieved=state.achieved | frozenset({"electronics_trigger"}),
            item_rates=state.item_rates,
            researched=frozenset({"electronics"}),
        )
        self.assertEqual(
            self.planner.next_goal(state_with_electronics).goal_id,
            "lab_bootstrap",
        )

    def test_claimed_goal_with_missing_dependency_becomes_debt(self) -> None:
        state = EngineeringState(
            achieved=frozenset(
                {"iron_backbone", "copper_mining", "copper_smelting"}
            ),
            item_rates={
                "iron-plate": 2.0,
                "copper-ore": 2.0,
                "copper-plate": 1.0,
            },
        )
        inferred = self.planner.inferred_achieved(state)
        self.assertIn("iron_backbone", inferred)
        self.assertIn("copper_mining", inferred)
        self.assertNotIn("copper_smelting", inferred)
        debt = self.planner.dependency_debt(state)
        self.assertEqual(debt[0]["goal_id"], "copper_smelting")
        self.assertEqual(
            debt[0]["missing_prerequisites"],
            ["coal_mining"],
        )
        self.assertEqual(
            self.planner.next_goal(state).goal_id,
            "coal_mining",
        )

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
