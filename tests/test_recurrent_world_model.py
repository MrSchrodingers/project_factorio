import math
import tempfile
import unittest
from pathlib import Path

from factorio_ai_lab.learning.recurrent_world_model import RecurrentWorldModel
from factorio_ai_lab.learning.telemetry import FEATURE_NAMES


def sample(index: int) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["prod:iron-ore"] = 10.0 + 0.3 * index
    features["prod:coal"] = 3.0 + math.sin(index / 5)
    features["entity:burner-mining-drill"] = 2.0
    features["progress"] = index / 80
    features["status:no_fuel"] = 1.0 if index % 17 == 0 else 0.0
    return {
        "timestamp": float(index),
        "run_id": "synthetic",
        "connected": True,
        "features": features,
        "critical_fault": bool(features["status:no_fuel"]),
    }


class RecurrentWorldModelTests(unittest.TestCase):
    def test_fits_predicts_and_round_trips(self) -> None:
        rows = [sample(i) for i in range(80)]
        model = RecurrentWorldModel(reservoir_size=24, seed=7)
        metrics = model.fit(rows)
        self.assertGreater(metrics.transition_count, 60)
        prediction, risk, hidden = model.predict_next(rows[-2])
        self.assertEqual(set(prediction), set(FEATURE_NAMES))
        self.assertGreaterEqual(risk, 0.0)
        self.assertLessEqual(risk, 1.0)
        self.assertEqual(hidden.shape, (24,))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.npz"
            model.save(path)
            loaded = RecurrentWorldModel.load(path)
            loaded_prediction, _, _ = loaded.predict_next(rows[-2])
            self.assertAlmostEqual(
                prediction["prod:iron-ore"],
                loaded_prediction["prod:iron-ore"],
                places=8,
            )


if __name__ == "__main__":
    unittest.main()
