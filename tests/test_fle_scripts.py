"""Every FLE script embedded in the curriculum must be valid Python.

These stage bodies are f-strings shipped to the FLE namespace and executed
there, so a syntax error in one does not fail at import, at lint, or in the
test suite -- it fails mid-generation, minutes into a run, and (until the
instrumentation fix) was recorded with no message at all. Compiling them here
turns that into a build-time failure.
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


def _embedded_scripts() -> list[tuple[int, str]]:
    """Every f-string assigned to a local named `code`, with its line number."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "code" not in targets:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.append((node.lineno, value.value))
        elif isinstance(value, ast.JoinedStr):
            # Substitute each interpolation with a literal so the skeleton can
            # be parsed; we are checking structure, not the formatted values.
            # Note the blind spot this creates: an interpolation that was
            # meant to be a literal brace in the script (for example a set
            # literal written `{Prototype.X}` inside an f-string) is replaced
            # here and parses cleanly. Ruff's F821 is what catches that case,
            # because the interpolated name has to resolve in the runner's own
            # scope; this test covers the script's syntax, not its braces.
            parts: list[str] = []
            for piece in value.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
                else:
                    parts.append("0")
            found.append((node.lineno, "".join(parts)))
    return found


SCRIPTS = _embedded_scripts()


def test_the_runner_actually_embeds_scripts() -> None:
    # Guard against the extractor silently finding nothing, which would make
    # every parametrised case below vacuously pass.
    assert len(SCRIPTS) >= 10, f"apenas {len(SCRIPTS)} scripts extraidos"


@pytest.mark.parametrize("lineno,source", SCRIPTS, ids=[str(n) for n, _ in SCRIPTS])
def test_embedded_script_parses(lineno: int, source: str) -> None:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        pytest.fail(
            f"script FLE em curriculum_runner.py:{lineno} nao compila: "
            f"{exc.msg} (linha {exc.lineno} do script)"
        )
