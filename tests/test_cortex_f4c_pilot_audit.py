from pathlib import Path

import pytest

from factorio_ai_lab.cortex.causal_harness import HarnessValidationError
from factorio_ai_lab.cortex.causal_pilot_runtime import pilot_sequence_guard


def _review(path: Path, seed: int, status: str = "pass") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"schema_version":"cortex_f4c_pilot_pair_review_v1",'
        f'"status":"{status}","seed":{seed}' "}\n"
    )


def test_sequence_guard_requires_prior_pass_review(tmp_path):
    sequence = [20261201, 20261202, 20261203]
    first = pilot_sequence_guard(tmp_path, sequence, 20261201)
    assert first["position"] == 1
    assert first["prior_reviews"] == []

    with pytest.raises(
        HarnessValidationError,
        match="prior pilot 20261201 has no PASS review",
    ):
        pilot_sequence_guard(tmp_path, sequence, 20261202)

    _review(
        tmp_path / "runs/f4c_pilot/20261201/review.json",
        20261201,
    )
    second = pilot_sequence_guard(tmp_path, sequence, 20261202)
    assert second["position"] == 2
    assert [row["seed"] for row in second["prior_reviews"]] == [20261201]


def test_sequence_guard_rejects_failed_review_and_future_evidence(tmp_path):
    sequence = [20261201, 20261202, 20261203]
    _review(
        tmp_path / "runs/f4c_pilot/20261201/review.json",
        20261201,
        status="fail",
    )
    with pytest.raises(
        HarnessValidationError,
        match="review is not PASS",
    ):
        pilot_sequence_guard(tmp_path, sequence, 20261202)

    _review(
        tmp_path / "runs/f4c_pilot/20261201/review.json",
        20261201,
    )
    future = tmp_path / "runs/f4c_pilot/20261203/pair.json"
    future.parent.mkdir(parents=True, exist_ok=True)
    future.write_text("{}\n")
    with pytest.raises(
        HarnessValidationError,
        match="future pilot evidence exists out of order",
    ):
        pilot_sequence_guard(tmp_path, sequence, 20261202)
