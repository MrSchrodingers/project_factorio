import unittest

from factorio_ai_lab.learning.survival import (
    FitnessVector,
    compare_challenger,
)


class SurvivalSelectionTests(unittest.TestCase):
    def test_first_candidate_becomes_champion(self) -> None:
        candidate = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.5},
        )
        decision = compare_challenger(None, candidate)
        self.assertTrue(decision.promoted)

    def test_first_candidate_with_failures_is_not_champion(self) -> None:
        candidate = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.5},
            failures=1,
        )
        decision = compare_challenger(None, candidate)
        self.assertFalse(decision.promoted)
        self.assertTrue(decision.regressions)

    def test_losing_existing_throughput_rejects_challenger(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 2.0},
        )
        challenger = FitnessVector(
            capabilities=frozenset({"iron_backbone", "copper_mining"}),
            rates_per_s={"iron-plate": 1.0, "copper-ore": 1.0},
        )
        decision = compare_challenger(champion, challenger)
        self.assertFalse(decision.promoted)
        self.assertTrue(
            any("iron-plate rate" in value for value in decision.regressions)
        )

    def test_new_capability_promotes_when_baseline_survives(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 2.0},
            external_dependencies=1,
        )
        challenger = FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.8, "coal": 0.5},
            external_dependencies=0,
        )
        decision = compare_challenger(champion, challenger)
        self.assertTrue(decision.promoted)
        self.assertFalse(decision.regressions)
        self.assertTrue(
            any("new capabilities" in value for value in decision.improvements)
        )

    def test_heterogeneous_rate_sum_cannot_promote_regression(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone", "automation_science"}),
            rates_per_s={"iron-system": 2.0, "automation-science-pack": 0.2},
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-system": 1.7, "automation-science-pack": 0.2},
        )
        decision = compare_challenger(champion, challenger)
        self.assertFalse(decision.promoted)
        self.assertFalse(decision.improvements)

    def test_individual_rate_improvement_can_promote(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-system": 2.0},
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-system": 2.2},
        )
        decision = compare_challenger(champion, challenger)
        self.assertTrue(decision.promoted)
        self.assertTrue(any("iron-system improved" in item for item in decision.improvements))

    def test_more_manual_logistics_rejects_challenger(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.5},
            autonomy_score=0.5,
            manual_logistics_calls=2,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 2.0},
            autonomy_score=0.5,
            manual_logistics_calls=3,
        )

        decision = compare_challenger(champion, challenger)

        self.assertFalse(decision.promoted)
        self.assertTrue(
            any("manual logistics increased" in row for row in decision.regressions)
        )

    def test_autonomy_improvement_can_promote(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.5},
            autonomy_score=0.5,
            manual_logistics_calls=2,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.5},
            autonomy_score=0.75,
            manual_logistics_calls=1,
        )

        decision = compare_challenger(champion, challenger)

        self.assertTrue(decision.promoted)
        self.assertTrue(
            any("autonomy score improved" in row for row in decision.improvements)
        )

    def test_low_physical_processing_coverage_rejects_challenger(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.0, "coal": 0.2},
            physical_processing_coverage=0.90,
            fuel_starved_entities=0,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.2, "coal": 0.3},
            physical_processing_coverage=1 / 6,
            fuel_starved_entities=0,
        )

        decision = compare_challenger(champion, challenger)

        self.assertFalse(decision.promoted)
        self.assertTrue(
            any(
                "physical processing coverage below 50%" in row
                for row in decision.regressions
            )
        )
        self.assertIn("physical_processing_coverage", decision.compared_metrics)

    def test_fuel_starvation_rejects_post_coal_challenger(self) -> None:
        challenger = FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.0, "coal": 0.2},
            physical_processing_coverage=0.75,
            fuel_starved_entities=2,
        )

        decision = compare_challenger(None, challenger)

        self.assertFalse(decision.promoted)
        self.assertTrue(
            any(
                "fuel starvation remains" in row
                for row in decision.regressions
            )
        )

    def test_equal_candidate_does_not_replace_incumbent(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 2.0},
        )
        decision = compare_challenger(champion, champion)
        self.assertFalse(decision.promoted)
        self.assertFalse(decision.regressions)


