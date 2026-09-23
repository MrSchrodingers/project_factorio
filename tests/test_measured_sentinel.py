"""A variable the FLE script never assigned is unmeasured, not zero.

The stage scripts run remotely and abort where they fail, leaving every
variable after the failing line unassigned. Reading those back with
``float(getattr(namespace, key, 0.0) or 0.0)`` turns an abort into a measured
zero: ``circuit_iron_ore: 0.0`` was read for eleven generations as an empty
iron buffer while the buffer actually held about 137 ore.

The sentinel has to be None, and every consumer of the measurement dict has to
survive it -- a gate that compares None numerically raises, and a ``:.0f`` on
None raises too, so the guards below cover the reader as well as the writer.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from factorio_ai_lab.experiments.curriculum_runner import (
    _measured_above,
    _measured_at_least,
    _measured_text,
    _namespace_measure,
)

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "factorio_ai_lab"
    / "experiments"
    / "curriculum_runner.py"
)

RUNNER_SOURCE = RUNNER.read_text(encoding="utf-8")


def _measured_key(node: ast.AST) -> str | None:
    """The literal key of a `measured["..."]` read, if that is what this is."""
    if not isinstance(node, ast.Subscript):
        return None
    if not isinstance(node.value, ast.Name) or node.value.id != "measured":
        return None
    index = node.slice
    if isinstance(index, ast.Constant) and isinstance(index.value, str):
        return index.value
    return None


def _zero_defaulted_namespace_reads(source: str) -> list[tuple[int, str]]:
    """`getattr(namespace, <variable>, <not None>)` reads, which erase aborts.

    Only the reads whose attribute name is itself a variable are covered: that
    is the loop over a tuple of keys, which is where the substitution of 0.0
    for "never assigned" was made.
    """
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "getattr":
            continue
        if len(node.args) != 3:
            continue
        target, attribute, default = node.args
        if not isinstance(target, ast.Name) or target.id != "namespace":
            continue
        if isinstance(attribute, ast.Constant):
            continue
        if isinstance(default, ast.Constant) and default.value is None:
            continue
        offenders.append((node.lineno, ast.unparse(node)))
    return sorted(offenders)


def _preserves_none(value: ast.expr) -> bool:
    """Whether the written expression can still come out as None.

    Either through the shared helper, or by testing the raw read against None
    inline, which is the same contract written out longhand.
    """
    for node in ast.walk(value):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_namespace_measure"
        ):
            return True
        if isinstance(node, ast.IfExp) and isinstance(node.test, ast.Compare):
            comparators = node.test.comparators
            if any(isinstance(operator, ast.Is) for operator in node.test.ops) and any(
                isinstance(entry, ast.Constant) and entry.value is None
                for entry in comparators
            ):
                return True
    return False


def _sentinel_loops(function: ast.AST) -> tuple[set[str], list[tuple[int, str]]]:
    """Keys read in a loop into `measured`, plus the reads that erase the None."""
    keys: set[str] = set()
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
            continue
        loop_variable = node.target.id
        writes = [
            statement
            for statement in ast.walk(node)
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "measured"
                and isinstance(target.slice, ast.Name)
                and target.slice.id == loop_variable
                for target in statement.targets
            )
        ]
        if not writes:
            continue
        if isinstance(node.iter, ast.Tuple):
            keys |= {
                element.value
                for element in node.iter.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
        for statement in writes:
            if not _preserves_none(statement.value):
                offenders.append((statement.lineno, ast.unparse(statement)))
    return keys, offenders


def _unguarded_consumers(source: str) -> list[tuple[int, str]]:
    """Consumers that would raise instead of deciding when a key is None."""
    offenders: list[tuple[int, str]] = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef):
            continue
        keys, _ = _sentinel_loops(function)
        if not keys:
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if any(_measured_key(operand) in keys for operand in operands):
                    offenders.append((node.lineno, ast.unparse(node)))
            elif isinstance(node, ast.FormattedValue) and node.format_spec is not None:
                if _measured_key(node.value) in keys:
                    offenders.append((node.lineno, ast.unparse(node.value)))
    return sorted(set(offenders))


def test_no_stage_defaults_an_unassigned_measurement_to_zero() -> None:
    offenders = _zero_defaulted_namespace_reads(RUNNER_SOURCE)
    assert offenders == [], (
        "leitura que transforma aborto em zero medido: "
        + "; ".join(f"curriculum_runner.py:{line}: {text}" for line, text in offenders)
    )


def test_every_sentinel_loop_uses_the_helper() -> None:
    offenders: list[tuple[int, str]] = []
    loops = 0
    for function in ast.walk(ast.parse(RUNNER_SOURCE)):
        if not isinstance(function, ast.FunctionDef):
            continue
        keys, found = _sentinel_loops(function)
        if keys:
            loops += 1
        offenders.extend(found)
    assert loops >= 4, f"apenas {loops} blocos de medicao em lote encontrados"
    assert offenders == [], (
        "bloco que transforma variavel nao atribuida em zero: "
        + "; ".join(f"curriculum_runner.py:{line}: {text}" for line, text in offenders)
    )


def test_no_consumer_compares_or_formats_a_possibly_unmeasured_key() -> None:
    offenders = _unguarded_consumers(RUNNER_SOURCE)
    assert offenders == [], (
        "consumidor que quebra com None: "
        + "; ".join(f"curriculum_runner.py:{line}: {text}" for line, text in offenders)
    )


POSITIVE_CASE = '''
def validate(result):
    for key in ("transfer_1", "endogenous_growth"):
        measured[key] = float(getattr(namespace, key, 0.0) or 0.0)
    detail = f"{measured['endogenous_growth']:.0f} buffered"
    return measured["transfer_1"] >= 1
'''

NEGATIVE_CASE = '''
def validate(result):
    for key in ("transfer_1", "endogenous_growth"):
        measured[key] = _namespace_measure(namespace, key)
    detail = _measured_text(measured, "endogenous_growth")
    return _measured_at_least(measured, "transfer_1", 1)
'''


def test_the_sentinel_probes_catch_a_known_positive() -> None:
    # Guard the instrument against the exact pattern this change removes.
    assert [text for _, text in _zero_defaulted_namespace_reads(POSITIVE_CASE)] == [
        "getattr(namespace, key, 0.0)"
    ]
    _, loop_offenders = _sentinel_loops(ast.parse(POSITIVE_CASE))
    assert [text for _, text in loop_offenders] == [
        "measured[key] = float(getattr(namespace, key, 0.0) or 0.0)"
    ]
    consumers = [text for _, text in _unguarded_consumers(POSITIVE_CASE)]
    assert "measured['transfer_1'] >= 1" in consumers
    assert "measured['endogenous_growth']" in consumers


def test_the_sentinel_probes_accept_the_fixed_pattern() -> None:
    assert _zero_defaulted_namespace_reads(NEGATIVE_CASE) == []
    assert _sentinel_loops(ast.parse(NEGATIVE_CASE))[1] == []
    assert _unguarded_consumers(NEGATIVE_CASE) == []


def test_the_longhand_sentinel_is_accepted_too() -> None:
    # `_install_fuel_feeds` writes the same contract without the helper.
    longhand = (
        "def collect(namespace):\n"
        '    for key in ("fuel_stock", "fuel_dose"):\n'
        "        value = getattr(namespace, key, None)\n"
        "        measured[key] = None if value is None else float(value)\n"
    )
    assert _sentinel_loops(ast.parse(longhand))[1] == []
    assert _zero_defaulted_namespace_reads(longhand) == []


def test_an_unassigned_variable_reads_as_unmeasured() -> None:
    namespace = SimpleNamespace(circuit_copper_ore=0, circuit_iron_ore=137)
    assert _namespace_measure(namespace, "circuit_iron_ore") == pytest.approx(137.0)
    assert _namespace_measure(namespace, "circuit_copper_ore") == pytest.approx(0.0)
    assert _namespace_measure(namespace, "circuit_copper") is None


def test_a_gate_refuses_an_unmeasured_value_instead_of_raising() -> None:
    measured = {"transfer_1": None, "endogenous_growth": None}
    assert _measured_at_least(measured, "transfer_1", 1) is False
    assert _measured_above(measured, "endogenous_growth") is False
    assert _measured_at_least(measured, "absent_key", 1) is False


def test_a_gate_still_decides_on_real_measurements() -> None:
    measured = {"transfer_1": 1.0, "endogenous_growth": 0.0, "stockpile": 12.0}
    assert _measured_at_least(measured, "transfer_1", 1) is True
    assert _measured_above(measured, "endogenous_growth") is False
    assert _measured_above(measured, "stockpile") is True


def test_prose_says_unmeasured_rather_than_zero() -> None:
    assert _measured_text({"stockpile": None}, "stockpile") == "unmeasured"
    assert _measured_text({}, "stockpile") == "unmeasured"
    assert _measured_text({"stockpile": 137.4}, "stockpile") == "137"
