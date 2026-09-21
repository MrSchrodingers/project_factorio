import unittest

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.astar import blocked_from, rectangular_bounds, weighted_astar


class AStarTests(unittest.TestCase):
    def test_routes_around_wall(self) -> None:
        obstacles = {GridPoint(2, 1), GridPoint(2, 2), GridPoint(2, 3)}
        result = weighted_astar(
            GridPoint(0, 2),
            GridPoint(5, 2),
            is_blocked=blocked_from(obstacles),
            in_bounds=rectangular_bounds(6, 5),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.path[0], GridPoint(0, 2))
        self.assertEqual(result.path[-1], GridPoint(5, 2))
        self.assertTrue(obstacles.isdisjoint(result.path))

    def test_no_route_when_sealed(self) -> None:
        obstacles = {GridPoint(1, 0), GridPoint(0, 1)}
        result = weighted_astar(
            GridPoint(0, 0),
            GridPoint(2, 2),
            is_blocked=blocked_from(obstacles),
            in_bounds=rectangular_bounds(3, 3),
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
