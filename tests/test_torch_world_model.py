import math
import tempfile
import unittest
from pathlib import Path

from factorio_ai_lab.learning.telemetry import FEATURE_NAMES
from factorio_ai_lab.learning.torch_world_model import TorchGRUWorldModel


def sample(index: int) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["prod:iron-ore"] = 8.0 + 0.12 * index
    features["prod:coal"] = 3.0 + math.sin(index / 7)
    features["coal_reserve"] = 8.0 + math.sin(index / 4)
    features["entity:burner-mining-drill"] = 3.0
    features["progress"] = min(1.0, index / 90)
    fault = index % 29 in {0, 1}
    features["status:no_fuel"] = 1.0 if fault else 0.0
    return {
        "timestamp": float(index),
        "run_id": "synthetic",
        "connected": True,
        "features": features,
        "critical_fault": fault,
    }


class TorchWorldModelTests(unittest.TestCase):
    def test_gru_fits_predicts_and_round_trips(self) -> None:
        rows = [sample(i) for i in range(110)]
        model = TorchGRUWorldModel(
            hidden_size=24,
            sequence_length=6,
            seed=7,
        )
        metrics = model.fit(rows, epochs=12, batch_size=32, patience=5)
        self.assertGreater(metrics.training_windows, 50)
        prediction, risk = model.predict_next(rows[-6:])
        self.assertEqual(set(prediction), set(FEATURE_NAMES))
        self.assertGreaterEqual(risk, 0.0)
        self.assertLessEqual(risk, 1.0)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gru.pt"
            model.save(path)
            loaded = TorchGRUWorldModel.load(path)
            loaded_prediction, loaded_risk = loaded.predict_next(rows[-6:])
            self.assertAlmostEqual(
                prediction["prod:iron-ore"],
                loaded_prediction["prod:iron-ore"],
                places=5,
            )
            self.assertAlmostEqual(risk, loaded_risk, places=5)


if __name__ == "__main__":
    unittest.main()
