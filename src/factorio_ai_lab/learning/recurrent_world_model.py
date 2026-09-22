from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from factorio_ai_lab.learning.telemetry import FEATURE_NAMES, feature_vector


@dataclass(frozen=True)
class WorldModelMetrics:
    sample_count: int
    transition_count: int
    validation_mse: float
    persistence_baseline_mse: float
    risk_accuracy: float
    risk_persistence_accuracy: float
    risk_brier: float
    risk_positive_rate: float

    @property
    def beats_persistence(self) -> bool:
        return self.validation_mse < self.persistence_baseline_mse

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "transition_count": self.transition_count,
            "validation_mse": self.validation_mse,
            "persistence_baseline_mse": self.persistence_baseline_mse,
            "beats_persistence": self.beats_persistence,
            "risk_accuracy": self.risk_accuracy,
            "risk_persistence_accuracy": self.risk_persistence_accuracy,
            "risk_brier": self.risk_brier,
            "risk_positive_rate": self.risk_positive_rate,
        }


class RecurrentWorldModel:
    """Echo-state recurrent model trained on Factorio telemetry."""

    def __init__(
        self,
        *,
        reservoir_size: int = 64,
        spectral_radius: float = 0.88,
        leak: float = 0.35,
        ridge: float = 1e-2,
        seed: int = 20260921,
    ) -> None:
        if reservoir_size < 4:
            raise ValueError("reservoir_size must be >= 4")
        self.reservoir_size = reservoir_size
        self.spectral_radius = spectral_radius
        self.leak = leak
        self.ridge = ridge
        self.seed = seed
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.w_in: np.ndarray | None = None
        self.w_res: np.ndarray | None = None
        self.w_out: np.ndarray | None = None
        self.w_risk: np.ndarray | None = None

    @property
    def input_dim(self) -> int:
        return len(FEATURE_NAMES)

    def _init_weights(self) -> None:
        rng = np.random.default_rng(self.seed)
        self.w_in = rng.normal(
            0.0,
            0.35,
            size=(self.reservoir_size, self.input_dim + 1),
        )
        raw = rng.normal(
            0.0,
            1.0 / math.sqrt(self.reservoir_size),
            size=(self.reservoir_size, self.reservoir_size),
        )
        raw *= rng.random(raw.shape) < 0.16
        eigenvalues = np.linalg.eigvals(raw)
        radius = float(np.max(np.abs(eigenvalues))) if eigenvalues.size else 1.0
        if radius <= 1e-9:
            radius = 1.0
        self.w_res = raw * (self.spectral_radius / radius)

    def _normalise(self, x: np.ndarray) -> np.ndarray:
        assert self.mean is not None
        assert self.scale is not None
        return (x - self.mean) / self.scale

    def _step(self, x_norm: np.ndarray, hidden: np.ndarray) -> np.ndarray:
        assert self.w_in is not None
        assert self.w_res is not None
        augmented = np.concatenate(([1.0], x_norm))
        proposal = np.tanh(self.w_in @ augmented + self.w_res @ hidden)
        return (1.0 - self.leak) * hidden + self.leak * proposal

    def _design_row(self, x_norm: np.ndarray, hidden: np.ndarray) -> np.ndarray:
        return np.concatenate(([1.0], x_norm, hidden))

    @staticmethod
    def _group_samples(samples: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for sample in samples:
            run_id = str(sample.get("run_id") or "unknown")
            groups.setdefault(run_id, []).append(sample)
        sequences: list[list[dict[str, Any]]] = []
        for rows in groups.values():
            rows.sort(key=lambda row: float(row.get("timestamp", 0.0)))
            if len(rows) >= 3:
                sequences.append(rows)
        return sequences

    def fit(self, samples: list[dict[str, Any]]) -> WorldModelMetrics:
        sequences = self._group_samples(samples)
        transition_count = sum(max(0, len(seq) - 1) for seq in sequences)
        if transition_count < 24:
            raise ValueError("at least 24 temporal transitions are required")

        matrix = np.asarray(
            [feature_vector(sample) for seq in sequences for sample in seq],
            dtype=np.float64,
        )
        self.mean = matrix.mean(axis=0)
        self.scale = matrix.std(axis=0)
        self.scale[self.scale < 1e-6] = 1.0
        self._init_weights()

        train_rows: list[np.ndarray] = []
        train_targets: list[np.ndarray] = []
        train_risk: list[float] = []
        val_rows: list[np.ndarray] = []
        val_targets: list[np.ndarray] = []
        val_current: list[np.ndarray] = []
        val_risk: list[float] = []
        val_current_risk: list[float] = []

        sequences.sort(
            key=lambda seq: float(seq[-1].get("timestamp", 0.0))
        )
        validation_sequence = sequences[-1] if len(sequences) >= 2 else None

        for sequence in sequences:
            hidden = np.zeros(self.reservoir_size, dtype=np.float64)
            vectors = np.asarray(
                [feature_vector(sample) for sample in sequence],
                dtype=np.float64,
            )
            normalised = self._normalise(vectors)
            temporal_split = max(
                2,
                min(len(sequence) - 1, int(len(sequence) * 0.80)),
            )
            for index in range(len(sequence) - 1):
                hidden = self._step(normalised[index], hidden)
                row = self._design_row(normalised[index], hidden)
                current = normalised[index]
                target = normalised[index + 1]
                delta_target = target - current
                risk = float(bool(sequence[index + 1].get("critical_fault", False)))
                current_risk = float(bool(sequence[index].get("critical_fault", False)))
                use_validation = (
                    sequence is validation_sequence
                    if validation_sequence is not None
                    else index >= temporal_split - 1
                )
                if not use_validation:
                    train_rows.append(row)
                    train_targets.append(delta_target)
                    train_risk.append(risk)
                else:
                    val_rows.append(row)
                    val_targets.append(target)
                    val_current.append(current)
                    val_risk.append(risk)
                    val_current_risk.append(current_risk)

        if not val_rows:
            raise ValueError("validation split produced no transitions")
        if len(train_rows) < 24:
            raise ValueError("training split produced fewer than 24 transitions")

        z = np.vstack(train_rows)
        y = np.vstack(train_targets)
        regulariser = self.ridge * np.eye(z.shape[1], dtype=np.float64)
        regulariser[0, 0] = 0.0
        gram = z.T @ z + regulariser
        self.w_out = np.linalg.solve(gram, z.T @ y)

        risk_target = np.asarray(train_risk, dtype=np.float64)
        positive = max(1.0, float(np.sum(risk_target > 0.5)))
        negative = max(1.0, float(np.sum(risk_target <= 0.5)))
        weights = np.where(
            risk_target > 0.5,
            len(risk_target) / (2.0 * positive),
            len(risk_target) / (2.0 * negative),
        )
        signed_target = np.where(risk_target > 0.5, 1.0, -1.0)
        sqrt_w = np.sqrt(weights)
        weighted_z = z * sqrt_w[:, None]
        weighted_target = signed_target * sqrt_w
        risk_gram = weighted_z.T @ weighted_z + regulariser
        self.w_risk = np.linalg.solve(
            risk_gram,
            weighted_z.T @ weighted_target,
        )

        zv = np.vstack(val_rows)
        yv = np.vstack(val_targets)
        current = np.vstack(val_current)
        predicted_delta = zv @ self.w_out
        prediction = current + predicted_delta
        validation_mse = float(np.mean((prediction - yv) ** 2))
        baseline_mse = float(np.mean((current - yv) ** 2))

        scores = np.clip(zv @ self.w_risk, -20.0, 20.0)
        probabilities = 1.0 / (1.0 + np.exp(-2.0 * scores))
        risk_truth = np.asarray(val_risk, dtype=np.float64)
        risk_prediction = (scores >= 0.0).astype(np.float64)
        risk_accuracy = float(np.mean(risk_prediction == risk_truth))
        risk_persistence = np.asarray(val_current_risk, dtype=np.float64)
        risk_persistence_accuracy = float(np.mean(risk_persistence == risk_truth))
        risk_brier = float(np.mean((probabilities - risk_truth) ** 2))
        risk_positive_rate = float(np.mean(risk_truth))

        return WorldModelMetrics(
            sample_count=len(samples),
            transition_count=transition_count,
            validation_mse=validation_mse,
            persistence_baseline_mse=baseline_mse,
            risk_accuracy=risk_accuracy,
            risk_persistence_accuracy=risk_persistence_accuracy,
            risk_brier=risk_brier,
            risk_positive_rate=risk_positive_rate,
        )

    def predict_next(
        self,
        sample: dict[str, Any],
        *,
        hidden: np.ndarray | None = None,
    ) -> tuple[dict[str, float], float, np.ndarray]:
        if self.w_out is None or self.w_risk is None:
            raise RuntimeError("model is not fitted")
        vector = np.asarray(feature_vector(sample), dtype=np.float64)
        x_norm = self._normalise(vector)
        hidden_state = (
            np.zeros(self.reservoir_size, dtype=np.float64)
            if hidden is None
            else hidden
        )
        next_hidden = self._step(x_norm, hidden_state)
        row = self._design_row(x_norm, next_hidden)
        predicted_delta = row @ self.w_out
        prediction_norm = x_norm + predicted_delta
        assert self.mean is not None
        assert self.scale is not None
        prediction = prediction_norm * self.scale + self.mean
        logit = float(np.clip(row @ self.w_risk, -20.0, 20.0))
        risk = 1.0 / (1.0 + math.exp(-2.0 * logit))
        return (
            {
                name: float(value)
                for name, value in zip(FEATURE_NAMES, prediction, strict=True)
            },
            risk,
            next_hidden,
        )

    def save(self, path: Path) -> None:
        if any(
            value is None
            for value in (
                self.mean,
                self.scale,
                self.w_in,
                self.w_res,
                self.w_out,
                self.w_risk,
            )
        ):
            raise RuntimeError("model is not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            mean=self.mean,
            scale=self.scale,
            w_in=self.w_in,
            w_res=self.w_res,
            w_out=self.w_out,
            w_risk=self.w_risk,
            reservoir_size=np.asarray([self.reservoir_size]),
            spectral_radius=np.asarray([self.spectral_radius]),
            leak=np.asarray([self.leak]),
            ridge=np.asarray([self.ridge]),
            seed=np.asarray([self.seed]),
        )

    @classmethod
    def load(cls, path: Path) -> RecurrentWorldModel:
        payload = np.load(path)
        model = cls(
            reservoir_size=int(payload["reservoir_size"][0]),
            spectral_radius=float(payload["spectral_radius"][0]),
            leak=float(payload["leak"][0]),
            ridge=float(payload["ridge"][0]),
            seed=int(payload["seed"][0]),
        )
        model.mean = payload["mean"]
        model.scale = payload["scale"]
        model.w_in = payload["w_in"]
        model.w_res = payload["w_res"]
        model.w_out = payload["w_out"]
        model.w_risk = payload["w_risk"]
        return model


def load_telemetry(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("connected", False):
            rows.append(row)
    return rows
