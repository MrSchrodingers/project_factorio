"""The names a stage leaves behind are a contract with the stages after it.

Every curriculum stage runs as a Python script inside one long-lived FLE
namespace, so a stage reaches the factory the stages before it built by name:
``chest``, ``smelt_furnace``, ``coal_chest``, ``steam_engine``. Nothing in the
runner declares those names, and nothing checked them -- until the moment a
stage read one that was never bound and the generation died there.

That moment was generation 44, stage 13:

    NameError: name 'chest' is not defined
    Did you mean one of these? ['fuel_chest', 'coal_chest']

``chest`` was bound by the construction path of stage 0 and by nothing else,
so the name existed for a generation that built its own mining cell and not
for an heir that adopted the one it inherited. The two paths of one stage have
to leave the same names behind: a name that exists only on one of them is a
trap armed for every stage downstream, and it takes thirteen stages and twelve
minutes of game time to spring it.

The sweep below is the guard. It reads every script the runner builds, in the
order ``run_curriculum`` runs them, and asks of each name a script reads: did
a stage before it bind that name on *every* path it could have taken? Stage 0
contributes what its alternatives share, never their union.

What it does not check: whether the entity a name is bound to is the right
one. That is the stage's own business. This is about names existing at all.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

from factorio_ai_lab.experiments import curriculum_runner

#: Names the FLE namespace hands to every script: the tools, the prototype
#: catalogues and the geometry types. Reading one of these is reading the
#: engine, not a name an earlier stage left behind.
FLE_API = frozenset(
    {
        "BuildingBox",
        "Direction",
        "Position",
        "Prototype",
        "Recipe",
        "Resource",
        "Technology",
        "can_place_entity",
        "connect_entities",
        "craft_item",
        "extract_item",
        "get_entities",
        "get_entity",
        "get_resource_patch",
        "harvest_resource",
        "insert_item",
        "inspect_inventory",
        "move_to",
        "nearest",
        "nearest_buildable",
        "pickup_entity",
        "place_entity",
        "place_entity_next_to",
        "rotate_entity",
        "set_entity_recipe",
        "sleep",
    }
)

#: The stages in the order ``run_curriculum`` runs them, with the fuel-feed
#: transaction ``stage_steam_power`` installs written where it runs. The order
#: is the contract: a name is available to a stage only if a stage before it
#: bound it.
CURRICULUM_ORDER = (
    "stage_baseline",
    "stage_online_learning",
    "stage_scale_mining",
    "stage_smelting_probe",
    "stage_astar_logistics",
    "stage_belt_smelting",
    "stage_coal_mining",
    "stage_copper_mining",
    "stage_copper_smelting",
    "stage_capability_survival",
    "stage_steam_power",
    "_install_fuel_feeds",
    "stage_powered_manufacturing",
    "stage_automation_science",
    "stage_electronic_circuits",
    "stage_logistic_science",
    "stage_transactional_rebuild",
)

#: Script fragments a stage splices into its own step, with the arguments that
#: make each one declare its counters and nothing more. A prelude called with
#: no plan still binds every name it ever binds, which is exactly the
#: guarantee the stages downstream may rely on.
PRELUDE_BUILDERS = {
    "mining_cell_supply_script": lambda: curriculum_runner.mining_cell_supply_script(
        None,
        fuel_needed=0,
    ),
    "coal_quarantine_script": lambda: curriculum_runner.coal_quarantine_script(
        None,
        keep=0,
    ),
    "_bootstrap_vault_release_script": (
        curriculum_runner._bootstrap_vault_release_script
    ),
    "_fuel_feed_script": lambda: curriculum_runner._fuel_feed_script(
        machines=curriculum_runner.FUEL_FED_MACHINES,
        coal_per_machine=0,
    ),
    # The whole fuel-feed step. `_install_fuel_feeds` splices this one
    # fragment and nothing else, so without it the sweep would read that
    # stage as running no script at all and pass by covering nothing.
    "_fuel_feed_code": lambda: curriculum_runner._fuel_feed_code(
        None,
        machines=curriculum_runner.FUEL_FED_MACHINES,
        coal_per_machine=0,
        fuel_needed=0,
    ),
}

#: A line that binds a name, for fragments too small to be valid Python on
#: their own -- ``"logistics_container_type = Prototype."`` is completed by
#: concatenation before it reaches the engine.
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_]\w*)\s*=(?!=)")


def _module_tree() -> tuple[str, ast.Module]:
    text = Path(curriculum_runner.__file__).read_text(encoding="utf-8")
    return text, ast.parse(text)


def _defmt(body: str) -> str:
    """An f-string template with its placeholders resolved to a literal.

    ``{expr}`` becomes ``1`` and ``{{``/``}}`` become single braces, which is
    what the engine receives once the runner has formatted the template. The
    substitution keeps the script parseable without pretending to know what
    the formatted values are.
    """
    out: list[str] = []
    index = 0
    size = len(body)
    while index < size:
        char = body[index]
        if char == "{":
            if index + 1 < size and body[index + 1] == "{":
                out.append("{")
                index += 2
                continue
            depth = 1
            cursor = index + 1
            while cursor < size and depth:
                if body[cursor] == "{":
                    depth += 1
                elif body[cursor] == "}":
                    depth -= 1
                elif body[cursor] in "\"'":
                    quote = body[cursor]
                    cursor += 1
                    while cursor < size and body[cursor] != quote:
                        cursor += 1
                cursor += 1
            out.append("1")
            index = cursor
            continue
        if char == "}" and index + 1 < size and body[index + 1] == "}":
            out.append("}")
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _literal_text(segment: str) -> str | None:
    """The body of a string literal as the engine will receive it."""
    match = re.match(r"^([fFrRbB]*)(\"\"\"|'''|\"|')", segment)
    if match is None:
        return None
    quote = match.group(2)
    if not segment.endswith(quote) or len(segment) < len(quote) * 2:
        return None
    body = segment[match.end() : len(segment) - len(quote)]
    return _defmt(body) if "f" in match.group(1).lower() else body


class _Scope:
    """Names a script binds on every path, and the names it reads."""

    def __init__(self, sure: set[str] | None = None) -> None:
        self.sure: set[str] = set(sure or ())
        self.reads: set[str] = set()


def _load(scope: _Scope, name: str) -> None:
    if name in FLE_API or hasattr(builtins, name):
        return
    if name not in scope.sure:
        scope.reads.add(name)


def _read_expression(scope: _Scope, node: ast.AST | None) -> None:
    if node is None:
        return
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            _load(scope, child.id)


def _bind(scope: _Scope, target: ast.AST) -> None:
    for child in ast.walk(target):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            scope.sure.add(child.id)


def _branch(scope: _Scope, body: list[ast.stmt]) -> _Scope:
    inner = _Scope(scope.sure)
    _walk(inner, body)
    scope.reads |= inner.reads
    return inner


def _walk(scope: _Scope, body: list[ast.stmt]) -> None:
    """Bindings and reads of a statement list, in execution order.

    A name bound only inside an ``if`` without an ``else``, inside a loop that
    may not run, or inside a ``try`` that may raise before reaching it, is not
    a name the next statement can count on. Only what every path binds is
    carried forward, which is the same question the curriculum asks of its
    stages.
    """
    for statement in body:
        if isinstance(statement, ast.Assign):
            _read_expression(scope, statement.value)
            for target in statement.targets:
                _bind(scope, target)
        elif isinstance(statement, (ast.AugAssign, ast.AnnAssign)):
            _read_expression(scope, statement.value)
            if isinstance(statement, ast.AugAssign):
                _read_expression(
                    scope,
                    ast.Name(id=getattr(statement.target, "id", ""), ctx=ast.Load()),
                )
            _bind(scope, statement.target)
        elif isinstance(statement, ast.If):
            _read_expression(scope, statement.test)
            taken = _branch(scope, statement.body)
            if statement.orelse:
                otherwise = _branch(scope, statement.orelse)
                scope.sure |= taken.sure & otherwise.sure
        elif isinstance(statement, ast.Try):
            _branch(scope, statement.body)
            for handler in statement.handlers:
                guarded = _Scope(scope.sure)
                if handler.name:
                    guarded.sure.add(handler.name)
                _walk(guarded, handler.body)
                scope.reads |= guarded.reads
            _branch(scope, statement.orelse)
            _walk(scope, statement.finalbody)
        elif isinstance(statement, (ast.For, ast.While)):
            _read_expression(
                scope,
                statement.iter if isinstance(statement, ast.For) else statement.test,
            )
            inner = _Scope(scope.sure)
            if isinstance(statement, ast.For):
                _bind(inner, statement.target)
            _walk(inner, statement.body)
            scope.reads |= inner.reads
        elif isinstance(statement, ast.Expr):
            _read_expression(scope, statement.value)
        else:
            _read_expression(scope, statement)


def script_scope(text: str) -> _Scope:
    """What one script binds on every path and what it reads.

    A fragment too small to parse on its own is read for its bindings alone:
    it is completed by concatenation before the engine sees it, and claiming
    it reads nothing is the conservative half of the answer.
    """
    scope = _Scope()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for line in text.splitlines():
            match = _ASSIGNMENT.match(line)
            if match is not None:
                scope.sure.add(match.group(1))
        return scope
    _walk(scope, tree.body)
    return scope


def _function_scripts(
    name: str,
    text: str,
    tree: ast.Module,
) -> tuple[list[str], list[str], list[str]]:
    """The preludes, fragments and step scripts of one stage function.

    A step script is what the executor is handed: the literal assigned to
    ``code`` or passed to ``execute`` directly. Everything else in the
    function is a fragment spliced into one of those before it runs.
    """
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    mains: set[int] = set()
    preludes: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("code", "fuel_code"):
                    mains.add(id(node.value))
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Attribute) and called.attr == "execute":
                for argument in node.args:
                    mains.add(id(argument))
            if isinstance(called, ast.Name) and called.id in PRELUDE_BUILDERS:
                preludes.append(PRELUDE_BUILDERS[called.id]())

    docstring = ast.get_docstring(function, clean=False)
    literals: list[tuple[int, str, bool]] = []
    for node in ast.walk(function):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        if isinstance(node, ast.Constant) and node.value == docstring:
            continue
        segment = ast.get_source_segment(text, node)
        if segment is None:
            continue
        body = _literal_text(segment)
        if body is None or not ("Prototype." in body or "Resource." in body):
            continue
        literals.append((node.lineno, body, id(node) in mains))
    literals.sort()
    fragments = [body for _, body, main in literals if not main]
    scripts = [body for _, body, main in literals if main]
    return preludes, fragments, scripts


def baseline_alternatives() -> tuple[str, ...]:
    """Every script stage 0 may run, one of which it does run.

    Called rather than read out of the source, because both are assembled
    from the world the stage surveyed: the container binding of the adopted
    cell is spliced in, and a static reading of the template would see the
    placeholder instead of the name.
    """
    return (
        curriculum_runner.baseline_build_script(center=(27.0, 83.0), settle_seconds=16),
        curriculum_runner.baseline_adopt_script(
            drill_position=(27.0, 83.0),
            container=((27.5, 84.5), curriculum_runner.BASELINE_CHEST_NAME),
            settle_seconds=16,
        ),
        curriculum_runner.baseline_adopt_script(
            drill_position=(27.0, 83.0),
            container=None,
            settle_seconds=16,
        ),
    )


def test_every_baseline_path_binds_the_same_names() -> None:
    """Build, adopt-with-container and adopt-without have to agree.

    This is the assertion generation 44 would have needed: the adoption path
    bound ``drill`` and stopped, the construction path bound ``drill`` and
    ``chest``, and the difference was invisible until stage 13 read the name
    that was missing.
    """
    bound = [script_scope(script).sure for script in baseline_alternatives()]
    reference = bound[0]
    for other in bound[1:]:
        assert other == reference, (
            "os caminhos da etapa 0 vinculam nomes diferentes; "
            f"so num deles: {sorted(reference ^ other)}"
        )
    assert {"drill", "chest"} <= reference


def test_the_adopted_cell_names_the_container_it_found() -> None:
    adopted = curriculum_runner.baseline_adopt_script(
        drill_position=(27.0, 83.0),
        container=((27.5, 84.5), curriculum_runner.BASELINE_CHEST_NAME),
        settle_seconds=16,
    )
    assert "chest = get_entity(" in adopted
    assert "Position(x=27.5, y=84.5)" in adopted
    assert "place_entity" not in adopted, (
        "a celula adotada nao pode ser reconstruida"
    )


def test_a_cell_with_no_container_still_binds_the_name() -> None:
    """An absent chest is None, never an absent name.

    None is testable -- stage 13 scans for a container holding iron ore when
    it reads one -- and a name that was never bound is not.
    """
    adopted = curriculum_runner.baseline_adopt_script(
        drill_position=(27.0, 83.0),
        container=None,
        settle_seconds=16,
    )
    assert "chest = None" in adopted
    assert "chest" in script_scope(adopted).sure


def test_every_name_a_stage_reads_was_bound_by_a_stage_before_it() -> None:
    """The sweep, over every script the curriculum runs, in order."""
    text, tree = _module_tree()
    # Stage 0 runs exactly one of its alternatives, so only what all of them
    # bind is available downstream. Its own step scripts -- the world
    # perception it always runs -- are cumulative and join them.
    available: set[str] = set.intersection(
        *(script_scope(script).sure for script in baseline_alternatives())
    )
    _, _, perception = _function_scripts("stage_baseline", text, tree)
    for script in perception:
        available |= script_scope(script).sure

    unsatisfied: dict[str, list[str]] = {}
    for stage in CURRICULUM_ORDER[1:]:
        preludes, fragments, scripts = _function_scripts(stage, text, tree)
        scope = _Scope(available)
        for part in (*preludes, *fragments, *scripts):
            part_scope = script_scope(part)
            scope.reads |= part_scope.reads - scope.sure
            scope.sure |= part_scope.sure
        missing = sorted(scope.reads - available)
        if missing:
            unsatisfied[stage] = missing
        available = scope.sure

    assert unsatisfied == {}, (
        "etapas leem nomes que nenhum caminho anterior vincula: "
        f"{unsatisfied}"
    )


@pytest.mark.parametrize("stage", CURRICULUM_ORDER)
def test_every_stage_in_the_contract_still_exists(stage: str) -> None:
    # The sweep is only a contract while it covers the stages that run. A
    # stage renamed out from under it would make it pass by covering nothing.
    assert callable(getattr(curriculum_runner, stage))
