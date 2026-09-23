"""Tests for the world-model decision layer.

The fixtures below are MEASURED, not invented. They come from a leave-one-
generation-out holdout of the active echo-state world model over the four most
recent curriculum generations of runs/telemetry/world_samples.jsonl
(2026-09-23). Each entry is the one-step normalized MSE per fold, in fold order:
curriculum-20260923T021611Z, -023825Z, -025433Z, -031342Z.
"""

import tempfile
import unittest
from pathlib import Path

from factorio_ai_lab.learning.model_arena import ModelArena, ModelCandidate
from factorio_ai_lab.learning.recurrent_world_model import RecurrentWorldModel
from factorio_ai_lab.learning.telemetry import FEATURE_NAMES
from factorio_ai_lab.learning.world_model_policy import (
    FeatureEvidence,
    RiskEvidence,
    WorldModelPolicy,
    feature_evidence_from_folds,
    holdout_feature_errors,
)

# Measured: model beats persistence in every fold with a wide margin.
IRON_ORE_MODEL = (0.12672067, 0.27074548, 0.21324981, 0.26277374)
IRON_ORE_PERSISTENCE = (0.19754587, 0.36452401, 0.30969689, 0.36614413)
IRON_ORE_SCALE = 17.14966

# Measured: model wins 3/4 folds but only by 1.5% on average.
COAL_RESERVE_MODEL = (0.00884414, 0.00815917, 0.00498518, 0.00373398)
COAL_RESERVE_PERSISTENCE = (0.00943125, 0.00860449, 0.00423401, 0.00384995)
COAL_RESERVE_SCALE = 15.952223

# Measured: model loses 2/4 folds and is 2.3x worse than persistence on average.
NO_FUEL_MODEL = (0.01599039, 0.020924, 0.12104793, 0.03411398)
NO_FUEL_PERSISTENCE = (0.016587, 0.02193142, 0.02096653, 0.02498608)
NO_FUEL_SCALE = 3.027296

# Measured: critical_fault is true in 100% of every holdout window.
RISK_POSITIVE_RATE = (1.0, 1.0, 1.0, 1.0)
RISK_ACCURACY = (1.0, 1.0, 0.913386, 0.988406)
RISK_PERSISTENCE_ACCURACY = (1.0, 1.0, 1.0, 1.0)


def measured_evidence() -> dict[str, FeatureEvidence]:
    return {
        "prod:iron-ore": FeatureEvidence(
            feature="prod:iron-ore",
            fold_model_mse=IRON_ORE_MODEL,
            fold_persistence_mse=IRON_ORE_PERSISTENCE,
            scale=IRON_ORE_SCALE,
        ),
        "coal_reserve": FeatureEvidence(
            feature="coal_reserve",
            fold_model_mse=COAL_RESERVE_MODEL,
            fold_persistence_mse=COAL_RESERVE_PERSISTENCE,
            scale=COAL_RESERVE_SCALE,
        ),
        "status:no_fuel": FeatureEvidence(
            feature="status:no_fuel",
            fold_model_mse=NO_FUEL_MODEL,
            fold_persistence_mse=NO_FUEL_PERSISTENCE,
            scale=NO_FUEL_SCALE,
        ),
    }


def measured_risk_evidence() -> RiskEvidence:
    return RiskEvidence(
        fold_positive_rate=RISK_POSITIVE_RATE,
        fold_accuracy=RISK_ACCURACY,
        fold_persistence_accuracy=RISK_PERSISTENCE_ACCURACY,
    )


class LinearDecayModel:
    """Deterministic stand-in: every feature drops by a fixed amount per step."""

    def __init__(self, decay: float = 2.0) -> None:
        self.decay = decay

    def predict_next(self, sample, *, hidden=None):
        features = sample.get("features", {})
        prediction = {
            name: float(features.get(name, 0.0)) - self.decay for name in FEATURE_NAMES
        }
        return prediction, 0.5, hidden


def eligible_evidence(feature: str, *, horizon: int = 8) -> FeatureEvidence:
    return FeatureEvidence(
        feature=feature,
        fold_model_mse=(1e-4, 1e-4, 1e-4, 1e-4),
        fold_persistence_mse=(1e-2, 1e-2, 1e-2, 1e-2),
        scale=1.0,
        validated_horizon=horizon,
    )


def factory_sample(coal: float = 40.0) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["coal_reserve"] = coal
    features["prod:iron-ore"] = 12.0
    return {
        "timestamp": 100.0,
        "run_id": "unit",
        "connected": True,
        "features": features,
        "critical_fault": False,
    }


def synthetic_run(count: int = 80) -> list[dict]:
    rows = []
    for index in range(count):
        features = {name: 0.0 for name in FEATURE_NAMES}
        features["coal_reserve"] = 400.0 - 2.0 * index
        features["prod:iron-ore"] = 10.0 + 0.3 * index
        features["entity:burner-mining-drill"] = 2.0
        rows.append(
            {
                "timestamp": float(index),
                "run_id": "synthetic",
                "connected": True,
                "features": features,
                "critical_fault": index % 3 == 0,
            }
        )
    return rows


