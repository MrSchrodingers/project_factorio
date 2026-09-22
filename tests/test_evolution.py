import unittest

from factorio_ai_lab.learning.evolution import challenger_genome


class EvolutionGenomeTests(unittest.TestCase):
    def test_first_attempt_is_control(self) -> None:
        genome = challenger_genome(attempt=1)
        self.assertEqual(genome.routing_turn_penalty, 0.25)
        self.assertEqual(genome.placement_exploration, 2.0)

    def test_rejected_attempts_still_mutate(self) -> None:
        control = challenger_genome(attempt=1)
        challenger = challenger_genome(attempt=2)
        self.assertNotEqual(challenger, control)
        self.assertAlmostEqual(challenger.routing_turn_penalty, 0.15)
        self.assertAlmostEqual(challenger.placement_exploration, 1.5)

    def test_mutates_around_champion(self) -> None:
        genome = challenger_genome(
            attempt=3,
            champion_configuration={
                "routing_turn_penalty": 0.1,
                "placement_exploration": 1.0,
            },
        )
        self.assertAlmostEqual(genome.routing_turn_penalty, 0.14)
        self.assertAlmostEqual(genome.placement_exploration, 1.25)


if __name__ == "__main__":
    unittest.main()
