from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from factorio_ai_lab.learning.model_arena import ModelArena, ModelCandidate
from factorio_ai_lab.learning.recurrent_world_model import (
    RecurrentWorldModel,
    load_telemetry,
)
from factorio_ai_lab.learning.telemetry import FEATURE_NAMES, feature_vector
from factorio_ai_lab.learning.torch_world_model import TorchGRUWorldModel

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
DEFAULT_TELEMETRY = RUNS_DIR / "telemetry" / "world_samples.jsonl"
MODEL_DIR = RUNS_DIR / "models"
ESN_MODEL_PATH = MODEL_DIR / "echo_state_world_model.npz"
GRU_MODEL_PATH = MODEL_DIR / "gru_world_model.pt"
METADATA_PATH = MODEL_DIR / "recurrent_world_model.json"
MODEL_REGISTRY = MODEL_DIR / "model_registry.json"


def _evaluate_holdout(
    model: TorchGRUWorldModel,
    rows: list[dict[str, Any]],
) -> dict[str, float | int | bool]:
    rows = sorted(rows, key=lambda row: float(row.get("timestamp", 0.0)))
    length = model.sequence_length
    if len(rows) <= length:
        raise ValueError("holdout run is too short")

    squared_errors: list[float] = []
    persistence_errors: list[float] = []
    risk_errors: list[float] = []
    risk_hits = 0
    count = 0

    assert model.mean is not None
    assert model.scale is not None

    for index in range(length, len(rows)):
        context = rows[index - length : index]
        target = np.asarray(feature_vector(rows[index]), dtype=np.float32)
        previous = np.asarray(feature_vector(rows[index - 1]), dtype=np.float32)
        prediction_map, risk = model.predict_next(context)
        prediction = np.asarray(
            [prediction_map[name] for name in FEATURE_NAMES],
            dtype=np.float32,
        )
        scale = model.scale
        squared_errors.append(
            float(np.mean(((prediction - target) / scale) ** 2))
        )
        persistence_errors.append(
            float(np.mean(((previous - target) / scale) ** 2))
        )
        truth = float(bool(rows[index].get("critical_fault", False)))
        risk_errors.append((risk - truth) ** 2)
        risk_hits += int((risk >= 0.5) == bool(truth))
        count += 1

    mse = float(np.mean(squared_errors))
    persistence = float(np.mean(persistence_errors))
    return {
        "windows": count,
        "validation_mse": mse,
        "persistence_baseline_mse": persistence,
        "beats_persistence": mse < persistence,
        "risk_accuracy": risk_hits / max(1, count),
        "risk_brier": float(np.mean(risk_errors)),
    }


def _generation_holdout(
    samples: list[dict[str, Any]],
    *,
    hidden_size: int,
    sequence_length: int,
    seed: int,
) -> dict[str, Any]:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in samples:
        run_id = str(row.get("run_id") or "unknown")
        if run_id.startswith("curriculum-"):
            by_run[run_id].append(row)

    eligible = {
        run_id: rows
        for run_id, rows in by_run.items()
        if len(rows) >= max(48, sequence_length + 12)
    }
    if len(eligible) < 3:
        return {
            "usable": False,
            "reason": "need at least three independent lab generations",
            "folds": [],
        }

    folds: list[dict[str, Any]] = []
    ordered = sorted(eligible)
    # Keep runtime bounded while preferring the most recent independent generations.
    for fold_index, holdout_run in enumerate(ordered[-4:]):
        training = [
            row
            for run_id, rows in eligible.items()
            if run_id != holdout_run
            for row in rows
        ]
        model = TorchGRUWorldModel(
            hidden_size=hidden_size,
            sequence_length=sequence_length,
            seed=seed + fold_index + 1,
        )
        try:
            model.fit(
                training,
                epochs=90,
                batch_size=64,
                patience=12,
            )
            result = _evaluate_holdout(model, eligible[holdout_run])
            result["run_id"] = holdout_run
            folds.append(result)
        except ValueError as exc:
            folds.append(
                {
                    "run_id": holdout_run,
                    "windows": 0,
                    "beats_persistence": False,
                    "error": str(exc),
                }
            )

    valid = [fold for fold in folds if "validation_mse" in fold]
    if not valid:
        return {"usable": False, "folds": folds, "reason": "no valid folds"}

    wins = sum(bool(fold["beats_persistence"]) for fold in valid)
    win_rate = wins / len(valid)
    mse = float(np.mean([float(fold["validation_mse"]) for fold in valid]))
    baseline = float(
        np.mean([float(fold["persistence_baseline_mse"]) for fold in valid])
    )
    brier = float(np.mean([float(fold["risk_brier"]) for fold in valid]))
    accuracy = float(np.mean([float(fold["risk_accuracy"]) for fold in valid]))
    usable = (
        len(valid) >= 3
        and win_rate >= 0.75
        and mse < baseline
        and brier <= 0.30
    )
    return {
        "folds": folds,
        "valid_folds": len(valid),
        "wins": wins,
        "win_rate": win_rate,
        "mean_validation_mse": mse,
        "mean_persistence_mse": baseline,
        "mean_risk_accuracy": accuracy,
        "mean_risk_brier": brier,
        "usable": usable,
    }