class EligibilityGateTests(unittest.TestCase):
    def test_gate_accepts_a_known_positive_case(self) -> None:
        # Instrument validation: the gate is not a constant "no". prod:iron-ore
        # wins 4/4 measured folds with a 29% margin and must be admitted.
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        verdict = policy.verdict("prod:iron-ore")
        self.assertTrue(verdict.eligible, verdict.reason)
        self.assertEqual(verdict.fold_wins, 4)
        self.assertLess(verdict.loss_ratio, 0.95)
        self.assertGreater(verdict.residual_sigma, 0.0)

    def test_coal_reserve_is_refused_for_insufficient_margin(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        verdict = policy.verdict("coal_reserve")
        self.assertFalse(verdict.eligible)
        self.assertIn("margin", verdict.reason)
        self.assertGreater(verdict.loss_ratio, 0.95)
        self.assertEqual(verdict.fold_wins, 3)

    def test_no_fuel_status_is_refused_for_inconsistent_folds(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        verdict = policy.verdict("status:no_fuel")
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.fold_wins, 2)
        self.assertIn("fold", verdict.reason)

    def test_unknown_feature_is_refused_rather_than_assumed(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        verdict = policy.verdict("status:no_power")
        self.assertFalse(verdict.eligible)
        self.assertIn("no holdout evidence", verdict.reason)

    def test_risk_channel_is_refused_when_labels_are_single_class(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            measured_evidence(),
            risk_evidence=measured_risk_evidence(),
        )
        verdict = policy.risk_verdict()
        self.assertFalse(verdict.eligible)
        self.assertIn("single-class", verdict.reason)

    def test_risk_channel_without_evidence_is_refused(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        verdict = policy.risk_verdict()
        self.assertFalse(verdict.eligible)
        self.assertIn("no holdout evidence", verdict.reason)


class ForecastTests(unittest.TestCase):
    def test_forecast_declares_interval_and_covers_prediction(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            {"coal_reserve": eligible_evidence("coal_reserve")},
        )
        forecast = policy.forecast(factory_sample(), "coal_reserve")
        self.assertTrue(forecast.usable, forecast.reason)
        self.assertAlmostEqual(forecast.value, 38.0, places=6)
        self.assertLess(forecast.lower, forecast.value)
        self.assertGreater(forecast.upper, forecast.value)
        self.assertEqual(forecast.confidence, 0.95)
        self.assertEqual(forecast.measured, 40.0)

    def test_forecast_is_gated_at_call_time_not_only_at_training(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        forecast = policy.forecast(factory_sample(), "coal_reserve")
        self.assertFalse(forecast.usable)
        self.assertIsNone(forecast.value)
        self.assertIsNone(forecast.lower)
        self.assertIn("margin", forecast.reason)
        # The measurement is still reported: the layer never hides ground truth.
        self.assertEqual(forecast.measured, 40.0)

    def test_interval_widens_with_horizon(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            {"coal_reserve": eligible_evidence("coal_reserve")},
        )
        near = policy.forecast(factory_sample(), "coal_reserve", horizon=1)
        far = policy.forecast(factory_sample(), "coal_reserve", horizon=4)
        self.assertTrue(far.usable, far.reason)
        self.assertAlmostEqual(far.value, 32.0, places=6)
        self.assertGreater(near.value - near.lower, 0.0)
        self.assertGreater(far.value - far.lower, near.value - near.lower)

    def test_forecast_refuses_horizon_beyond_validated(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            {"coal_reserve": eligible_evidence("coal_reserve", horizon=2)},
        )
        forecast = policy.forecast(factory_sample(), "coal_reserve", horizon=5)
        self.assertFalse(forecast.usable)
        self.assertIn("horizon", forecast.reason)

    def test_unsupported_confidence_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WorldModelPolicy(LinearDecayModel(), measured_evidence(), confidence=0.77)


class SurvivalAlertTests(unittest.TestCase):
    def test_alert_fires_on_pessimistic_bound_when_evidence_supports(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            {"coal_reserve": eligible_evidence("coal_reserve")},
        )
        alert = policy.survival_alert(factory_sample(coal=10.0), horizon=8)
        self.assertTrue(alert.actionable, alert.reason)
        self.assertTrue(alert.alarm)
        self.assertEqual(alert.basis, "forecast")
        self.assertEqual(alert.steps_to_threshold, 5)

    def test_alert_stays_silent_when_reserve_holds_through_the_window(self) -> None:
        policy = WorldModelPolicy(
            LinearDecayModel(),
            {"coal_reserve": eligible_evidence("coal_reserve")},
        )
        alert = policy.survival_alert(factory_sample(coal=400.0), horizon=8)
        self.assertTrue(alert.actionable, alert.reason)
        self.assertFalse(alert.alarm)
        self.assertIsNone(alert.steps_to_threshold)

    def test_alert_refuses_when_feature_is_not_eligible(self) -> None:
        # This is the state of the repository today: coal_reserve does not
        # clear the persistence baseline by a usable margin.
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        alert = policy.survival_alert(factory_sample(coal=10.0), horizon=8)
        self.assertFalse(alert.actionable)
        self.assertFalse(alert.alarm)
        self.assertEqual(alert.basis, "refused")
        self.assertEqual(alert.measured, 10.0)
        self.assertIn("margin", alert.reason)

    def test_alert_prefers_measurement_over_forecast_when_already_breached(self) -> None:
        policy = WorldModelPolicy(LinearDecayModel(), measured_evidence())
        alert = policy.survival_alert(factory_sample(coal=0.0), horizon=8)
        self.assertTrue(alert.alarm)
        self.assertEqual(alert.basis, "measurement")
        self.assertEqual(alert.steps_to_threshold, 0)


class ArenaGateTests(unittest.TestCase):
    def test_policy_refuses_when_arena_has_no_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            policy = WorldModelPolicy(
                LinearDecayModel(),
                {"coal_reserve": eligible_evidence("coal_reserve")},
                arena=arena,
            )
            verdict = policy.verdict("coal_reserve")
            self.assertFalse(verdict.eligible)
            self.assertIn("arena", verdict.reason)

    def test_policy_refuses_when_arena_incumbent_is_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            arena = ModelArena(path)
            arena.select(
                ModelCandidate(
                    task="world_model",
                    model_id="esn",
                    protocol="p1",
                    eligible=True,
                    primary_score=0.5,
                )
            )
            payload = path.read_text(encoding="utf-8").replace("true", "false")
            path.write_text(payload, encoding="utf-8")
            policy = WorldModelPolicy(
                LinearDecayModel(),
                {"coal_reserve": eligible_evidence("coal_reserve")},
                arena=ModelArena(path),
            )
            self.assertFalse(policy.verdict("coal_reserve").eligible)

    def test_policy_passes_arena_gate_with_promoted_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            arena.select(
                ModelCandidate(
                    task="world_model",
                    model_id="esn",
                    protocol="p1",
                    eligible=True,
                    primary_score=0.5,
                )
            )
            self.assertIsNotNone(arena.incumbent("world_model"))
            self.assertIsNone(arena.incumbent("absent_task"))
            policy = WorldModelPolicy(
                LinearDecayModel(),
                {"coal_reserve": eligible_evidence("coal_reserve")},
                arena=arena,
            )
            self.assertTrue(policy.verdict("coal_reserve").eligible)


class EvidenceBuilderTests(unittest.TestCase):
    def test_holdout_errors_reproduce_persistence_comparison(self) -> None:
        rows = synthetic_run()
        model = RecurrentWorldModel(reservoir_size=24, seed=7)
        model.fit(rows)
        errors = holdout_feature_errors(model, rows)
        self.assertEqual(set(errors), set(FEATURE_NAMES))
        model_mse, persistence_mse = errors["coal_reserve"]
        self.assertGreaterEqual(model_mse, 0.0)
        self.assertGreaterEqual(persistence_mse, 0.0)

    def test_evidence_from_folds_requires_minimum_folds(self) -> None:
        folds = [{"coal_reserve": (0.1, 0.9)}, {"coal_reserve": (0.1, 0.9)}]
        evidence = feature_evidence_from_folds(folds, {"coal_reserve": 2.0})
        policy = WorldModelPolicy(LinearDecayModel(), evidence)
        verdict = policy.verdict("coal_reserve")
        self.assertFalse(verdict.eligible)
        self.assertIn("folds", verdict.reason)

    def test_constant_feature_cannot_be_predicted_better_than_persistence(self) -> None:
        folds = [{"progress": (0.0, 0.0)} for _ in range(4)]
        evidence = feature_evidence_from_folds(folds, {"progress": 1.0})
        policy = WorldModelPolicy(LinearDecayModel(), evidence)
        verdict = policy.verdict("progress")
        self.assertFalse(verdict.eligible)
        self.assertIn("exact", verdict.reason)


class RealModelIntegrationTests(unittest.TestCase):
    def test_policy_drives_the_real_recurrent_model(self) -> None:
        rows = synthetic_run()
        model = RecurrentWorldModel(reservoir_size=24, seed=7)
        model.fit(rows)
        policy = WorldModelPolicy(
            model,
            {"coal_reserve": eligible_evidence("coal_reserve")},
        )
        forecast = policy.forecast(rows[-2], "coal_reserve", horizon=3)
        self.assertTrue(forecast.usable, forecast.reason)
        self.assertTrue(forecast.lower <= forecast.value <= forecast.upper)


if __name__ == "__main__":
    unittest.main()
