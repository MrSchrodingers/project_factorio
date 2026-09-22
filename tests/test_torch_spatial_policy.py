import tempfile
import unittest
from pathlib import Path

import numpy as np

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.spatial_policy import examples_from_path
from factorio_ai_lab.learning.torch_spatial_policy import TorchSpatialPolicy


class TorchSpatialPolicyTests(unittest.TestCase):
    def test_attention_policy_learns_simple_route(self) -> None:
        path = tuple(GridPoint(x, 2) for x in range(1, 8))
        x_rows, y_rows = examples_from_path(
            path,
            blocked=set(),
            in_bounds=lambda point: 0 <= point.x < 10 and 0 <= point.y < 5,
        )
        x = np.vstack(x_rows * 20)
        y = np.asarray(y_rows * 20, dtype=np.int64)
        model = TorchSpatialPolicy(
            d_model=24,
            heads=4,
            layers=1,
            seed=4,
        )
        metrics = model.fit(
            x,
            y,
            validation=(x, y),
            epochs=18,
            batch_size=64,
            patience=5,
        )
        self.assertGreater(metrics.validation_accuracy, 0.95)
        rollout = model.rollout(
            path[0],
            path[-1],
            is_blocked=set().__contains__,
            in_bounds=lambda point: 0 <= point.x < 10 and 0 <= point.y < 5,
            max_steps=20,
        )
        self.assertIsNotNone(rollout)
        self.assertEqual(rollout[-1], path[-1])

        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "attention.pt"
            model.save(model_path)
            loaded = TorchSpatialPolicy.load(model_path)
            self.assertEqual(
                np.argmax(model.logits(x[:1])),
                np.argmax(loaded.logits(x[:1])),
            )


if __name__ == "__main__":
    unittest.main()
