"""The stage journal must record the failure the FLE actually returned.

The gym environment returns {"error_occurred": bool, "result": str, ...}
(fle/env/gym_env/environment.py:504) and has no "error" key. Reading one
yielded None on every failure, so eleven consecutive generations recorded
`"error": null` for a stage that aborted mid-script, and the only surviving
number -- a metric defaulted to 0.0 because its variable was never assigned --
pointed at the wrong cause entirely.
"""

from __future__ import annotations

from factorio_ai_lab.experiments.curriculum_runner import _step_error_text


def test_successful_step_has_no_error_text() -> None:
    assert _step_error_text({"error_occurred": False, "result": "ok"}) is None


def test_missing_info_is_tolerated() -> None:
    assert _step_error_text(None) is None
    assert _step_error_text({}) is None


def test_failure_reports_the_result_field() -> None:
    info = {
        "error_occurred": True,
        "result": "Error: Cannot move. Path not found to (27.5, 84.5)",
    }
    text = _step_error_text(info)
    assert text is not None
    assert "Path not found" in text


def test_legacy_error_key_is_not_consulted() -> None:
    # A payload shaped like the old (wrong) expectation must still surface the
    # real message, not the absent key.
    info = {"error_occurred": True, "error": None, "result": "AttributeError: x"}
    assert _step_error_text(info) == "AttributeError: x"


def test_failure_without_result_stays_none() -> None:
    assert _step_error_text({"error_occurred": True, "result": None}) is None
    assert _step_error_text({"error_occurred": True, "result": "   "}) is None


def test_long_messages_are_truncated() -> None:
    info = {"error_occurred": True, "result": "x" * 5000}
    text = _step_error_text(info)
    assert text is not None
    assert len(text) == 1200
