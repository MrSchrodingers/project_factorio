from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from factorio_ai_lab.learning.telemetry import (
    CONTROL_NAMES,
    FEATURE_NAMES,
    control_vector,
    feature_vector,
)


@dataclass(frozen=True)
class TorchWorldModelMetrics:
    sample_count: int
    sequence_count: int
    training_windows: int
    validation_windows: int
    validation_mse: float
    persistence_baseline_mse: float
    risk_accuracy: float
    risk_brier: float
    epochs: int

    @property
    def beats_persistence(self) -> bool:
        return self.validation_mse < self.persistence_baseline_mse

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "sequence_count": self.sequence_count,
            "training_windows": self.training_windows,
            "validation_windows": self.validation_windows,
            "validation_mse": self.validation_mse,
            "persistence_baseline_mse": self.persistence_baseline_mse,
            "beats_persistence": self.beats_persistence,
            "risk_accuracy": self.risk_accuracy,
            "risk_brier": self.risk_brier,
            "epochs": self.epochs,
        }


class GRUWorldNet(nn.Module):
    def __init__(
        self,
        input_dim: int,
        hidden_size: int,
        output_dim: int,
    ) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_dim,
            hidden_size,
            num_layers=2,
            batch_first=True,
            dropout=0.10,
        )
        self.norm = nn.LayerNorm(hidden_size)
        self.next_state = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.SiLU(),
            nn.Linear(hidden_size, output_dim),
        )
        self.risk = nn.Sequential(
            nn.Linear(hidden_size, max(8, hidden_size // 2)),
            nn.SiLU(),
            nn.Linear(max(8, hidden_size // 2), 1),
        )

    def forward(self, sequence: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        encoded, _ = self.gru(sequence)
        hidden = self.norm(encoded[:, -1])
        return self.next_state(hidden), self.risk(hidden).squeeze(-1)


class TorchGRUWorldModel:
    def __init__(
        self,
        *,
        hidden_size: int = 96,
        sequence_length: int = 8,
        learning_rate: float = 2e-3,
        weight_decay: float = 1e-4,
        seed: int = 20260921,
        device: str = "cpu",
    ) -> None:
        if sequence_length < 2:
            raise ValueError("sequence_length must be >= 2")
        self.hidden_size = hidden_size
        self.sequence_length = sequence_length
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.seed = seed
        self.device = torch.device(device)
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.model = GRUWorldNet(
            len(FEATURE_NAMES) + len(CONTROL_NAMES),
            hidden_size,
            len(FEATURE_NAMES),
        ).to(self.device)

    @staticmethod
    def _group_samples(samples: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for sample in samples:
            run_id = str(sample.get("run_id") or "unknown")
            groups.setdefault(run_id, []).append(sample)
        sequences: list[list[dict[str, Any]]] = []
        for rows in groups.values():
            rows.sort(key=lambda row: float(row.get("timestamp", 0.0)))
            if len(rows) >= 4:
                sequences.append(rows)
        return sequences

    def _build_windows(
        self,
        sequences: list[list[dict[str, Any]]],
    ) -> tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
    ]:
        all_vectors = np.asarray(
            [feature_vector(row) for seq in sequences for row in seq],
            dtype=np.float32,
        )
        self.mean = all_vectors.mean(axis=0).astype(np.float32)
        self.scale = all_vectors.std(axis=0).astype(np.float32)
        self.scale[self.scale < 1e-6] = 1.0

        train_x: list[np.ndarray] = []
        train_y: list[np.ndarray] = []
        train_risk: list[float] = []
        val_x: list[np.ndarray] = []
        val_y: list[np.ndarray] = []
        val_risk: list[float] = []

        for rows in sequences:
            vectors = np.asarray(
                [feature_vector(row) for row in rows],
                dtype=np.float32,
            )
            controls = np.asarray(
                [control_vector(row) for row in rows],
                dtype=np.float32,
            )
            normalised = (vectors - self.mean) / self.scale
            model_inputs = np.concatenate(
                [normalised, controls],
                axis=1,
            )
            split = max(
                self.sequence_length + 1,
                min(len(rows) - 1, int(len(rows) * 0.80)),
            )
            for target_index in range(self.sequence_length, len(rows)):
                start = target_index - self.sequence_length
                x = model_inputs[start:target_index]
                y = normalised[target_index]
                risk = float(bool(rows[target_index].get("critical_fault", False)))
                if target_index < split:
                    train_x.append(x)
                    train_y.append(y)
                    train_risk.append(risk)
                else:
                    val_x.append(x)
                    val_y.append(y)
                    val_risk.append(risk)

        if not train_x or not val_x:
            raise ValueError("insufficient temporal windows for train/validation split")
        return (
            np.stack(train_x),
            np.stack(train_y),
            np.asarray(train_risk, dtype=np.float32),
            np.stack(val_x),
            np.stack(val_y),
            np.asarray(val_risk, dtype=np.float32),
        )

    def fit(
        self,
        samples: list[dict[str, Any]],
        *,
        epochs: int = 120,
        batch_size: int = 64,
        patience: int = 18,
    ) -> TorchWorldModelMetrics:
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        sequences = self._group_samples(samples)
        if sum(len(seq) for seq in sequences) < 48:
            raise ValueError("at least 48 telemetry samples are required")

        (
            train_x,
            train_y,
            train_risk,
            val_x,
            val_y,
            val_risk,
        ) = self._build_windows(sequences)

        x_train = torch.from_numpy(train_x).to(self.device)
        y_train = torch.from_numpy(train_y).to(self.device)
        r_train = torch.from_numpy(train_risk).to(self.device)
        x_val = torch.from_numpy(val_x).to(self.device)
        y_val = torch.from_numpy(val_y).to(self.device)
        r_val = torch.from_numpy(val_risk).to(self.device)

        optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay,
        )
        state_loss = nn.SmoothL1Loss()
        positive = float(train_risk.sum())
        negative = float(len(train_risk) - positive)
        pos_weight = torch.tensor(
            [max(1.0, negative / max(positive, 1.0))],
            dtype=torch.float32,
            device=self.device,
        )
        risk_loss = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        best = math.inf
        best_state: dict[str, torch.Tensor] | None = None
        stale = 0
        completed_epochs = 0
        generator = torch.Generator().manual_seed(self.seed + 99)

        for epoch in range(epochs):
            order = torch.randperm(len(x_train), generator=generator)
            self.model.train()
            for start in range(0, len(order), batch_size):
                indices = order[start : start + batch_size].to(self.device)
                prediction, risk_logits = self.model(x_train[indices])
                loss = state_loss(prediction, y_train[indices])
                loss = loss + 0.25 * risk_loss(
                    risk_logits,
                    r_train[indices],
                )
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()

            self.model.eval()
            with torch.no_grad():
                prediction, risk_logits = self.model(x_val)
                validation = torch.mean((prediction - y_val) ** 2).item()
                risk_component = risk_loss(risk_logits, r_val).item()
                objective = validation + 0.05 * risk_component
            completed_epochs = epoch + 1
            if objective + 1e-7 < best:
                best = objective
                best_state = {
                    key: value.detach().cpu().clone()
                    for key, value in self.model.state_dict().items()
                }
                stale = 0
            else:
                stale += 1
                if stale >= patience:
                    break

        if best_state is not None:
            self.model.load_state_dict(best_state)
        self.model.eval()
        with torch.no_grad():
            prediction, risk_logits = self.model(x_val)
            validation_mse = float(torch.mean((prediction - y_val) ** 2).item())
            persistence = x_val[:, -1, : len(FEATURE_NAMES)]
            persistence_mse = float(
                torch.mean((persistence - y_val) ** 2).item()
            )
            probabilities = torch.sigmoid(risk_logits)
            risk_prediction = (probabilities >= 0.5).float()
            risk_accuracy = float(
                torch.mean((risk_prediction == r_val).float()).item()
            )
            risk_brier = float(torch.mean((probabilities - r_val) ** 2).item())

        return TorchWorldModelMetrics(
            sample_count=len(samples),
            sequence_count=len(sequences),
            training_windows=len(train_x),
            validation_windows=len(val_x),
            validation_mse=validation_mse,
            persistence_baseline_mse=persistence_mse,
            risk_accuracy=risk_accuracy,
            risk_brier=risk_brier,
            epochs=completed_epochs,
        )

    def predict_next(
        self,
        sequence: list[dict[str, Any]],
    ) -> tuple[dict[str, float], float]:
        if self.mean is None or self.scale is None:
            raise RuntimeError("model is not fitted")
        if len(sequence) < self.sequence_length:
            raise ValueError("sequence is shorter than sequence_length")
        rows = sequence[-self.sequence_length :]
        vectors = np.asarray(
            [feature_vector(row) for row in rows],
            dtype=np.float32,
        )
        controls = np.asarray(
            [control_vector(row) for row in rows],
            dtype=np.float32,
        )
        normalised = (vectors - self.mean) / self.scale
        model_inputs = np.concatenate(
            [normalised, controls],
            axis=1,
        )
        tensor = torch.from_numpy(model_inputs[None, ...]).to(self.device)
        self.model.eval()
        with torch.no_grad():
            prediction, risk_logits = self.model(tensor)
        prediction_np = prediction[0].cpu().numpy() * self.scale + self.mean
        risk = float(torch.sigmoid(risk_logits)[0].item())
        return (
            {
                name: float(value)
                for name, value in zip(FEATURE_NAMES, prediction_np, strict=True)
            },
            risk,
        )

    def save(self, path: Path) -> None:
        if self.mean is None or self.scale is None:
            raise RuntimeError("model is not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.model.state_dict(),
                "mean": self.mean,
                "scale": self.scale,
                "hidden_size": self.hidden_size,
                "sequence_length": self.sequence_length,
                "learning_rate": self.learning_rate,
                "weight_decay": self.weight_decay,
                "seed": self.seed,
                "feature_names": FEATURE_NAMES,
                "control_names": CONTROL_NAMES,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path, *, device: str = "cpu") -> TorchGRUWorldModel:
        payload = torch.load(path, map_location=device, weights_only=False)
        model = cls(
            hidden_size=int(payload["hidden_size"]),
            sequence_length=int(payload["sequence_length"]),
            learning_rate=float(payload["learning_rate"]),
            weight_decay=float(payload["weight_decay"]),
            seed=int(payload["seed"]),
            device=device,
        )
        if tuple(payload["feature_names"]) != FEATURE_NAMES:
            raise ValueError("world-model feature schema mismatch")
        if tuple(payload.get("control_names", ())) != CONTROL_NAMES:
            raise ValueError("world-model control schema mismatch")
        model.mean = np.asarray(payload["mean"], dtype=np.float32)
        model.scale = np.asarray(payload["scale"], dtype=np.float32)
        model.model.load_state_dict(payload["state_dict"])
        model.model.eval()
        return model
