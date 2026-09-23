"""The discoveries endpoint has to publish what the search found and dropped.

The loop appends one generation to ``runs/evolution_history.jsonl`` every ~17
minutes, and the record it writes contains more than the promoted/rejected
verdict anyone reads back. Four failure modes are pinned here.

First, retention has three states. A generation that recorded no verdict has
unknown retention, not false retention, and a client that receives ``false``
for it reads an invented measurement.

Second, an absent history rendered as a summary of zeros reads as a measured
"nothing was found". A missing file, an empty file and a file whose every line
is unreadable are absences, and the payload has to say so.

Third, a list silently cut at some limit is the defect this panel already
paid for elsewhere: the payload declares the limit, the total and the cut.

Fourth, the payload has to survive ``JSON.parse``: Starlette serialises with
``allow_nan=True`` and the bare ``Infinity``/``NaN`` tokens make the browser
discard the whole update.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factorio_ai_lab.dashboard import app as dashboard_app
from factorio_ai_lab.dashboard.state import DashboardState

DISCOVERIES_URL = "/api/evolution/discoveries"
HISTORY = Path(__file__).resolve().parents[1] / "runs" / "evolution_history.jsonl"


def _row(
    generation: int,
    *,
    achieved: tuple[str, ...] = (),
    route: float | None = None,
    bottleneck: str | None = None,
    promoted: bool | None = None,
) -> dict[str, Any]:
    """A history row. ``promoted=None`` writes a decision with no verdict in it."""
    decision: dict[str, Any] = {"improvements": [], "regressions": []}
    if promoted is not None:
        decision["promoted"] = promoted
    if route is not None:
        decision["improvements"].append(f"route cost improved 7.505→{route}")
    row: dict[str, Any] = {
        "generation": generation,
        "run_id": f"run-{generation}",
        "decision": decision,
        "engineering_progression": {"achieved": list(achieved)},
    }
    if bottleneck is not None:
        row["bottleneck"] = bottleneck
    return row


def _write_history(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _append_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _parse_like_browser(text: str) -> Any:
    """``json.loads`` accepts Infinity/NaN by default; ``JSON.parse`` does not."""

    def reject(token: str) -> Any:
        raise AssertionError(f"non-JSON constant on the wire: {token}")

    return json.loads(text, parse_constant=reject)


def test_three_retention_states_survive_the_json(tmp_path: Path, monkeypatch) -> None:
    path = _write_history(
        tmp_path / "evolution_history.jsonl",
        [
            _row(1, route=7.30, promoted=True),
            _row(2, route=7.28, promoted=False),
            _row(3, route=7.25, promoted=None),
        ],
    )

    monkeypatch.setattr(dashboard_app.state, "evolution_history_path", path)
    response = TestClient(dashboard_app.app).get(DISCOVERIES_URL)

    assert response.status_code == 200
    decoded = _parse_like_browser(response.text)
    assert decoded["measured"] is True

    by_generation = {item["generation"]: item["retained"] for item in decoded["discoveries"]}
    assert by_generation == {1: True, 2: False, 3: None}
    assert '"retained": null' in response.text or '"retained":null' in response.text

    summary = decoded["summary"]
    assert (summary["retained"], summary["discarded"], summary["unknown"]) == (1, 1, 1)
    # The generation with no verdict routed the cheapest of the three. Counting
    # it as discarded would report a saving nobody measured.
    assert summary["best_discarded_route"]["generation"] == 2
    assert summary["best_discarded_route"]["achieved"] == 7.28
    assert summary["best_discarded_route"]["baseline"] == 7.505


def test_most_recent_first_and_truncation_is_declared(tmp_path: Path) -> None:
    path = _write_history(
        tmp_path / "evolution_history.jsonl",
        [_row(generation, route=7.4 - generation / 100) for generation in range(1, 6)],
    )

    payload = DashboardState().discovery_data(history_path=path, limit=2)

    assert payload["measured"] is True
    assert payload["total"] == 5
    assert payload["returned"] == 2
    assert payload["limit"] == 2
    assert payload["truncated"] is True
    assert [item["generation"] for item in payload["discoveries"]] == [5, 4]
    # The summary covers every finding, not only the returned slice.
    assert payload["summary"]["total"] == 5
    assert "5" in payload["reason"] and "2" in payload["reason"]

    whole = DashboardState().discovery_data(history_path=path)
    assert whole["truncated"] is False
    assert whole["returned"] == whole["total"] == 5


def test_missing_history_is_declared_absent_not_summarised_as_zero(tmp_path: Path) -> None:
    payload = DashboardState().discovery_data(history_path=tmp_path / "absent.jsonl")

    assert payload["measured"] is False
    assert payload["summary"] is None
    assert payload["discoveries"] == []
    assert payload["total"] == 0
    assert payload["truncated"] is False
    assert payload["source"]["exists"] is False
    assert payload["source"]["lines_found"] == 0
    assert payload["reason"].strip()


def test_empty_history_does_not_become_a_summary(tmp_path: Path) -> None:
    path = _write_history(tmp_path / "evolution_history.jsonl", [])

    payload = DashboardState().discovery_data(history_path=path)

    assert payload["measured"] is False
    assert payload["summary"] is None
    assert payload["source"]["exists"] is True
    assert payload["source"]["readable"] is True
    assert payload["source"]["lines_found"] == 0
    assert payload["source"]["lines_read"] == 0
    assert "empty" in payload["reason"]


def test_history_with_only_unreadable_lines_is_an_absence(tmp_path: Path) -> None:
    path = tmp_path / "evolution_history.jsonl"
    path.write_text('{"generation": 1,\nnot json at all\n', encoding="utf-8")

    payload = DashboardState().discovery_data(history_path=path)

    assert payload["measured"] is False
    assert payload["summary"] is None
    assert payload["source"]["lines_found"] == 2
    assert payload["source"]["lines_read"] == 0
    assert payload["source"]["lines_unreadable"] == 2
    assert payload["source"]["readable"] is True
    assert payload["reason"].strip()


def test_unopenable_history_is_not_reported_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "evolution_history.jsonl"
    path.write_bytes(b'{"generation": 1}\n\xff\xfe\x00 not utf-8\n')

    payload = DashboardState().discovery_data(history_path=path)

    assert payload["measured"] is False
    assert payload["summary"] is None
    assert payload["source"]["exists"] is True
    assert payload["source"]["readable"] is False
    # Zero lines here means the file could not be opened, and the reason has to
    # say so: reported as "empty" it would read as a run that recorded nothing.
    assert payload["source"]["lines_found"] == 0
    assert "empty" not in payload["reason"]
    assert "could not be read" in payload["reason"]


def test_unreadable_line_is_counted_and_does_not_break_the_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "evolution_history.jsonl"
    path.write_text(
        json.dumps(_row(1, route=7.30, promoted=False))
        + "\n"
        + '{"generation": 2, "decision":\n'
        + "\n"
        + json.dumps(_row(3, bottleneck="power", promoted=True))
        + "\n",
        encoding="utf-8",
    )

    payload = DashboardState().discovery_data(history_path=path)

    assert payload["measured"] is True
    assert payload["source"]["lines_found"] == 3
    assert payload["source"]["lines_read"] == 2
    assert payload["source"]["lines_unreadable"] == 1
    assert payload["total"] == 2

    monkeypatch.setattr(dashboard_app.state, "evolution_history_path", path)
    response = TestClient(dashboard_app.app).get(DISCOVERIES_URL)
    assert response.status_code == 200
    assert response.json()["source"]["lines_unreadable"] == 1


def test_cache_is_invalidated_when_a_generation_is_appended(tmp_path: Path) -> None:
    path = _write_history(
        tmp_path / "evolution_history.jsonl",
        [_row(1, route=7.30, promoted=False), _row(2, route=7.28, promoted=False)],
    )
    state = DashboardState()

    first = state.discovery_data(history_path=path)
    cached = state.discovery_data(history_path=path)
    assert cached is first, "unchanged history must be served from cache"
    assert first["total"] == 2

    _append_row(path, _row(3, route=7.20, promoted=False))
    refreshed = state.discovery_data(history_path=path)

    assert refreshed is not first, "an appended generation must force a reread"
    assert refreshed["total"] == 3
    assert refreshed["summary"]["best_discarded_route"]["achieved"] == 7.20


def test_response_is_strictly_valid_json(tmp_path: Path, monkeypatch) -> None:
    path = _write_history(
        tmp_path / "evolution_history.jsonl",
        [
            _row(1, achieved=("burner_mining",), bottleneck="ore", promoted=False),
            _row(2, route=7.28, bottleneck="power", promoted=True),
        ],
    )

    monkeypatch.setattr(dashboard_app.state, "evolution_history_path", path)
    response = TestClient(dashboard_app.app).get(DISCOVERIES_URL)

    assert response.status_code == 200
    assert "Infinity" not in response.text
    assert "NaN" not in response.text
    decoded = _parse_like_browser(response.text)
    assert decoded["measured"] is True
    assert decoded["summary"]["total"] == decoded["total"]


def test_non_finite_value_is_neutralised_at_the_edge(monkeypatch) -> None:
    def poisoned(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "measured": True,
            "summary": {"best_discarded_route": {"saved": float("inf")}},
            "discoveries": [{"retained": None, "evidence": {"achieved": float("nan")}}],
        }

    monkeypatch.setattr(dashboard_app.state, "discovery_data", poisoned)
    response = TestClient(dashboard_app.app).get(DISCOVERIES_URL)

    assert response.status_code == 200
    assert "Infinity" not in response.text
    assert "NaN" not in response.text
    decoded = _parse_like_browser(response.text)
    assert decoded["summary"]["best_discarded_route"]["saved"] is None
    assert decoded["discoveries"][0]["retained"] is None


@pytest.mark.skipif(not HISTORY.is_file(), reason="no recorded evolution history on disk")
def test_live_history_surfaces_the_discarded_route_improvements() -> None:
    payload = DashboardState().discovery_data()

    if not payload["measured"]:
        pytest.skip(payload["reason"])

    assert payload["source"]["path"].endswith("evolution_history.jsonl")
    assert payload["source"]["lines_read"] > 0

    summary = payload["summary"]
    routes = summary["by_kind"].get("route_improvement")
    assert routes is not None, "the recorded history contains route improvements"
    assert routes["discarded"] >= 1, "route improvements were found and not kept"

    best = summary["best_discarded_route"]
    assert best is not None
    assert best["achieved"] < best["baseline"]
    assert best["saved"] > 0
    assert summary["retained"] + summary["discarded"] + summary["unknown"] == summary["total"]
