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


def _embedded_scripts() -> list[tuple[int, str]]:
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if "code" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.append((node.lineno, value.value))
        elif isinstance(value, ast.JoinedStr):
            parts = [
                piece.value
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
                else "0"
                for piece in value.values
            ]
            found.append((node.lineno, "".join(parts)))
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
