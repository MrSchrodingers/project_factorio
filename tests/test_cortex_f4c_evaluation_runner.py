from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.causal_evaluation import evaluation_sequence_guard
from factorio_ai_lab.cortex.causal_harness import HarnessValidationError


def _review(path: Path, seed: int, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "cortex_f4c_evaluation_pair_review_v1",
                "status": status,
                "seed": seed,
            }
        )
        + "\n"
    )


def test_evaluation_sequence_allows_pass_or_technical_invalid(tmp_path):
    sequence = [20261221, 20261222, 20261223]
    first = evaluation_sequence_guard(tmp_path, sequence, 20261221)
    assert first["position"] == 1

    with pytest.raises(
        HarnessValidationError,
        match="prior evaluation 20261221 has no canonical review",
    ):
        evaluation_sequence_guard(tmp_path, sequence, 20261222)

    _review(
        tmp_path / "runs/f4c_evaluation/20261221/review.json",
        20261221,
        "technical_invalid",
    )
    second = evaluation_sequence_guard(tmp_path, sequence, 20261222)
    assert second["position"] == 2
    assert second["prior_reviews"][0]["status"] == "technical_invalid"

    _review(
        tmp_path / "runs/f4c_evaluation/20261222/review.json",
        20261222,
        "pass",
    )
    third = evaluation_sequence_guard(tmp_path, sequence, 20261223)
    assert third["position"] == 3


def test_evaluation_sequence_rejects_fail_and_future_evidence(tmp_path):
    sequence = [20261221, 20261222, 20261223]
    _review(
        tmp_path / "runs/f4c_evaluation/20261221/review.json",
        20261221,
        "fail",
    )
    with pytest.raises(
        HarnessValidationError,
        match="review blocks continuation",
    ):
        evaluation_sequence_guard(tmp_path, sequence, 20261222)

    _review(
        tmp_path / "runs/f4c_evaluation/20261221/review.json",
        20261221,
        "pass",
    )
    future = tmp_path / "runs/f4c_evaluation/20261223/pair.json"
    future.parent.mkdir(parents=True, exist_ok=True)
    future.write_text("{}\n")
    with pytest.raises(
        HarnessValidationError,
        match="future evaluation evidence exists out of order",
    ):
        evaluation_sequence_guard(tmp_path, sequence, 20261222)
