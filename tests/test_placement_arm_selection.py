"""No valid placement episode is a measurement, not a crash.

stage_online_learning ran `max(mean_output_by_arm.values())` on a dict built
only from episodes marked valid. When every episode was invalid -- which is
what an inherited world produces, because each arm tries to place a drill on
tiles the ancestor already occupies -- the dict is empty and max() raises
ValueError. The generation died at stage 1, and the service restarted into
the same crash 94 times.

An uninformative bandit is a fact about the run: no arm was measured. The
stage has to say that and let the caller decide, rather than raising an
exception whose text says nothing about placement. This is the same rule the
rest of the project already follows -- absence declared, never substituted.
"""

from __future__ import annotations

import pytest

from factorio_ai_lab.experiments.curriculum_runner import (
    NO_MEASURED_ARM,
    best_compact_arm,
)

ARMS = {"east_near": (4.5, 0.0), "west_near": (-4.5, 0.0), "far": (9.5, 8.5)}


def _history(rows):
    return [
        {"arm": arm, "output": output, "valid": valid}
        for arm, output, valid in rows
    ]


def test_the_best_arm_is_the_compact_one_among_equals() -> None:
    outcome = best_compact_arm(
        arms=ARMS,
        history=_history([("east_near", 10.0, True), ("far", 10.0, True)]),
    )
    assert outcome.arm == "east_near"
    assert outcome.reason is None


def test_more_output_wins_over_compactness() -> None:
    outcome = best_compact_arm(
        arms=ARMS,
        history=_history([("east_near", 1.0, True), ("far", 90.0, True)]),
    )
    assert outcome.arm == "far"


def test_no_valid_episode_is_reported_not_raised() -> None:
    outcome = best_compact_arm(
        arms=ARMS,
        history=_history([("east_near", 0.0, False), ("far", 0.0, False)]),
    )
    assert outcome.arm is None
    assert outcome.reason == NO_MEASURED_ARM


def test_an_empty_history_is_reported_not_raised() -> None:
    outcome = best_compact_arm(arms=ARMS, history=[])
    assert outcome.arm is None
    assert outcome.reason == NO_MEASURED_ARM


def test_a_single_valid_episode_still_decides() -> None:
    # One measurement is thin evidence but it is evidence; refusing it would
    # discard a real reading.
    outcome = best_compact_arm(
        arms=ARMS,
        history=_history([("far", 3.0, True), ("east_near", 0.0, False)]),
    )
    assert outcome.arm == "far"


def test_the_old_behaviour_would_have_raised() -> None:
    # Characterises what was fixed: the dict comprehension keeps only valid
    # rows, so an all-invalid history left max() with nothing.
    rows = _history([("east_near", 0.0, False)])
    means = {r["arm"]: r["output"] for r in rows if r["valid"]}
    assert means == {}
    with pytest.raises(ValueError):
        max(means.values())
