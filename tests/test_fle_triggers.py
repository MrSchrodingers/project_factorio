"""FLE scripts must not print the substrings that mark an action as failed.

fle/env/gym_env/environment.py:451 decides failure with

    error_occurred = "error" in result.lower() or "exception: " in result.lower()

which is a substring test over whatever the script printed. A variable merely
*named* `circuit_nav_error` in the printed payload was enough to fail a stage
that had just produced 5 electronic circuits, 16 copper cables and read 24
iron ore. This test keeps that trap closed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "factorio_ai_lab"
    / "experiments"
    / "curriculum_runner.py"
)

TRIGGERS = ("error", "exception: ")


def _script_text(value: ast.AST | None) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        return "".join(
            piece.value
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
            else "0"
            for piece in value.values
        )
    return None


def _embedded_scripts() -> list[tuple[int, str]]:
    """Every script the runner hands the engine, in both shapes it has.

    A stage assigns its script to ``code``; a script builder returns one. The
    second shape exists because the two paths of stage 0 have to be
    comparable name by name, which they are only when each is a function that
    can be called and read. A probe that knew only the first shape would have
    quietly stopped covering them, and this file is the guard whose silence
    is indistinguishable from safety.
    """
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            named_code = "code" in [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            text = _script_text(node.value) if named_code else None
        elif isinstance(node, ast.Return):
            text = _script_text(node.value)
        else:
            continue
        if text is not None and ("Prototype." in text or "Resource." in text):
            found.append((node.lineno, text))
    return found


SCRIPTS = _embedded_scripts()


def _assigned_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_scripts_were_found() -> None:
    assert len(SCRIPTS) >= 10, f"apenas {len(SCRIPTS)} scripts extraidos"


def test_both_paths_of_the_baseline_cell_are_covered() -> None:
    # Guard the instrument. Stage 0 builds its cell or commissions the one it
    # inherited, and only one of those was ever written as ``code = ...``.
    sources = [source for _, source in SCRIPTS]
    assert any("place_entity(\n    Prototype.BurnerMiningDrill" in s for s in sources), (
        "o script que constroi a celula base saiu da cobertura"
    )
    assert any("get_entity(\n    Prototype.BurnerMiningDrill" in s for s in sources), (
        "o script que comissiona a celula herdada saiu da cobertura"
    )


@pytest.mark.parametrize("lineno,source", SCRIPTS, ids=[str(n) for n, _ in SCRIPTS])
def test_no_identifier_trips_the_failure_heuristic(lineno: int, source: str) -> None:
    offenders = sorted(
        name
        for name in _assigned_names(source)
        if any(trigger.strip() in name.lower() for trigger in TRIGGERS)
    )
    assert not offenders, (
        f"curriculum_runner.py:{lineno}: identificadores {offenders} contem "
        "uma palavra que o FLE usa para marcar a acao como falha "
        "(environment.py:451). Renomeie."
    )


@pytest.mark.parametrize("lineno,source", SCRIPTS, ids=[str(n) for n, _ in SCRIPTS])
def test_no_string_literal_trips_the_failure_heuristic(lineno: int, source: str) -> None:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(trigger in lowered for trigger in TRIGGERS):
                offenders.append(node.value[:60])
    assert not offenders, (
        f"curriculum_runner.py:{lineno}: literais {offenders} contem uma "
        "palavra que o FLE usa para marcar a acao como falha. Se o texto "
        "precisa existir, neutralize a palavra."
    )