class CommensurabilityTests(unittest.TestCase):
    """Incumbents measured by fewer instruments than the challenger."""

    def _legacy_champion(self) -> FitnessVector:
        # Shape of runs/evolution_champion.json (generation 6): no metric from
        # the physical graph, no autonomy metric.
        return FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.0, "coal": 0.2},
            route_cost=7.5,
            route_turns=1,
        )

    def test_unmeasured_incumbent_metric_cannot_reject_challenger(self) -> None:
        champion = self._legacy_champion()
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.2, "coal": 0.25},
            route_cost=7.5,
            route_turns=1,
            physical_processing_coverage=1 / 3,
            fuel_starved_entities=1,
            manual_logistics_calls=4,
        )

        decision = compare_challenger(champion, challenger)

        self.assertTrue(decision.promoted)
        self.assertEqual(decision.regressions, ())
        self.assertNotIn("physical_processing_coverage", decision.compared_metrics)
        self.assertNotIn("fuel_starved_entities", decision.compared_metrics)

    def test_measured_incumbent_still_rejects_coverage_regression(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.0, "coal": 0.2},
            physical_processing_coverage=0.80,
            fuel_starved_entities=0,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.5, "coal": 0.4},
            physical_processing_coverage=0.40,
            fuel_starved_entities=0,
        )

        decision = compare_challenger(champion, challenger)

        self.assertFalse(decision.promoted)
        self.assertTrue(
            any(
                "physical processing coverage below 50%" in row
                for row in decision.regressions
            )
        )
        self.assertIn("physical_processing_coverage", decision.compared_metrics)

    def test_measured_incumbent_still_rejects_fuel_starvation(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone", "coal_mining"}),
            rates_per_s={"iron-plate": 1.0, "coal": 0.2},
            physical_processing_coverage=0.80,
            fuel_starved_entities=0,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.5, "coal": 0.4},
            physical_processing_coverage=0.85,
            fuel_starved_entities=2,
        )

        decision = compare_challenger(champion, challenger)

        self.assertFalse(decision.promoted)
        self.assertTrue(
            any("fuel starvation remains" in row for row in decision.regressions)
        )
        self.assertIn("fuel_starved_entities", decision.compared_metrics)

    def test_decision_reports_what_was_ignored(self) -> None:
        champion = self._legacy_champion()
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.2, "coal": 0.25},
            physical_processing_coverage=1 / 3,
            autonomy_score=0.4,
        )

        decision = compare_challenger(champion, challenger)
        payload = decision.to_dict()

        ignored = " | ".join(decision.incommensurable_metrics)
        self.assertIn("physical_processing_coverage", ignored)
        self.assertIn("autonomy_score", ignored)
        self.assertIn("route_cost", ignored)
        self.assertIn("rates_per_s", decision.compared_metrics)
        self.assertEqual(
            payload["incommensurable_metrics"],
            list(decision.incommensurable_metrics),
        )
        self.assertEqual(
            payload["compared_metrics"],
            list(decision.compared_metrics),
        )

    def test_promoted_challenger_sets_the_floor_for_the_next_generation(self) -> None:
        champion = self._legacy_champion()
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 1.2, "coal": 0.25},
            route_cost=7.5,
            route_turns=1,
            physical_processing_coverage=1 / 3,
            fuel_starved_entities=1,
        )

        first = compare_challenger(champion, challenger)
        self.assertTrue(first.promoted)

        # The promoted fitness vector is stored whole, so the metric it
        # introduced is measured on both sides from here on.
        successor = FitnessVector(
            capabilities=challenger.capabilities,
            rates_per_s={"iron-plate": 1.6, "coal": 0.4},
            route_cost=7.5,
            route_turns=1,
            physical_processing_coverage=0.20,
            fuel_starved_entities=3,
        )
        second = compare_challenger(challenger, successor)

        self.assertFalse(second.promoted)
        self.assertIn("physical_processing_coverage", second.compared_metrics)
        self.assertTrue(
            any(
                "physical processing coverage" in row
                for row in second.regressions
            )
        )
        self.assertTrue(
            any("fuel-starved entities increased" in row for row in second.regressions)
        )

    def test_incumbent_keeps_closed_loop_autonomy_guard(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.0},
            closed_loop_autonomy=True,
        )
        challenger = FitnessVector(
            capabilities=champion.capabilities,
            rates_per_s={"iron-plate": 2.0},
            closed_loop_autonomy=None,
        )

        decision = compare_challenger(champion, challenger)

        self.assertFalse(decision.promoted)
        self.assertIn("closed-loop autonomy was lost", decision.regressions)


if __name__ == "__main__":
    unittest.main()
