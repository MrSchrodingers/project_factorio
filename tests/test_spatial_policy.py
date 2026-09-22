import unittest

import numpy as np

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.spatial_policy import (
    FEATURE_DIM,
    SpatialPolicy,
    encode_state,
    examples_from_path,
)


class SpatialPolicyTests(unittest.TestCase):
    def test_encode_state_has_stable_shape(self) -> None:
        features = encode_state(
            point=GridPoint(2, 2),
            goal=GridPoint(5, 2),
            is_blocked={GridPoint(3, 3)}.__contains__,
            in_bounds=lambda point: 0 <= point.x < 8 and 0 <= point.y < 8,
            previous_direction=(1, 0),
        )
        self.assertEqual(features.shape, (FEATURE_DIM,))

    def test_policy_learns_simple_rightward_routes(self) -> None:
        path = tuple(GridPoint(x, 2) for x in range(1, 7))
        x_rows, y_rows = examples_from_path(
            path,
            blocked=set(),
            in_bounds=lambda point: 0 <= point.x < 10 and 0 <= point.y < 5,
        )
        x = np.vstack(x_rows * 30)
        y = np.asarray(y_rows * 30, dtype=np.int64)
        model = SpatialPolicy(hidden_size=16, seed=3)
        model.fit(x, y, epochs=40, learning_rate=0.03, batch_size=64)
        rollout = model.rollout(
            path[0],
            path[-1],
            is_blocked=set().__contains__,
            in_bounds=lambda point: 0 <= point.x < 10 and 0 <= point.y < 5,
            max_steps=20,
        )
        self.assertIsNotNone(rollout)
        self.assertEqual(rollout[-1], path[-1])


if __name__ == "__main__":
    unittest.main()
