"""The survival endpoint must publish the sample gate next to the curve.

The evolution loop appends one generation report every ~17 minutes, and the
panel exists to watch that move. Three failure modes are pinned here.

First, an estimate served without its sample-size verdict reads as if it
transferred to future generations; with the events currently on disk it does
not, and the gate is the only thing that says so.

Second, an absent measurement rendered as an empty curve reads as a measured
zero. A missing directory, an unreadable report and a generation that ran
before the instrumentation are all absences, and the payload has to say so
instead of drawing a flat line.

Third, the payload has to survive ``JSON.parse``: Starlette and FastAPI
serialise with ``allow_nan=True``, and the bare ``Infinity``/``NaN`` tokens are
rejected by the browser, which then discards the update entirely.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from factorio_ai_lab.dashboard import app as dashboard_app
from factorio_ai_lab.dashboard.state import DashboardState

GATE_FIELDS = (
    "n",
    "n_events",
    "n_censored",
    "distinct_event_times",
    "purpose",
    "min_events_required",
    "sufficient",
    "verdict",
    "reason",
)

SURVIVAL_URL = "/api/evolution/survival"


def _write_report(
    directory: Path,
    generation: int,
    runtime_s: float | None,
    halt_cause: str | None,
) -> Path:
    path = directory / f"generation-{generation:04d}-curriculum.json"
    path.write_text(
        json.dumps(
            {
                "generation": generation,
                "challenger": {
                    "configuration": {"variant": "baseline", "stages": 4},
                    "fitness": {
                        "productive_runtime_s": runtime_s,
                        "halt_cause": halt_cause,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _parse_like_browser(text: str) -> Any:
    """``json.loads`` accepts Infinity/NaN by default; ``JSON.parse`` does not."""

    def reject(token: str) -> Any:
        raise AssertionError(f"non-JSON constant on the wire: {token}")

    return json.loads(text, parse_constant=reject)


def test_payload_carries_the_whole_sample_gate(tmp_path: Path) -> None:
    _write_report(tmp_path, 1, 120.0, "fuel_starvation")
    _write_report(tmp_path, 2, 240.0, "power_starvation")
    _write_report(tmp_path, 3, 300.0, "none_observed")

    payload = DashboardState().survival_data(reports_dir=tmp_path)

    assert payload["measured"] is True
    sample = payload["sample"]
    assert set(GATE_FIELDS) <= set(sample)
    assert (sample["n"], sample["n_events"], sample["n_censored"]) == (3, 2, 1)
    assert sample["sufficient"] is False
    assert sample["verdict"] == "insufficient"
    assert sample["reason"].strip()

    # The gate travels with the number: a client reading the curve alone still
    # finds the verdict that qualifies it.
    for section in ("curve", "hazard", "competing_risks"):
        gate = payload[section]["gate"]
        assert set(GATE_FIELDS) <= set(gate)
        assert gate["sufficient"] is False
        assert gate["verdict"] == "insufficient"
        assert gate["reason"].strip()

    times = [point["time_s"] for point in payload["curve"]["points"]]
    assert times == [0.0, 120.0, 240.0]
    assert payload["hazard"]["points"][0]["n_at_risk"] == 3
    assert payload["competing_risks"]["cause_counts"] == {
        "fuel_starvation": 1,
        "power_starvation": 1,
    }
    assert set(payload["competing_risks"]["incidence"]) == {
        "fuel_starvation",
        "power_starvation",
    }


def test_missing_directory_is_declared_absent_not_drawn(tmp_path: Path) -> None:
    payload = DashboardState().survival_data(reports_dir=tmp_path / "absent")

    assert payload["measured"] is False
    assert payload["curve"] is None
    assert payload["hazard"] is None
    assert payload["competing_risks"] is None
    assert payload["reason"].strip()
    assert payload["source"]["exists"] is False
    assert payload["sample"]["n"] == 0
    assert payload["sample"]["verdict"] == "empty"


def test_empty_directory_does_not_become_a_curve(tmp_path: Path) -> None:
    payload = DashboardState().survival_data(reports_dir=tmp_path)

    assert payload["measured"] is False
    assert payload["curve"] is None
    assert payload["source"]["exists"] is True
    assert payload["source"]["reports_found"] == 0
    assert payload["sample"]["verdict"] == "empty"
    assert payload["reason"].strip()


def test_reports_without_instrumentation_are_excluded_not_counted_as_zero(
    tmp_path: Path,
) -> None:
    _write_report(tmp_path, 1, None, None)
    _write_report(tmp_path, 2, None, None)

    payload = DashboardState().survival_data(reports_dir=tmp_path)

    assert payload["measured"] is False
    assert payload["curve"] is None
    assert payload["excluded"]["unknown"] == 2
    assert payload["source"]["reports_found"] == 2
    assert payload["sample"]["n"] == 0
    assert payload["reason"].strip()


def test_unreadable_report_is_counted_as_excluded_and_does_not_break_the_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    _write_report(tmp_path, 1, 120.0, "fuel_starvation")
    _write_report(tmp_path, 2, 240.0, "power_starvation")
    (tmp_path / "generation-0003-truncated.json").write_text(
        '{"generation": 3, "challenger":', encoding="utf-8"
    )

    payload = DashboardState().survival_data(reports_dir=tmp_path)

    assert payload["measured"] is True
    assert payload["excluded"]["unreadable"] == 1
    assert payload["source"]["reports_found"] == 3
    assert payload["source"]["reports_read"] == 2
    assert payload["sample"]["n"] == 2

    monkeypatch.setattr(dashboard_app.state, "generation_reports_dir", tmp_path)
    response = TestClient(dashboard_app.app).get(SURVIVAL_URL)
    assert response.status_code == 200
    assert response.json()["excluded"]["unreadable"] == 1


def test_cache_is_invalidated_when_a_new_report_lands(tmp_path: Path) -> None:
    _write_report(tmp_path, 1, 120.0, "fuel_starvation")
    _write_report(tmp_path, 2, 240.0, "power_starvation")
    state = DashboardState()

    first = state.survival_data(reports_dir=tmp_path)
    cached = state.survival_data(reports_dir=tmp_path)
    assert cached is first, "unchanged report set must be served from cache"
    assert first["sample"]["n"] == 2

    _write_report(tmp_path, 3, 360.0, "fuel_starvation")
    refreshed = state.survival_data(reports_dir=tmp_path)

    assert refreshed is not first, "a new report must force a reread"
    assert refreshed["sample"]["n"] == 3
    assert refreshed["sample"]["n_events"] == 3


def test_cache_is_invalidated_when_a_report_is_rewritten(tmp_path: Path) -> None:
    _write_report(tmp_path, 1, 120.0, "fuel_starvation")
    state = DashboardState()

    first = state.survival_data(reports_dir=tmp_path)
    assert first["curve"]["points"][-1]["time_s"] == 120.0

    path = _write_report(tmp_path, 1, 480.0, "fuel_starvation")
    # Same name and same byte count: the rewrite is only visible in the
    # timestamp, and coarse filesystem granularity would otherwise make the
    # assertion depend on how fast the test ran.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    refreshed = state.survival_data(reports_dir=tmp_path)

    assert refreshed["curve"]["points"][-1]["time_s"] == 480.0


def test_response_is_strictly_valid_json(tmp_path: Path, monkeypatch) -> None:
    _write_report(tmp_path, 1, 120.0, "fuel_starvation")
    _write_report(tmp_path, 2, 120.0, "fuel_starvation")
    _write_report(tmp_path, 3, 240.0, "power_starvation")
    _write_report(tmp_path, 4, 240.0, "none_observed")

    monkeypatch.setattr(dashboard_app.state, "generation_reports_dir", tmp_path)
    response = TestClient(dashboard_app.app).get(SURVIVAL_URL)

    assert response.status_code == 200
    assert "Infinity" not in response.text
    assert "NaN" not in response.text
    decoded = _parse_like_browser(response.text)
    assert decoded["measured"] is True
    assert decoded["sample"]["verdict"] == "insufficient"
    assert decoded["curve"]["gate"]["verdict"] == "insufficient"


def test_live_reports_on_disk_are_served_with_their_gate() -> None:
    payload = DashboardState().survival_data()

    assert set(GATE_FIELDS) <= set(payload["sample"])
    if payload["measured"]:
        assert payload["curve"]["gate"]["verdict"] == payload["sample"]["verdict"]
    else:
        assert payload["curve"] is None
        assert payload["reason"].strip()