def train(
    *,
    telemetry_path: Path = DEFAULT_TELEMETRY,
    reservoir_size: int = 64,
    gru_hidden_size: int = 96,
    gru_sequence_length: int = 8,
    seed: int = 20260921,
) -> dict[str, Any]:
    samples = load_telemetry(telemetry_path)
    controlled_samples = [
        row
        for row in samples
        if isinstance(row.get("action"), dict)
        and bool(row.get("action", {}).get("action_id"))
    ]
    controlled_runs = sorted(
        {
            str(row.get("run_id"))
            for row in controlled_samples
            if row.get("run_id")
        }
    )
    metadata: dict[str, Any] = {
        "trained_at": datetime.now(UTC).isoformat(),
        "telemetry_path": str(telemetry_path.relative_to(PROJECT_ROOT)),
        "sample_count": len(samples),
        "action_labeled_samples": len(controlled_samples),
        "action_labeled_runs": len(controlled_runs),
        "action_conditioned": True,
        "baselines": {},
        "candidates": {},
        "active_model": None,
        "usable": False,
    }

    esn = RecurrentWorldModel(
        reservoir_size=reservoir_size,
        seed=seed,
    )
    try:
        esn_metrics = esn.fit(samples)
        esn.save(ESN_MODEL_PATH)
        metadata["baselines"]["echo_state_network"] = {
            **esn_metrics.to_dict(),
            "model_path": str(ESN_MODEL_PATH.relative_to(PROJECT_ROOT)),
        }
    except ValueError as exc:
        metadata["baselines"]["echo_state_network"] = {
            "usable": False,
            "reason": str(exc),
        }
        esn_metrics = None

    generation_holdout = _generation_holdout(
        controlled_samples,
        hidden_size=gru_hidden_size,
        sequence_length=gru_sequence_length,
        seed=seed,
    )
    metadata["generation_holdout"] = generation_holdout

    gru = TorchGRUWorldModel(
        hidden_size=gru_hidden_size,
        sequence_length=gru_sequence_length,
        seed=seed,
    )
    try:
        if len(controlled_samples) < 500 or len(controlled_runs) < 3:
            raise ValueError(
                "action-conditioned GRU requires at least 500 labeled "
                "samples across three independent runs"
            )
        gru_metrics = gru.fit(controlled_samples)
        gru.save(GRU_MODEL_PATH)
        esn_mse = (
            esn_metrics.validation_mse
            if esn_metrics is not None
            else float("inf")
        )
        beats_esn = gru_metrics.validation_mse < esn_mse
        usable = (
            gru_metrics.beats_persistence
            and beats_esn
            and gru_metrics.validation_windows >= 12
            and gru_metrics.risk_brier <= 0.30
            and bool(generation_holdout.get("usable"))
        )
        metadata["candidates"]["gru"] = {
            **gru_metrics.to_dict(),
            "beats_echo_state": beats_esn,
            "model_path": str(GRU_MODEL_PATH.relative_to(PROJECT_ROOT)),
            "usable": usable,
        }
        if usable:
            metadata["active_model"] = "gru"
            metadata["usable"] = True
        elif esn_metrics is not None and esn_metrics.beats_persistence:
            metadata["active_model"] = "echo_state_network"
            metadata["usable"] = True
    except ValueError as exc:
        metadata["candidates"]["gru"] = {
            "usable": False,
            "reason": str(exc),
        }
        if esn_metrics is not None and esn_metrics.beats_persistence:
            metadata["active_model"] = "echo_state_network"
            metadata["usable"] = True

    holdout_mse = generation_holdout.get("mean_validation_mse")
    holdout_baseline = generation_holdout.get("mean_persistence_mse")
    normalized_error = (
        float(holdout_mse) / max(float(holdout_baseline), 1e-12)
        if isinstance(holdout_mse, (int, float))
        and isinstance(holdout_baseline, (int, float))
        else float("inf")
    )
    model_candidate = ModelCandidate(
        task="world_model",
        model_id="torch_gru",
        protocol="world-v2-action-conditioned-generation-holdout-latest4",
        eligible=bool(metadata.get("usable")) and metadata.get("active_model") == "gru",
        primary_score=normalized_error,
        secondary_score=(
            float(generation_holdout.get("mean_risk_brier"))
            if isinstance(generation_holdout.get("mean_risk_brier"), (int, float))
            else 1.0
        ),
        artifact=str(GRU_MODEL_PATH.relative_to(PROJECT_ROOT)),
        metadata={
            "holdout_win_rate": generation_holdout.get("win_rate"),
            "sample_count": len(controlled_samples),
            "action_labeled_runs": len(controlled_runs),
        },
    )
    metadata["model_selection"] = ModelArena(MODEL_REGISTRY).select(
        model_candidate
    ).to_dict()

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    parser.add_argument("--reservoir-size", type=int, default=64)
    parser.add_argument("--gru-hidden-size", type=int, default=96)
    parser.add_argument("--gru-sequence-length", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260921)
    args = parser.parse_args()
    print(
        json.dumps(
            train(
                telemetry_path=args.telemetry,
                reservoir_size=args.reservoir_size,
                gru_hidden_size=args.gru_hidden_size,
                gru_sequence_length=args.gru_sequence_length,
                seed=args.seed,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
