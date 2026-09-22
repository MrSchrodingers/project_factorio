import unittest

from factorio_ai_lab.learning.telemetry import (
    FEATURE_NAMES,
    compact_telemetry_sample,
    feature_vector,
)


class TelemetryTests(unittest.TestCase):
    def test_compact_sample_preserves_operational_signals(self) -> None:
        sample = compact_telemetry_sample(
            timestamp=1.0,
            world={
                "connected": True,
                "tick": 10,
                "entities": [
                    {"name": "burner-mining-drill", "status": "no_fuel"},
                    {"name": "stone-furnace", "status": "working"},
                ],
            },
            research={
                "run_id": "r1",
                "progress": 0.5,
                "arena": {"mode": "lab_play"},
                "metrics": {"coal_endogenous_stockpile": 8},
                "resource_accounting": {
                    "exogenous_inputs": {
                        "coal": {
                            "status": "self_sufficient",
                            "safety_stock_target": 4,
                        }
                    }
                },
            },
            progression={"achieved": ["iron_backbone"], "dependency_debt": []},
            production={
                "series": {
                    "iron-ore": {"produced_rate": 30, "consumed_rate": 0},
                    "coal": {"produced_rate": 6, "consumed_rate": 4},
                }
            },
        )
        self.assertEqual(sample["arena"], "lab_play")
        self.assertTrue(sample["critical_fault"])
        self.assertEqual(sample["features"]["prod:iron-ore"], 30.0)
        self.assertEqual(sample["features"]["coal_reserve"], 8.0)
        self.assertEqual(sample["features"]["external_dependencies"], 0.0)
        self.assertEqual(len(feature_vector(sample)), len(FEATURE_NAMES))


if __name__ == "__main__":
    unittest.main()
