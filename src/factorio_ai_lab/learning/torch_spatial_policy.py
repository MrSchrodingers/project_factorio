from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.spatial_policy import (
    ACTION_DIM,
    FEATURE_DIM,
    LOCAL_SIZE,
    encode_state,
)
from factorio_ai_lab.planning.astar import CARDINAL


class SpatialAttentionNet(nn.Module):
    def __init__(
        self,
        *,
        d_model: int = 48,
        heads: int = 4,
        layers: int = 2,
    ) -> None:
        super().__init__()
        if d_model % heads:
            raise ValueError("d_model must be divisible by heads")
        token_count = LOCAL_SIZE * LOCAL_SIZE
        self.occupancy = nn.Linear(1, d_model)
        self.position = nn.Parameter(
            torch.zeros(1, token_count, d_model)
        )
        self.context = nn.Sequential(
            nn.Linear(2 + len(CARDINAL), d_model),
            nn.SiLU(),
            nn.Linear(d_model, d_model),
        )
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=heads,
            dim_feedforward=d_model * 3,
            dropout=0.05,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            encoder_layer,
            num_layers=layers,
        )
        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model),
            nn.SiLU(),
            nn.Linear(d_model, ACTION_DIM),
        )
        nn.init.normal_(self.position, std=0.02)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        token_count = LOCAL_SIZE * LOCAL_SIZE
        occupancy = features[:, :token_count].unsqueeze(-1)
        context = features[:, token_count:]
        tokens = self.occupancy(occupancy)
        tokens = tokens + self.position + self.context(context).unsqueeze(1)
        encoded = self.encoder(tokens)
        pooled = encoded.mean(dim=1)
        return self.head(pooled)


@dataclass(frozen=True)
class TorchSpatialMetrics:
    training_examples: int
    validation_examples: int
    validation_accuracy: float
    epochs: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "training_examples": self.training_examples,
            "validation_examples": self.validation_examples,
            "validation_accuracy": self.validation_accuracy,
            "epochs": self.epochs,
        }


class TorchSpatialPolicy:
    def __init__(
        self,
        *,
        d_model: int = 48,
        heads: int = 4,
        layers: int = 2,
        seed: int = 20260921,
        device: str = "cpu",
    ) -> None:
        torch.manual_seed(seed)
        self.d_model = d_model
        self.heads = heads
        self.layers = layers
        self.seed = seed
        self.device = torch.device(device)
        self.model = SpatialAttentionNet(
            d_model=d_model,
            heads=heads,
            layers=layers,
        ).to(self.device)

    def fit(
        self,
        x: np.ndarray,
        y: np.ndarray,
        *,
        validation: tuple[np.ndarray, np.ndarray] | None = None,
        epochs: int = 45,
        batch_size: int = 256,
        learning_rate: float = 1.5e-3,
        patience: int = 8,
    ) -> TorchSpatialMetrics:
        if x.ndim != 2 or x.shape[1] != FEATURE_DIM:
            raise ValueError("unexpected feature shape")
        if len(x) < 16:
            raise ValueError("at least 16 route decisions are required")

        x_train = torch.as_tensor(x, dtype=torch.float32, device=self.device)
        y_train = torch.as_tensor(y, dtype=torch.long, device=self.device)
        if validation is None:
            x_val, y_val = x, y
        else:
            x_val, y_val = validation
        x_validation = torch.as_tensor(
            x_val,
            dtype=torch.float32,
            device=self.device,
        )
        y_validation = torch.as_tensor(
            y_val,
            dtype=torch.long,
            device=self.device,
        )

        optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=learning_rate,
            weight_decay=1e-4,
        )
        criterion = nn.CrossEntropyLoss()
        generator = torch.Generator().manual_seed(self.seed + 17)
        best_accuracy = -1.0
        best_state: dict[str, torch.Tensor] | None = None
        stale = 0
        completed = 0

        for epoch in range(epochs):
            order = torch.randperm(len(x_train), generator=generator)
            self.model.train()
            for start in range(0, len(order), batch_size):
                indices = order[start : start + batch_size].to(self.device)
                logits = self.model(x_train[indices])
                loss = criterion(logits, y_train[indices])
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                optimizer.step()

            self.model.eval()
            with torch.no_grad():
                prediction = self.model(x_validation).argmax(dim=1)
                accuracy = float(
                    (prediction == y_validation).float().mean().item()
                )
            completed = epoch + 1
            if accuracy > best_accuracy + 1e-5:
                best_accuracy = accuracy
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
        return TorchSpatialMetrics(
            training_examples=len(x),
            validation_examples=len(x_val),
            validation_accuracy=best_accuracy,
            epochs=completed,
        )

    def logits(self, x: np.ndarray) -> np.ndarray:
        tensor = torch.as_tensor(
            x,
            dtype=torch.float32,
            device=self.device,
        )
        self.model.eval()
        with torch.no_grad():
            return self.model(tensor).cpu().numpy()

    def accuracy(self, x: np.ndarray, y: np.ndarray) -> float:
        prediction = np.argmax(self.logits(x), axis=1)
        return float(np.mean(prediction == y))

    def rollout(
        self,
        start: GridPoint,
        goal: GridPoint,
        *,
        is_blocked: Callable[[GridPoint], bool],
        in_bounds: Callable[[GridPoint], bool],
        max_steps: int = 512,
    ) -> tuple[GridPoint, ...] | None:
        current = start
        previous: tuple[int, int] | None = None
        path = [current]
        visits: dict[tuple[GridPoint, tuple[int, int] | None], int] = {}

        for _ in range(max_steps):
            if current == goal:
                return tuple(path)
            state = (current, previous)
            visits[state] = visits.get(state, 0) + 1
            if visits[state] > 2:
                return None

            encoded = encode_state(
                point=current,
                goal=goal,
                is_blocked=is_blocked,
                in_bounds=in_bounds,
                previous_direction=previous,
            )
            scores = self.logits(encoded[None, :])[0]
            ranked = np.argsort(scores)[::-1]
            chosen: tuple[int, int] | None = None
            for index in ranked:
                direction = CARDINAL[int(index)]
                nxt = GridPoint(
                    current.x + direction[0],
                    current.y + direction[1],
                )
                if not in_bounds(nxt) or (nxt != goal and is_blocked(nxt)):
                    continue
                if visits.get((nxt, direction), 0) >= 2:
                    continue
                chosen = direction
                break
            if chosen is None:
                return None
            current = GridPoint(
                current.x + chosen[0],
                current.y + chosen[1],
            )
            previous = chosen
            path.append(current)

        return tuple(path) if current == goal else None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.model.state_dict(),
                "d_model": self.d_model,
                "heads": self.heads,
                "layers": self.layers,
                "seed": self.seed,
                "feature_dim": FEATURE_DIM,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path, *, device: str = "cpu") -> TorchSpatialPolicy:
        payload = torch.load(path, map_location=device, weights_only=False)
        if int(payload["feature_dim"]) != FEATURE_DIM:
            raise ValueError("spatial-policy feature schema mismatch")
        policy = cls(
            d_model=int(payload["d_model"]),
            heads=int(payload["heads"]),
            layers=int(payload["layers"]),
            seed=int(payload["seed"]),
            device=device,
        )
        policy.model.load_state_dict(payload["state_dict"])
        policy.model.eval()
        return policy
