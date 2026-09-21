import unittest

from factorio_ai_lab.learning.bandit import UCB1Bandit


class UCB1BanditTests(unittest.TestCase):
    def test_explores_every_arm_first(self) -> None:
        bandit = UCB1Bandit((0.0, 0.1, 0.25))
        selected = []
        for reward in (0.1, 0.2, 0.3):
            arm = bandit.select()
            selected.append(arm)
            bandit.update(arm, reward)
        self.assertEqual(selected, [0.0, 0.1, 0.25])

    def test_converges_toward_better_arm(self) -> None:
        bandit = UCB1Bandit(("a", "b"), exploration=0.2)
        for _ in range(100):
            arm = bandit.select()
            bandit.update(arm, 1.0 if arm == "b" else 0.0)
        self.assertEqual(bandit.best_observed(), "b")
        self.assertGreater(
            bandit.stats()["b"].pulls,
            bandit.stats()["a"].pulls,
        )

    def test_rejects_unknown_arm(self) -> None:
        bandit = UCB1Bandit((1, 2))
        with self.assertRaises(KeyError):
            bandit.update(3, 1.0)


if __name__ == "__main__":
    unittest.main()
