"""No stage may divide its output by the literal it passed to sleep().

The game keeps running through move_to, extract_item and place_entity, so the
window a stage really measures is several times the literal: 80 game seconds
against a literal of 16 where it was measured on the copper stage. Every rate
divided by the literal was inflated by that ratio, and a champion recorded
copper-ore at 0.8125/s when a burner mining drill physically mines 0.25/s.

Two guards live here. One reads curriculum_runner.py and refuses any rate whose
denominator traces back to the literal, including through an intermediate
variable such as `total_window = settle_seconds + 8`. The other runs the clock
against a stand-in environment, because the label on the metric has to say
which instrument produced the number.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from factorio_ai_lab.experiments.curriculum_runner import (
    _record_observed_window,
    _StageClock,
)
from factorio_ai_lab.metrics.rates import rate_per_second

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "factorio_ai_lab"
    / "experiments"
    / "curriculum_runner.py"
)

#: Names that carry the sleep literal, and anything computed from them.
LITERAL_SOURCES = frozenset({"settle_seconds", "seed_seconds"})

#: Calls that turn a literal into a measured window. An expression containing
#: one of them is reporting the game clock, not the literal it fell back to.
MEASURED_CALLS = frozenset(
    {"observed_window_seconds", "_record_observed_window", "window"}
)

#: Keyword denominators of normalized_rate_ratio.
DURATION_KEYWORDS = frozenset({"candidate_duration_s", "baseline_duration_s"})

RATE_CALLS = frozenset({"rate_per_second", "normalized_rate_ratio"})


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _mentions_measured_call(node: ast.AST) -> bool:
    return any(
        _call_name(child) in MEASURED_CALLS
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    )


def _names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _literal_derived_names(function: ast.AST) -> set[str]:
    """Locals that carry the sleep literal, directly or through arithmetic."""
    tainted = set(LITERAL_SOURCES)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            if _mentions_measured_call(node.value):
                continue
            if not _names(node.value) & tainted:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in tainted:
                    tainted.add(target.id)
                    changed = True
    return tainted


def _denominators(function: ast.AST) -> list[ast.expr]:
    found: list[ast.expr] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in RATE_CALLS:
            continue
        if name == "rate_per_second" and len(node.args) >= 2:
            found.append(node.args[1])
        for keyword in node.keywords:
            if keyword.arg in DURATION_KEYWORDS:
                found.append(keyword.value)
    return found


def _literal_denominator_offenders(source: str) -> list[tuple[str, int, str]]:
    """Rates whose denominator is the sleep literal instead of the clock."""
    tree = ast.parse(source)
    offenders: dict[tuple[int, str], tuple[str, int, str]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        tainted = _literal_derived_names(function)
        for expression in _denominators(function):
            if _mentions_measured_call(expression):
                continue
            if not _names(expression) & tainted:
                continue
            rendered = ast.unparse(expression)
            offenders[(expression.lineno, rendered)] = (
                function.name,
                expression.lineno,
                rendered,
            )
    return sorted(offenders.values(), key=lambda entry: entry[1])


def _top_level_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


def _called_names(function: ast.AST) -> set[str]:
    return {
        _call_name(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }


RUNNER_SOURCE = RUNNER.read_text(encoding="utf-8")
RUNNER_TREE = ast.parse(RUNNER_SOURCE)

MEASURING_STAGES = [
    function
    for function in _top_level_functions(RUNNER_TREE)
    if _called_names(function) & RATE_CALLS
]


def test_the_extractor_finds_the_measuring_stages() -> None:
    # Guard against a scan that silently finds nothing, which would make every
    # assertion below vacuously true.
    assert len(MEASURING_STAGES) >= 13, (
        f"apenas {len(MEASURING_STAGES)} estagios com taxa encontrados"
    )


def test_no_stage_divides_by_the_sleep_literal() -> None:
    offenders = _literal_denominator_offenders(RUNNER_SOURCE)
    assert offenders == [], (
        "denominador vindo do literal de sleep em: "
        + "; ".join(
            f"{name} (curriculum_runner.py:{lineno}: {expression})"
            for name, lineno, expression in offenders
        )
    )


def test_the_denominator_probe_catches_a_known_positive() -> None:
    # Guard the instrument: both the direct literal and one laundered through
    # an intermediate variable have to be reported.
    direct = _literal_denominator_offenders(
        "def stage_probe(env, journal, *, settle_seconds):\n"
        "    rate_per_second(output, settle_seconds)\n"
    )
    assert [entry[2] for entry in direct] == ["settle_seconds"]

    laundered = _literal_denominator_offenders(
        "def stage_probe(env, journal, *, settle_seconds):\n"
        "    total_window = settle_seconds + 8\n"
        "    rate_per_second(output, total_window)\n"
    )
    assert [entry[2] for entry in laundered] == ["total_window"]

    ratio = _literal_denominator_offenders(
        "def stage_probe(env, journal, *, settle_seconds):\n"
        "    normalized_rate_ratio(\n"
        "        candidate_count=plates,\n"
        "        candidate_duration_s=float(settle_seconds),\n"
        "        baseline_count=direct,\n"
        "        baseline_duration_s=direct_duration,\n"
        "    )\n"
    )
    assert [entry[2] for entry in ratio] == ["float(settle_seconds)"]


def test_the_denominator_probe_accepts_the_observed_window() -> None:
    clean = _literal_denominator_offenders(
        "def stage_probe(env, journal, *, settle_seconds):\n"
        "    clock = _StageClock(env)\n"
        "    window = _record_observed_window(\n"
        "        journal,\n"
        "        clock,\n"
        "        metric_prefix='probe',\n"
        "        fallback_seconds=float(settle_seconds),\n"
        "    )\n"
        "    rate_per_second(output, window)\n"
    )
    assert clean == []


@pytest.mark.parametrize(
    "stage",
    MEASURING_STAGES,
    ids=[function.name for function in MEASURING_STAGES],
)
def test_every_measuring_stage_records_its_instrument(stage: ast.FunctionDef) -> None:
    assert "_record_observed_window" in _called_names(stage), (
        f"{stage.name} calcula uma taxa sem gravar <nome>_duration_s e "
        "<nome>_duration_source: nao da para saber qual instrumento mediu"
    )


class _FakeEnv:
    """Stand-in exposing the one call `_game_ticks` makes."""

    def __init__(self, ticks: int | None) -> None:
        self.ticks = ticks
        instance = (
            SimpleNamespace()
            if ticks is None
            else SimpleNamespace(get_elapsed_ticks=lambda: self.ticks)
        )
        self.unwrapped = SimpleNamespace(instance=instance)


def _journal() -> SimpleNamespace:
    return SimpleNamespace(state={"metrics": {}})


def test_the_window_is_the_game_clock_not_the_literal() -> None:
    # The measured case: 4800 ticks is 80 game seconds against a literal of 16.
    env = _FakeEnv(1000)
    clock = _StageClock(env)
    env.ticks = 5800
    clock.stop()
    journal = _journal()

    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="probe",
        fallback_seconds=16.0,
    )

    assert window == pytest.approx(80.0)
    assert journal.state["metrics"]["probe_duration_s"] == pytest.approx(80.0)
    assert journal.state["metrics"]["probe_duration_source"] == "observed_game_ticks"
    assert rate_per_second(20.0, window) == pytest.approx(0.25)


def test_a_runtime_without_a_counter_falls_back_and_says_so() -> None:
    clock = _StageClock(_FakeEnv(None))
    clock.stop()
    journal = _journal()

    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="probe",
        fallback_seconds=16.0,
    )

    assert window == pytest.approx(16.0)
    assert journal.state["metrics"]["probe_duration_source"] == "sleep_literal"


def test_a_rolled_back_clock_is_not_reported_as_observed() -> None:
    # A rejected step is rolled back by resetting the environment to its
    # checkpoint, which rewinds the counter. A negative window must not be
    # dressed up as a measurement.
    env = _FakeEnv(5800)
    clock = _StageClock(env)
    env.ticks = 1000
    clock.stop()
    journal = _journal()

    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="probe",
        fallback_seconds=16.0,
    )

    assert window == pytest.approx(16.0)
    assert journal.state["metrics"]["probe_duration_source"] == "sleep_literal"


def test_a_clock_that_never_stopped_falls_back() -> None:
    # The step raised before the acceptance callback ran.
    env = _FakeEnv(1000)
    clock = _StageClock(env)
    env.ticks = 5800
    journal = _journal()

    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="probe",
        fallback_seconds=16.0,
    )

    assert window == pytest.approx(16.0)
    assert journal.state["metrics"]["probe_duration_source"] == "sleep_literal"
