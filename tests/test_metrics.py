import unittest

from factorio_ai_lab.metrics.objectives import ObjectiveVector, scalar_score


class ObjectiveTests(unittest.TestCase):
    def test_more_throughput_improves_score(self) -> None:
        low = ObjectiveVector(8, 10, 10, 10, 10, 0, 0)
        high = ObjectiveVector(16, 10, 10, 10, 10, 0, 0)
        self.assertGreater(scalar_score(high), scalar_score(low))

    def test_failure_is_penalized(self) -> None:
        clean = ObjectiveVector(16, 10, 10, 10, 10, 0, 0)
        failed = ObjectiveVector(16, 10, 10, 10, 10, 1, 0)
        self.assertLess(scalar_score(failed), scalar_score(clean))


if __name__ == "__main__":
    unittest.main()
