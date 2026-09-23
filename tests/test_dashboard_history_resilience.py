"""A half-written line must not erase the history the panel shows.

The loop appends to runs/evolution_history.jsonl while the dashboard reads
it, so a partially written last line is an expected state, not a corruption.
`evolution_data` wrapped the whole parse in one try and answered `history =
[]` on the first JSONDecodeError: one truncated line and the panel reports
zero generations, with nothing said about why.

That is the failure shape this project keeps paying for -- an absence served
as a measurement. The reader already built for the discoveries endpoint skips
and counts the bad line; this makes the older path use it too.
"""

from __future__ import annotations

import json

from factorio_ai_lab.dashboard.state import DashboardState


def _history_file(tmp_path, rows, *, trailing: str | None = None):
    path = tmp_path / "evolution_history.jsonl"
    lines = [json.dumps(row) for row in rows]
    if trailing is not None:
        lines.append(trailing)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _rows(count):
    return [
        {"generation": index, "run_id": f"run-{index}", "decision": {"promoted": False}}
        for index in range(1, count + 1)
    ]


def _evolution(state, path):
    return state.evolution_data(research={"evolution": {}})


def test_a_truncated_last_line_does_not_erase_the_history(tmp_path) -> None:
    path = _history_file(tmp_path, _rows(5), trailing='{"generation": 6, "run_i')
    state = DashboardState()
    state.evolution_history_path = path

    payload = _evolution(state, path)
    generations = [row.get("generation") for row in payload["history"]]
    assert generations == [1, 2, 3, 4, 5], f"historia perdida: {generations}"


def test_the_unreadable_line_is_reported_not_hidden(tmp_path) -> None:
    path = _history_file(tmp_path, _rows(3), trailing="{oops")
    state = DashboardState()
    state.evolution_history_path = path

    payload = _evolution(state, path)
    assert payload["history_lines_unreadable"] == 1
    assert payload["history_lines_found"] == 4


def test_a_clean_history_reports_no_unreadable_lines(tmp_path) -> None:
    path = _history_file(tmp_path, _rows(3))
    state = DashboardState()
    state.evolution_history_path = path

    payload = _evolution(state, path)
    assert payload["history_lines_unreadable"] == 0
    assert len(payload["history"]) == 3


def test_a_missing_history_is_empty_without_raising(tmp_path) -> None:
    state = DashboardState()
    state.evolution_history_path = tmp_path / "absent.jsonl"

    payload = _evolution(state, state.evolution_history_path)
    assert payload["history"] == []
    assert payload["history_lines_found"] == 0


def test_only_the_most_recent_rows_are_served(tmp_path) -> None:
    path = _history_file(tmp_path, _rows(40))
    state = DashboardState()
    state.evolution_history_path = path

    payload = _evolution(state, path)
    generations = [row.get("generation") for row in payload["history"]]
    assert generations[-1] == 40, "a linha mais recente sumiu"
    assert len(generations) <= 16
