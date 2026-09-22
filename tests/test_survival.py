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

    def test_equal_candidate_does_not_replace_incumbent(self) -> None:
        champion = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 2.0},
        )
        decision = compare_challenger(champion, champion)
        self.assertFalse(decision.promoted)
        self.assertFalse(decision.regressions)


if __name__ == "__main__":
    unittest.main()
