import unittest

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.metrics.routing import count_turns


class RoutingMetricsTests(unittest.TestCase):
    def test_counts_direction_changes(self) -> None:
        path = (
            GridPoint(0, 0),
            GridPoint(1, 0),
            GridPoint(2, 0),
            GridPoint(2, 1),
            GridPoint(2, 2),
            GridPoint(3, 2),
        )
        self.assertEqual(count_turns(path), 2)

    def test_short_path_has_no_turns(self) -> None:
        self.assertEqual(count_turns((GridPoint(0, 0), GridPoint(1, 0))), 0)


if __name__ == "__main__":
    unittest.main()
