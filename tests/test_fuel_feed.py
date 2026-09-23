"""Burner machines must be fed by the factory, not by one-off doses.

A live RCON census of generation 27 found the whole extraction stopped: six
burner mining drills and the boiler all sat at coal=0, and the two assemblers
on the same electric network were unpowered in consequence. Every unit of fuel
in this arena entered through ``insert_item`` in discrete doses; the largest of
them, 20 coal into the logistics drill, buys 533 s of drill time against a
generation that runs for roughly 4134 s of game time. Meanwhile 359 coal sat
locked in the bootstrap vault, which no code path ever reopened.

These tests pin the fix: a physical feed (a chest plus a burner inserter
pointing at the machine) for every burner machine that survives its
transaction, the vault being reopened once the coal capability is proven, and
instrumentation that reports "not measured" as None rather than 0.0.
"""

from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.fuel import (
    BURNER_MINING_DRILL,
    profile_from_energy_per_tick,
)
from factorio_ai_lab.planning.resupply import ContainerRole, FuelSource, plan_supply

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "factorio_ai_lab"
    / "experiments"
    / "curriculum_runner.py"
)

RUNNER_TREE = ast.parse(RUNNER.read_text(encoding="utf-8"))

#: fle/env/gym_env/environment.py:451 marks a step as failed when the printed
#: result contains either of these substrings.
TRIGGERS = ("error", "exception: ")

# The UCB placement trials also build a burner drill, but their accept callback
# returns False unconditionally (`reject_trial`), so every trial drill is rolled
# back and none of them exists in the persistent world. The live census of
# generation 27 found exactly six burner drills, which is this set minus the
# trial one.
ROLLED_BACK_DRILL_VARIABLES = {"trial_drill"}


def _script_text(value: ast.AST) -> str | None:
    """A script literal as text, with its formatted values stubbed out."""
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
    """Every script the runner hands the engine, with its line number.

    Two shapes carry one: the literal a stage assigns to ``code``, and the
    literal a script builder returns. The second shape exists because the two
    paths of stage 0 have to be comparable name by name, which they are only
    when each is a function that can be called and read. A probe that knew
    only the first shape stopped seeing the baseline mining cell the moment
    that stage was split, and answered that the arena had five burner drills.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(RUNNER_TREE):
        if isinstance(node, ast.Assign):
            named_code = "code" in [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            text = _script_text(node.value) if named_code else None
        elif isinstance(node, ast.Return) and node.value is not None:
            text = _script_text(node.value)
        else:
            continue
        if text is not None and "Prototype." in text:
            found.append((node.lineno, text))
    return found


def _placed_entity_variables(prototype: str) -> set[str]:
    """Namespace variables assigned a freshly placed entity of `prototype`."""
    names: set[str] = set()
    for _, source in _embedded_scripts():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            function = call.func
            if not isinstance(function, ast.Name):
                continue
            if function.id not in {"place_entity", "place_entity_next_to"}:
                continue
            if not call.args:
                continue
            first = call.args[0]
            if not isinstance(first, ast.Attribute) or first.attr != prototype:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _trigger_offenders(source: str) -> list[str]:
    """Identifiers and literals in `source` that trip the FLE failure check."""
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            if any(trigger.strip() in node.id.lower() for trigger in TRIGGERS):
                offenders.append(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(trigger in lowered for trigger in TRIGGERS):
                offenders.append(node.value[:60])
    return sorted(offenders)


def _placement_calls(source: str, prototype: str) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Name):
            continue
        if function.id != "place_entity_next_to" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Attribute) and first.attr == prototype:
            calls.append(node)
    return calls


def _feed_script() -> str:
    builder = getattr(curriculum_runner, "_fuel_feed_script", None)
    assert builder is not None, (
        "curriculum_runner has no _fuel_feed_script: burner machines are still "
        "fed only by discrete insert_item doses"
    )
    return builder(
        machines=curriculum_runner.FUEL_FED_MACHINES,
        coal_per_machine=64,
    )


def _vault_script() -> str:
    builder = getattr(curriculum_runner, "_bootstrap_vault_release_script", None)
    assert builder is not None, (
        "curriculum_runner has no _bootstrap_vault_release_script: the "
        "quarantined bootstrap coal is still unreachable"
    )
    return builder()


def _generated_scripts() -> list[tuple[int, str]]:
    """Embedded stage scripts plus the ones the fuel helpers generate.

    The extractor used by tests/test_fle_scripts.py only sees f-strings
    assigned to `code`, and replaces every interpolation with a literal. A
    script produced by a helper and concatenated into a step is therefore
    invisible to it, so it is added here explicitly.
    """
    scripts = _embedded_scripts()
    for name in ("_bootstrap_vault_release_script", "_fuel_feed_script"):
        builder = getattr(curriculum_runner, name, None)
        if builder is None:
            continue
        if name == "_fuel_feed_script":
            scripts.append((0, _feed_script()))
        else:
            scripts.append((0, builder()))
    return scripts


def test_one_dose_cannot_cover_a_generation() -> None:
    # The largest dose in the roteiro is 20 coal into the logistics drill.
    horizon = curriculum_runner.LAB_GENERATION_HORIZON_SECONDS
    assert horizon > 3600, "a lab generation runs for roughly 4134 game seconds"
    assert BURNER_MINING_DRILL.coal_for_seconds(horizon) > 20


def test_the_feed_script_compiles() -> None:
    ast.parse(_feed_script())


def test_the_feed_script_does_not_trip_the_failure_heuristic() -> None:
    assert _trigger_offenders(_feed_script()) == []


def test_the_trigger_probe_catches_a_known_positive() -> None:
    # Guard the instrument: a script that would be marked as failed by
    # environment.py:451 must be reported by the same predicate used above.
    offenders = _trigger_offenders("feed_error=1\nprint('exception: nope')\n")
    assert "feed_error" in offenders
    assert any("exception: " in entry for entry in offenders)


def test_one_inserter_and_one_chest_are_placed_per_machine() -> None:
    source = _feed_script()
    inserters = _placement_calls(source, "BurnerInserter")
    chests = _placement_calls(source, "WoodenChest")
    assert len(inserters) == 1, "expected exactly one burner inserter placement"
    assert len(chests) == 1, "expected exactly one fuel chest placement"
    reference = chests[0].args[1]
    assert isinstance(reference, ast.Attribute)
    assert reference.attr == "position"
    assert isinstance(reference.value, ast.Name)
    assert reference.value.id.endswith("_inserter"), (
        "the chest has to extend from the inserter, which is the only tile a "
        "Factorio inserter picks up from"
    )


def test_the_feed_charge_is_sized_by_the_fuel_helper() -> None:
    source = curriculum_runner._fuel_feed_script(
        machines=curriculum_runner.FUEL_FED_MACHINES,
        coal_per_machine=197,
    )
    assert "197" in source, "the per-machine charge must reach the remote script"


def test_every_persistent_burner_drill_has_a_fuel_feed() -> None:
    drills = _placed_entity_variables("BurnerMiningDrill") - ROLLED_BACK_DRILL_VARIABLES
    assert len(drills) == 6, (
        f"expected the six burner drills measured over RCON, found {sorted(drills)}"
    )
    fed = {variable for variable, _ in curriculum_runner.FUEL_FED_MACHINES}
    assert drills <= fed, (
        f"burner drills without an automatic fuel feed: {sorted(drills - fed)}"
    )


def test_the_boiler_is_fed_too() -> None:
    fed = {variable for variable, _ in curriculum_runner.FUEL_FED_MACHINES}
    assert "boiler" in fed


def _called_function_names(function_name: str) -> set[str]:
    node = next(
        candidate
        for candidate in ast.walk(RUNNER_TREE)
        if isinstance(candidate, ast.FunctionDef) and candidate.name == function_name
    )
    return {
        call.func.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }


def test_the_steam_stage_installs_the_feeds() -> None:
    assert "_install_fuel_feeds" in _called_function_names("stage_steam_power"), (
        "stage_steam_power still leaves the boiler on a single min(8, ...) dose"
    )


def test_the_installer_builds_the_feed_and_reopens_the_vault() -> None:
    called = _called_function_names("_fuel_feed_code")
    assert "_fuel_feed_script" in called
    assert "_bootstrap_vault_release_script" in called, (
        "the 359 coal quarantined in the bootstrap vault stay unreachable"
    )
    assert "_fuel_feed_code" in _called_function_names("_install_fuel_feeds")


def _fuel_code_parts() -> list[str]:
    """What `_fuel_feed_code` concatenates, in the order the engine gets it."""
    function = next(
        candidate
        for candidate in ast.walk(RUNNER_TREE)
        if isinstance(candidate, ast.FunctionDef)
        and candidate.name == "_fuel_feed_code"
    )
    returned = next(
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return) and node.value is not None
    )

    def flatten(node: ast.AST) -> list[ast.AST]:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return flatten(node.left) + flatten(node.right)
        return [node]

    names: list[str] = []
    for part in flatten(returned):
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Name):
            names.append(part.func.id)
        elif isinstance(part, ast.Constant) and isinstance(part.value, str):
            names.append("<report>")
        else:  # pragma: no cover - a shape this probe cannot read
            names.append(type(part).__name__)
    return names


def test_the_step_draws_from_the_world_before_it_splits_the_charge() -> None:
    """The order is the contract, not an accident of concatenation.

    ``_fuel_feed_script`` reads the coal the agent carries on its first line
    and splits that across the machines. A draw spliced after it divides an
    inventory the coal has not reached yet, which is indistinguishable in the
    journal from the empty feed generations 44 and 46 installed.
    """
    assert getattr(curriculum_runner, "_fuel_feed_code", None) is not None, (
        "curriculum_runner has no _fuel_feed_code: the fuel feed is still "
        "assembled out of the vault release and the split alone"
    )
    parts = _fuel_code_parts()
    assert parts[:3] == [
        "_bootstrap_vault_release_script",
        "mining_cell_supply_script",
        "_fuel_feed_script",
    ], f"the fuel-feed step runs its fragments in the wrong order: {parts}"


def test_the_installer_plans_the_draw_against_the_standing_world() -> None:
    called = _called_function_names("_install_fuel_feeds")
    assert "survey_stage_supply" in called, (
        "the fuel feed still sizes its charge from the inventory the heir "
        "happened to inherit and never looks at the standing containers"
    )
    assert "_fuel_feed_anchor" in called, (
        "a draw planned from no anchor is a draw planned around tiles nobody "
        "read"
    )


def test_the_release_and_the_feed_compile_as_one_script() -> None:
    # They are concatenated into a single step, so their indentation has to
    # agree; each one parsing on its own is not enough.
    ast.parse(_vault_script() + _feed_script())


def test_the_bootstrap_vault_is_emptied_somewhere() -> None:
    reopened = False
    for _, source in _generated_scripts():
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "extract_item":
                continue
            if any(
                isinstance(argument, ast.Name) and argument.id == "bootstrap_vault"
                for argument in node.args
            ):
                reopened = True
    assert reopened, "no script ever extracts coal from bootstrap_vault"


def test_the_vault_script_does_not_trip_the_failure_heuristic() -> None:
    assert _trigger_offenders(_vault_script()) == []


def test_unmeasured_feeds_are_none_not_zero() -> None:
    rows = curriculum_runner._fuel_feed_rows
    assert rows(None) is None
    assert rows("not a log") is None


def test_measured_feeds_keep_their_per_machine_charge() -> None:
    parsed = curriculum_runner._fuel_feed_rows(
        [
            ("boiler", 50, 1, ""),
            ("coal_drill", 0, 0, "no buildable side"),
        ]
    )
    assert parsed is not None
    assert parsed[0]["machine"] == "boiler"
    assert parsed[0]["coal_loaded"] == pytest.approx(50.0)
    assert parsed[0]["inserter_primer_coal"] == pytest.approx(1.0)
    assert parsed[0]["placement_note"] is None
    assert parsed[1]["coal_loaded"] == pytest.approx(0.0)
    assert parsed[1]["placement_note"] == "no buildable side"


def test_a_log_of_the_wrong_shape_is_not_a_partial_census() -> None:
    # Half a parsed list would read as "these are all the feeds that exist",
    # which is a wrong measurement rather than a missing one.
    assert curriculum_runner._fuel_feed_rows([("boiler", 50, 1, ""), ("drill",)]) is None


def test_a_missing_field_stays_none() -> None:
    # A row the remote script could not fill must not be reported as a
    # measured zero: that substitution is what made eleven generations of
    # aborted stages read as "the buffer was empty".
    parsed = curriculum_runner._fuel_feed_rows([("boiler", None, None, "")])
    assert parsed is not None
    assert parsed[0]["coal_loaded"] is None
    assert parsed[0]["inserter_primer_coal"] is None


# --------------------------------------------------------------------------
# Behavioural check against a stand-in world.
#
# The stand-in below encodes the FLE contract this feed relies on, the same
# one open_play_runner.py:3998-4007 already builds against: place_entity_next_to
# puts the entity on the tile adjacent to the reference in the given direction,
# and rotate_entity sets the direction an inserter drops into. Running the
# generated script against it checks what a syntax check cannot -- that the
# inserter ends up dropping into the machine and picking up from the chest,
# that the charge is split instead of spent on the first machine, and that one
# machine with no buildable side does not take the other feeds down with it.
# --------------------------------------------------------------------------


class _Proto:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self.name


class _Prototype:
    Coal = _Proto("coal")
    BurnerInserter = _Proto("burner-inserter")
    WoodenChest = _Proto("wooden-chest")
    IronChest = _Proto("iron-chest")
    StoneFurnace = _Proto("stone-furnace")
    IronOre = _Proto("iron-ore")
    IronPlate = _Proto("iron-plate")


class _Direction:
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)


class _Position(NamedTuple):
    """A tile centre that answers ``.x``/``.y`` and behaves like a tuple.

    The FLE namespace hands a script a ``Position`` object, and the feed has
    to read the position a placement call returned to know which side it
    actually landed on. A stand-in whose positions were plain tuples could
    not exercise that read at all.
    """

    x: float
    y: float


class _TileDimensions(NamedTuple):
    """What ``Entity.tile_dimensions`` carries (fle/env/entities.py:479)."""

    tile_width: float
    tile_height: float


def _covered_tiles(
    position: tuple[float, float],
    footprint: tuple[int, int],
) -> list[_Position]:
    """Every tile an entity of `footprint` centred at `position` stands on."""
    width, height = int(footprint[0]), int(footprint[1])
    left = position[0] - width / 2 + 0.5
    top = position[1] - height / 2 + 0.5
    return [
        _Position(left + column, top + row)
        for column in range(width)
        for row in range(height)
    ]


class _Entity:
    def __init__(
        self,
        name: str,
        position: tuple[float, float],
        tile_dimensions: tuple[float, float] = (1, 1),
        footprint: tuple[int, int] | None = None,
    ) -> None:
        self.name = name
        self.position = _Position(*position)
        # What the entity reports and what it stands on are two different
        # things: ``tile_dimensions`` comes from the unrotated prototype
        # (fle/env/mods/serialize.lua:1108), so a 3x2 boiler facing west
        # reports 3x2 while occupying 2x3 tiles.
        self.tile_dimensions = _TileDimensions(*tile_dimensions)
        self.tiles = _covered_tiles(position, footprint or (1, 1))
        self.direction: tuple[int, int] | None = None
        self.inventory: dict[str, int] = {}


class _Inventory:
    def __init__(self, entity: _Entity) -> None:
        self._entity = entity

    def __getitem__(self, prototype: _Proto) -> int:
        return self._entity.inventory.get(prototype.name, 0)


class _World:
    """Minimal stand-in for the FLE tools the feed script calls."""

    def __init__(self, *, blocked: set[tuple[int, int]] | None = None) -> None:
        self.player = _Entity("character", (0, 0))
        self.blocked = set(blocked or ())
        self.entities: dict[tuple[int, int], _Entity] = {}
        self.placed: list[_Entity] = []
        self.picked_up: list[_Entity] = []

    def spawn(
        self,
        name: str,
        position: tuple[float, float],
        tile_dimensions: tuple[float, float] = (1, 1),
        footprint: tuple[int, int] | None = None,
    ) -> _Entity:
        entity = _Entity(name, position, tile_dimensions, footprint)
        for tile in entity.tiles:
            self.entities[tile] = entity
        return entity

    # -- tools ------------------------------------------------------------
    def move_to(self, position: tuple[int, int]) -> tuple[int, int]:
        self.player.position = position
        return position

    def inspect_inventory(self, entity: _Entity | None = None) -> _Inventory:
        return _Inventory(entity if entity is not None else self.player)

    def place_entity_next_to(
        self,
        prototype: _Proto,
        position: tuple[float, float],
        direction: tuple[int, int],
        spacing: int = 0,
    ) -> _Entity:
        """Place next to `position`, moving to another side when it does not fit.

        The fallback is FLE's, not an invention of this stand-in: when the
        requested tile is taken, ``place_entity_next_to`` scores every other
        side and every distance from one to three tiles and takes the best
        one (fle/env/tools/agent/place_entity_next_to/server.lua:41-110,
        ``find_alternative_position_smart``), preferring the closer
        positions. The caller is told where the entity went and nothing else;
        the side it asked for is not the side it got.
        """
        sides = [direction] + [
            side
            for side in (
                _Direction.UP,
                _Direction.DOWN,
                _Direction.LEFT,
                _Direction.RIGHT,
            )
            if side != direction
        ]
        reference = self.entities.get(_Position(*position))
        footprint = (
            list(reference.tiles) if reference is not None else [_Position(*position)]
        )
        for distance in range(spacing + 1, spacing + 4):
            for side in sides:
                candidates = sorted(
                    {
                        _Position(
                            tile.x + side[0] * distance,
                            tile.y + side[1] * distance,
                        )
                        for tile in footprint
                    },
                    key=lambda tile: (
                        abs(tile.x - position[0]) + abs(tile.y - position[1]),
                        tile.x,
                        tile.y,
                    ),
                )
                for target in candidates:
                    if (
                        target in footprint
                        or target in self.blocked
                        or target in self.entities
                    ):
                        continue
                    entity = self.spawn(prototype.name, target)
                    entity.direction = side
                    self.placed.append(entity)
                    return entity
        raise ValueError(f"cannot place {prototype.name} next to {tuple(position)}")

    def rotate_entity(self, entity: _Entity, direction: tuple[int, int]) -> _Entity:
        entity.direction = direction
        return entity

    def pickup_entity(self, entity: _Entity) -> bool:
        for tile in entity.tiles:
            self.entities.pop(tile, None)
        if entity in self.placed:
            self.placed.remove(entity)
        self.picked_up.append(entity)
        return True

    def insert_item(
        self,
        prototype: _Proto,
        entity: _Entity,
        quantity: int,
    ) -> _Entity:
        carried = self.player.inventory.get(prototype.name, 0)
        assert quantity <= carried, "inserted more than the player carries"
        self.player.inventory[prototype.name] = carried - quantity
        entity.inventory[prototype.name] = (
            entity.inventory.get(prototype.name, 0) + quantity
        )
        return entity

    def _resolve(self, target: _Entity | tuple[float, float]) -> _Entity:
        """An entity, whether it was named or addressed by position.

        The supply prelude draws with ``extract_item(Prototype.Coal,
        Position(x=..., y=...), ...)``, which is how every stage that takes
        fuel out of the standing world already addresses a container.
        """
        if isinstance(target, _Entity):
            return target
        entity = self.entities.get(tuple(target))
        if entity is None:
            raise ValueError(f"nothing stands at {target}")
        return entity

    def extract_item(
        self,
        prototype: _Proto,
        entity: _Entity | tuple[float, float],
        quantity: int,
    ) -> int:
        entity = self._resolve(entity)
        available = entity.inventory.get(prototype.name, 0)
        moved = min(quantity, available)
        entity.inventory[prototype.name] = available - moved
        self.player.inventory[prototype.name] = (
            self.player.inventory.get(prototype.name, 0) + moved
        )
        return moved

    def namespace(self) -> dict[str, object]:
        return {
            "Prototype": _Prototype,
            "Direction": _Direction,
            "move_to": self.move_to,
            "inspect_inventory": self.inspect_inventory,
            "place_entity_next_to": self.place_entity_next_to,
            "rotate_entity": self.rotate_entity,
            "pickup_entity": self.pickup_entity,
            "insert_item": self.insert_item,
            "extract_item": self.extract_item,
            "Position": lambda x, y: _Position(float(x), float(y)),
        }


MACHINE_POSITIONS = {
    "coal_drill": (0, 0),
    "boiler": (40, 0),
    "copper_drill": (80, 0),
    "drill": (120, 0),
    "scale_drill": (160, 0),
    "smelt_drill": (200, 0),
    "logistics_drill": (240, 0),
}


def _run_feed(
    *,
    vault_coal: int = 359,
    quarantined: int | None = None,
    blocked: set[tuple[int, int]] | None = None,
    missing: set[str] | None = None,
    machine_targets: dict[str, int] | None = None,
    shapes: dict[str, tuple[tuple[int, int], tuple[int, int]]] | None = None,
) -> tuple[_World, dict[str, object]]:
    world = _World(blocked=blocked)
    scope = world.namespace()
    for variable, position in MACHINE_POSITIONS.items():
        if variable in (missing or set()):
            continue
        reported, footprint = (shapes or {}).get(variable, ((1, 1), (1, 1)))
        scope[variable] = world.spawn(variable, position, reported, footprint)
    vault = world.spawn("bootstrap_vault", (-20, 0))
    vault.inventory["coal"] = vault_coal
    scope["bootstrap_vault"] = vault
    # What stage 6 put in, bound by the quarantine script that put it there.
    # Defaults to the whole stock: the container stage 6 placed itself was
    # empty before, which is the world the cold start meets.
    scope["bootstrap_quarantine"] = (
        vault_coal if quarantined is None else quarantined
    )
    script = curriculum_runner._bootstrap_vault_release_script() + (
        curriculum_runner._fuel_feed_script(
            machines=curriculum_runner.FUEL_FED_MACHINES,
            coal_per_machine=197,
            machine_targets=machine_targets,
        )
    )
    exec(compile(script, "<feed>", "exec"), scope)  # noqa: S102
    return world, scope


def test_the_vault_is_emptied_into_the_player() -> None:
    world, scope = _run_feed()
    assert scope["fuel_vault_released"] == 359
    assert world.entities[(-20, 0)].inventory["coal"] == 0


def test_only_the_quarantined_coal_comes_back_out() -> None:
    """A vault in an inherited world is a container the world was using.

    ``quarantine_container`` picks one that is already standing, because an
    heir carries no chest to place. The coal beside the bootstrap coal in it
    is the ancestor's, feeding a chain this stage may not empty, so the
    release takes what this generation put in and leaves the rest.
    """
    world, scope = _run_feed(vault_coal=320, quarantined=11)

    assert scope["fuel_vault_stock"] == 320
    assert scope["fuel_vault_claim"] == 11
    assert scope["fuel_vault_released"] == 11
    assert world.entities[(-20, 0)].inventory["coal"] == 309


def test_a_quarantine_larger_than_the_stock_takes_only_what_is_there() -> None:
    # Something else drew from the container between the two stages. What is
    # there bounds the claim; asking for more would raise instead of reading.
    _, scope = _run_feed(vault_coal=4, quarantined=359)

    assert scope["fuel_vault_claim"] == 4
    assert scope["fuel_vault_released"] == 4


def test_every_machine_gets_an_inserter_that_drops_into_it() -> None:
    world, scope = _run_feed()
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    assert len(rows) == len(MACHINE_POSITIONS)
    inserters = [e for e in world.placed if e.name == "burner-inserter"]
    chests = [e for e in world.placed if e.name == "wooden-chest"]
    assert len(inserters) == len(MACHINE_POSITIONS)
    assert len(chests) == len(MACHINE_POSITIONS)
    machine_tiles = set(MACHINE_POSITIONS.values())
    chest_tiles = {chest.position for chest in chests}
    for inserter in inserters:
        assert inserter.direction is not None
        drop = (
            inserter.position[0] + inserter.direction[0],
            inserter.position[1] + inserter.direction[1],
        )
        pickup = (
            inserter.position[0] - inserter.direction[0],
            inserter.position[1] - inserter.direction[1],
        )
        assert drop in machine_tiles, "the inserter drops away from its machine"
        assert pickup in chest_tiles, "the inserter picks up from open ground"


def test_the_charge_follows_the_priority_and_never_exceeds_the_stock() -> None:
    """Each machine takes its own charge, in order, out of what is left.

    Dividing equally is what generation 49 did with the 301 coal it drew:
    43 each against a target of 197, which left the boiler -- the machine
    every electric assembler waits on -- exactly as short as the drills it
    powers, and the circuit assembler at ``no_power``. The order in
    FUEL_FED_MACHINES is the priority, and the stock is spent along it.
    """
    world, scope = _run_feed()
    chests = [e for e in world.placed if e.name == "wooden-chest"]
    loaded = [chest.inventory.get("coal", 0) for chest in chests]
    assert loaded[0] == 197, "the head of the priority order was rationed"
    assert loaded[1] > 0, "the boiler was left to the leftovers"
    assert sum(loaded) <= 359
    assert scope["fuel_coal_loaded_total"] == sum(loaded)
    assert scope["fuel_fed_count"] == len(MACHINE_POSITIONS)
    inserters = [e for e in world.placed if e.name == "burner-inserter"]
    assert all(entity.inventory.get("coal", 0) == 1 for entity in inserters), (
        "a burner inserter with no coal never makes its first swing"
    )


def test_the_boiler_is_charged_before_the_ore_drills_under_scarcity() -> None:
    """The defect, as arithmetic.

    300 coal over seven machines is 41 each under an equal split. Under the
    priority split the coal drill takes its whole 197 -- it is the only
    machine that turns the stock back into a flow -- the boiler takes the 96
    that are left above the primers, and the ore drills get nothing, which is
    said out loud rather than spread thin.
    """
    _, scope = _run_feed(vault_coal=300)
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    loaded = {row["machine"]: row["coal_loaded"] for row in rows}
    assert loaded["coal_drill"] == 197.0
    assert loaded["boiler"] == 96.0, (
        f"the boiler got {loaded['boiler']} coal out of a 300 coal stock"
    )
    assert loaded["iron_logistics_drill"] == 0.0
    # Every machine still keeps the one unit that starts its inserter, so a
    # feed the coal drill refills later has a hand to move it.
    assert [row["inserter_primer_coal"] for row in rows] == [1.0] * len(rows)
    assert scope["fuel_coal_loaded_total"] == 293


def test_the_boiler_charge_is_the_one_the_caller_sized() -> None:
    """The boiler's own target reaches the remote script.

    A boiler burns at 1.8 MW at full draw against a drill's 150 kW, so its
    horizon charge is a different number from the drills'. With the two
    targets equal the boiler would take 197 here; with its own it takes what
    the stock can still cover.
    """
    _, scope = _run_feed(vault_coal=1000, machine_targets={"boiler": 800})
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    loaded = {row["machine"]: row["coal_loaded"] for row in rows}
    assert loaded["coal_drill"] == 197.0
    assert loaded["boiler"] == 796.0


def test_the_inserter_drops_into_the_machine_when_the_side_moves() -> None:
    """The live defect of generation 50, reproduced.

    Read off the arena at tick 25231279: the boiler at (-1, 9.5) sat at
    ``no_fuel`` with 41 coal in a wooden chest 1.5 tiles away, and its burner
    inserter at (0.5, 9.5) reported ``waiting_for_space_in_destination`` with
    a drop position of (0.5, 10.7) -- open ground, not the boiler. FLE had
    moved the inserter to the side that was free and the script rotated it
    towards the side it had asked for.
    """
    world, scope = _run_feed(blocked={(40, -1), (40, 1)})
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    by_machine = {row["machine"]: row for row in rows}
    assert by_machine["boiler"]["coal_loaded"], "no feed was built for the boiler"
    inserter = next(
        entity
        for entity in world.placed
        if entity.name == "burner-inserter"
        and abs(entity.position.x - 40) <= 3
        and abs(entity.position.y) <= 3
    )
    assert inserter.direction is not None
    drop = (
        inserter.position.x + inserter.direction[0],
        inserter.position.y + inserter.direction[1],
    )
    assert drop == (40, 0), f"the boiler's inserter drops at {drop}, on open ground"
    pickup = (
        inserter.position.x - inserter.direction[0],
        inserter.position.y - inserter.direction[1],
    )
    chest = world.entities.get(pickup)
    assert chest is not None and chest.name == "wooden-chest", (
        "the inserter picks up from a tile no chest stands on"
    )
    assert chest.inventory.get("coal", 0) > 0


#: A machine shaped like the boiler of generation 50: three tiles long, laid
#: across the axis its prototype states (``tile_dimensions`` is the unrotated
#: prototype, fle/env/mods/serialize.lua:1108), with pipes on three sides.
#: The only free side is two tiles from the position the placement call is
#: given, which is the distance a one-tile rule gets wrong.
BOILER_SHAPE = {"boiler": ((1, 3), (3, 1))}
BOILER_PIPES = {
    (39, -1),
    (40, -1),
    (41, -1),
    (39, 1),
    (40, 1),
    (41, 1),
    (38, 0),
}


def test_the_inserter_drops_into_a_machine_larger_than_one_tile() -> None:
    """The live defect, tile for tile.

    Measured on the arena at tick 25231279: the boiler at (-1, 9.5) sat at
    ``no_fuel`` with 41 coal in a wooden chest 1.5 tiles away, its burner
    inserter at (0.5, 9.5) reporting ``waiting_for_space_in_destination``
    with a drop position of (0.5, 10.7) -- open ground. The inserter had
    landed on the only free side while the script rotated it towards the side
    it had asked for, and no side of a machine three tiles long is one tile
    from the position the call was given.
    """
    world, scope = _run_feed(blocked=BOILER_PIPES, shapes=BOILER_SHAPE)
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    by_machine = {row["machine"]: row for row in rows}
    assert by_machine["boiler"]["coal_loaded"], (
        f"the boiler was left unfed: {by_machine['boiler']['placement_note']}"
    )
    inserter = next(
        entity
        for entity in world.placed
        if entity.name == "burner-inserter"
        and abs(entity.position.x - 40) <= 4
        and abs(entity.position.y) <= 4
    )
    assert inserter.direction is not None
    drop = _Position(
        inserter.position.x + inserter.direction[0],
        inserter.position.y + inserter.direction[1],
    )
    boiler = world.entities.get(_Position(41, 0))
    assert boiler is not None and boiler.name == "boiler"
    assert world.entities.get(drop) is boiler, (
        f"the inserter drops at {tuple(drop)}, which is not a boiler tile"
    )
    pickup = _Position(
        inserter.position.x - inserter.direction[0],
        inserter.position.y - inserter.direction[1],
    )
    chest = world.entities.get(pickup)
    assert chest is not None and chest.name == "wooden-chest"
    assert chest.inventory.get("coal", 0) > 0


def test_a_crowded_machine_does_not_take_the_other_feeds_down() -> None:
    # Every tile around the boiler is occupied, as pipes and the steam engine
    # can leave it in this arena.
    blocked = {(40, -1), (40, 1), (39, 0), (41, 0)}
    world, scope = _run_feed(blocked=blocked)
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    by_machine = {row["machine"]: row for row in rows}
    assert by_machine["boiler"]["coal_loaded"] == 0.0
    assert by_machine["boiler"]["placement_note"]
    assert scope["fuel_fed_count"] == len(MACHINE_POSITIONS) - 1
    assert not [
        entity for entity in world.placed if entity.position in blocked
    ], "a failed side left an orphan entity behind"
    # FLE answers a blocked side with a position further out rather than a
    # refusal, and an inserter two tiles away drops on the ground. Nothing
    # may be left standing around a machine that could not be fed.
    assert [
        entity
        for entity in world.placed
        if abs(entity.position.x - 40) <= 3 and abs(entity.position.y) <= 3
    ] == []
    assert by_machine["coal_drill"]["coal_loaded"] > 0


def test_an_undefined_machine_is_reported_as_unmeasured() -> None:
    _, scope = _run_feed(missing={"smelt_drill"})
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    by_machine = {row["machine"]: row for row in rows}
    assert by_machine["iron_smelt_drill"]["coal_loaded"] is None
    assert by_machine["iron_smelt_drill"]["placement_note"]
    assert by_machine["coal_drill"]["coal_loaded"] > 0


def test_an_empty_vault_still_leaves_the_feeds_standing() -> None:
    # Hardware first: the chests and inserters have to exist even when there is
    # nothing to put in them, because the coal drill fills them afterwards.
    world, scope = _run_feed(vault_coal=0)
    assert scope["fuel_dose"] == 0
    assert len([e for e in world.placed if e.name == "burner-inserter"]) == len(
        MACHINE_POSITIONS
    )


def test_a_feed_that_cannot_finish_leaves_no_orphan_inserter() -> None:
    # The inserter fits on every side of the boiler but the chest tile behind
    # it is taken on all four. A half-built feed that is left standing both
    # wastes an inserter and occupies the tile the next attempt needs.
    chest_tiles = {(40, -2), (40, 2), (38, 0), (42, 0)}
    world, scope = _run_feed(blocked=chest_tiles)
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    by_machine = {row["machine"]: row for row in rows}
    assert by_machine["boiler"]["coal_loaded"] == 0.0
    around_the_boiler = [
        entity
        for entity in world.placed
        if abs(entity.position[0] - 40) <= 2 and abs(entity.position[1]) <= 2
    ]
    assert around_the_boiler == [], (
        f"half-built feeds left standing: "
        f"{[(e.name, e.position) for e in around_the_boiler]}"
    )
    assert world.picked_up, "no orphan inserter was ever removed"


# --------------------------------------------------------------------------
# The heir's world, as generations 44 and 46 measured it: four to six coal in
# the inventory, no quarantine of its own to reopen, and containers standing
# in the factory it inherited with hundreds of coal in them.
# --------------------------------------------------------------------------

#: Containers of the standing world and what each holds, in the shape the
#: supply survey reports them. The figures are the ones read off the live
#: world during generation 46.
WORLD_COAL: dict[tuple[float, float], int] = {
    (27.5, 10.5): 279,
    (27.5, 70.5): 350,
}

#: What generation 46 arrived carrying. `fuel_share` is
#: ``max(0, carried // 7 - 1)``, which is zero for anything under fourteen.
HEIR_CARRIED_COAL = 6


def _machine_charge() -> int:
    return len(curriculum_runner.FUEL_FED_MACHINES) * (
        197 + curriculum_runner.FUEL_FEED_PRIMER_COAL
    )


def _world_plan(*, chain: frozenset[tuple[float, float]] = frozenset()) -> Any:
    """What the survey would hand the installer for the world above."""
    return plan_supply(
        anchor=(0.0, 0.0),
        fuel_needed=_machine_charge(),
        fuel_carried=HEIR_CARRIED_COAL,
        fuel_sources=tuple(
            FuelSource(
                position=position,
                available=amount,
                supplies_chain=position in chain,
            )
            for position, amount in WORLD_COAL.items()
        ),
    )


def _run_step(
    *,
    supply: Any,
    carried: int = HEIR_CARRIED_COAL,
    machine_targets: dict[str, int] | None = None,
) -> tuple[_World, dict[str, object], dict[str, object]]:
    """The whole installer step, against the stand-in world.

    ``bootstrap_vault`` is None and the quarantine is zero, which is the
    inherited world exactly: an heir that adopted its ancestor's cell parked
    nothing of its own, and generation 46's journal carries the resulting
    ``'NoneType' object has no attribute 'position'`` in ``vault_note``.
    """
    world = _World()
    scope = world.namespace()
    for variable, position in MACHINE_POSITIONS.items():
        scope[variable] = world.spawn(variable, position)
    for position, amount in WORLD_COAL.items():
        world.spawn("iron-chest", position).inventory["coal"] = amount
    scope["bootstrap_vault"] = None
    scope["bootstrap_quarantine"] = 0
    world.player.inventory["coal"] = carried
    report: dict[str, object] = {}
    scope["print"] = report.update
    script = curriculum_runner._fuel_feed_code(
        supply,
        machines=curriculum_runner.FUEL_FED_MACHINES,
        coal_per_machine=197,
        fuel_needed=_machine_charge(),
        machine_targets=machine_targets,
    )
    exec(compile(script, "<fuel-feed>", "exec"), scope)  # noqa: S102
    return world, scope, report


def test_the_whole_step_does_not_trip_the_failure_heuristic() -> None:
    assert (
        _trigger_offenders(
            curriculum_runner._fuel_feed_code(
                _world_plan(),
                machines=curriculum_runner.FUEL_FED_MACHINES,
                coal_per_machine=197,
                fuel_needed=_machine_charge(),
            )
        )
        == []
    )


def test_an_heir_fills_its_feed_out_of_the_world_it_inherited() -> None:
    """The regression of generations 44 and 46, as a measurement.

    Six inherited coal split seven ways is a dose of zero: both generations
    built fourteen entities, loaded ``coal_loaded_total: 0.0`` into them and
    then lost the electronic-circuits capability they had inherited.
    """
    world, scope, report = _run_step(supply=_world_plan())
    assert scope["fuel_coal_loaded_total"] > 0, "the feed is still installed empty"
    assert report["fuel_supply_drawn"] > 0
    rows = curriculum_runner._fuel_feed_rows(scope["fuel_feed_log"])
    assert rows is not None
    loaded = {row["machine"]: row["coal_loaded"] for row in rows}
    # 629 coal drawn out of the world cover the first machines of the
    # priority order in full; what the world does not hold is not invented
    # for the rest.
    assert loaded["coal_drill"] == 197.0
    assert loaded["boiler"] == 197.0
    # The chests hold what the ledger says was loaded, and nothing was
    # invented: the draw cannot exceed what the containers held.
    chests = [entity for entity in world.placed if entity.name == "wooden-chest"]
    assert sum(chest.inventory.get("coal", 0) for chest in chests) == scope[
        "fuel_coal_loaded_total"
    ]
    assert report["fuel_supply_drawn"] <= sum(WORLD_COAL.values())


def test_the_containers_the_draw_took_from_are_still_standing() -> None:
    """Drawing fuel is not dismantling. Every container keeps its place."""
    world, _, _ = _run_step(supply=_world_plan())
    for position in WORLD_COAL:
        assert position in world.entities, f"the draw removed the chest at {position}"
    assert [entity.name for entity in world.picked_up] == []


def test_a_feed_with_no_draw_is_installed_empty() -> None:
    """The defect itself, kept as the thing the draw has to prevent.

    A step assembled without a plan -- which is what an unsurveyed world
    gives -- divides the six coal the heir inherited and gives every machine
    zero. This is the reading generation 46 reported.
    """
    _, scope, _ = _run_step(supply=None)
    assert scope["fuel_dose"] == 0
    assert scope["fuel_coal_loaded_total"] == 0


def test_the_anchor_is_a_machine_that_is_standing() -> None:
    namespace = SimpleNamespace(boiler=SimpleNamespace(position=SimpleNamespace(x=40.0, y=2.0)))
    assert curriculum_runner._fuel_feed_anchor(
        namespace,
        curriculum_runner.FUEL_FED_MACHINES,
    ) == (40.0, 2.0)


def test_an_arena_with_no_machine_has_no_anchor() -> None:
    # None is not the origin. A draw planned around (0, 0) would order the
    # containers by their distance to a point no machine stands on.
    assert (
        curriculum_runner._fuel_feed_anchor(
            SimpleNamespace(),
            curriculum_runner.FUEL_FED_MACHINES,
        )
        is None
    )


# --------------------------------------------------------------------------
# The reserve: a container that feeds a chain keeps what that chain burns.
# --------------------------------------------------------------------------


class _SupplyRcon:
    """RCON stand-in answering the reads a supply survey makes."""

    def __init__(self, chests: dict[tuple[float, float], int], carried: int) -> None:
        self._chests = dict(chests)
        self._carried = carried

    def send_command(self, command: str) -> str:
        if "get_main_inventory" in command:
            return str(self._carried)
        match = re.search(r"\{x=(-?[\d.]+),y=(-?[\d.]+)\}", command)
        if match is None:
            return ""
        key = (float(match.group(1)), float(match.group(2)))
        return str(self._chests.get(key, 0))


def _supply_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    roles: tuple[ContainerRole, ...],
    chests: dict[tuple[float, float], int],
    carried: int = 0,
) -> Any:
    instance = SimpleNamespace(
        rcon_client=_SupplyRcon(chests, carried),
        namespace=SimpleNamespace(),
    )
    monkeypatch.setattr(
        curriculum_runner,
        "survey_world",
        lambda env: SimpleNamespace(entities=(), footprints={}),
    )
    monkeypatch.setattr(curriculum_runner, "build_factory_graph", lambda entities: {})
    monkeypatch.setattr(curriculum_runner, "container_roles", lambda graph: roles)
    return SimpleNamespace(unwrapped=SimpleNamespace(instance=instance))


SPARE_CHEST = ContainerRole(
    node_id="spare",
    name="iron-chest",
    position=(27.5, 10.5),
)
FEEDING_CHEST = ContainerRole(
    node_id="feeding",
    name="iron-chest",
    position=(27.5, 70.5),
    supplies_chain=True,
)


def test_a_container_that_feeds_a_chain_keeps_what_its_chain_burns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emptying the chest an inserter pulls from stops the machine behind it.

    The chest that feeds nothing is drawn dry; the one that is feeding a
    machine gives up only what it holds above a burner machine's charge for
    the rest of the generation.
    """
    env = _supply_env(
        monkeypatch,
        roles=(SPARE_CHEST, FEEDING_CHEST),
        chests={SPARE_CHEST.position: 279, FEEDING_CHEST.position: 350},
    )
    plan = curriculum_runner.survey_stage_supply(
        env,
        anchor=(27.5, 40.0),
        fuel_needed=2000,
        container_needed=False,
        chain_reserve=197,
    )
    assert plan is not None
    drawn = {draw.position: draw.quantity for draw in plan.fuel_draws}
    assert drawn[SPARE_CHEST.position] == 279
    assert drawn[FEEDING_CHEST.position] == 350 - 197, (
        "the draw emptied a container a machine is waiting on"
    )


def test_a_stage_that_asks_for_no_reserve_draws_as_it_always_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stages 1 to 7 pass no reserve, and their draw must be the one they have
    # always made: the default cannot change what they take.
    env = _supply_env(
        monkeypatch,
        roles=(FEEDING_CHEST,),
        chests={FEEDING_CHEST.position: 350},
    )
    plan = curriculum_runner.survey_stage_supply(
        env,
        anchor=(27.5, 40.0),
        fuel_needed=40,
        container_needed=False,
    )
    assert plan is not None
    assert [draw.quantity for draw in plan.fuel_draws] == [40]


# --------------------------------------------------------------------------
# The boiler: the one machine on the list whose outage stops every electric
# machine, and the only one whose burn rate nothing in this lab had measured.
# --------------------------------------------------------------------------


def test_the_installer_sizes_the_boiler_from_the_runtime() -> None:
    called = _called_function_names("_install_fuel_feeds")
    assert "_runtime_fuel_feed_figures" in called, (
        "the boiler charge is still sized from a literal instead of the draw "
        "the prototype reports"
    )
    assert "profile_from_energy_per_tick" in called
    assert "_boiler_covered_seconds" in called, (
        "boiler_covered_seconds stays null, so nothing says whether the "
        "charge outlasts the generation"
    )


def test_boiler_coverage_is_measured_from_the_charge_and_the_draw() -> None:
    # 30000 J/tick is 1.8 MW, read over RCON from the live 2.0.73 runtime;
    # 4 MJ of coal against it is 2.22 s of full draw per unit.
    profile = profile_from_energy_per_tick("boiler", 30000)
    assert profile is not None
    covered = curriculum_runner._boiler_covered_seconds(
        [{"machine": "boiler", "coal_loaded": 90.0}],
        profile,
        "boiler",
    )
    assert covered == pytest.approx(200.0, abs=0.5)


def test_boiler_coverage_without_a_measured_draw_is_none() -> None:
    # A probe that did not answer leaves the rate unknown. Reporting 0.0
    # would say the charge covers nothing, which is a measurement nobody
    # made.
    assert (
        curriculum_runner._boiler_covered_seconds(
            [{"machine": "boiler", "coal_loaded": 90.0}],
            None,
            "boiler",
        )
        is None
    )


def test_boiler_coverage_without_a_measured_charge_is_none() -> None:
    profile = profile_from_energy_per_tick("boiler", 30000)
    assert curriculum_runner._boiler_covered_seconds(None, profile, "boiler") is None
    assert curriculum_runner._boiler_covered_seconds([], profile, "boiler") is None
    assert (
        curriculum_runner._boiler_covered_seconds(
            [{"machine": "boiler", "coal_loaded": None}],
            profile,
            "boiler",
        )
        is None
    )


def test_a_boiler_that_was_fed_nothing_covers_zero_seconds() -> None:
    # A measured zero is not an absence: the feed was built and loaded
    # nothing, and that reads as no coverage rather than as no measurement.
    profile = profile_from_energy_per_tick("boiler", 30000)
    assert (
        curriculum_runner._boiler_covered_seconds(
            [{"machine": "boiler", "coal_loaded": 0.0}],
            profile,
            "boiler",
        )
        == 0.0
    )


# --------------------------------------------------------------------------
# The whole installer, from the prototype read to the line in the journal.
# --------------------------------------------------------------------------


class _RecordingExecutor:
    """Runs nothing; keeps the script it was handed."""

    def __init__(self) -> None:
        self.code: str | None = None

    def execute(self, code: str, **_: Any) -> Any:
        self.code = code
        return SimpleNamespace(
            accepted=True,
            info={"error_occurred": False},
            candidate_game_state=object(),
        )


class _Journal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"stage": "Steam power", "metrics": {}}
        self.events: list[tuple[str, str]] = []

    def event(self, kind: str, message: str, **_: Any) -> None:
        self.events.append((kind, message))


#: What the live 2.0.73 runtime answered for the three figures the feed sizes
#: itself from, read over RCON on 2026-09-23.
RUNTIME_FIGURES = (
    '{"boiler_energy_per_tick":30000,"chest_slots":16,"coal_stack_size":50}'
)


def _installer_namespace() -> Any:
    namespace = SimpleNamespace(
        **{
            variable: SimpleNamespace(
                position=SimpleNamespace(x=float(x), y=float(y))
            )
            for variable, (x, y) in MACHINE_POSITIONS.items()
        }
    )
    namespace.fuel_stock = 300
    namespace.fuel_dose = 0
    namespace.fuel_fed_count = 7
    namespace.fuel_coal_loaded_total = 293
    namespace.fuel_vault_stock = 0
    namespace.fuel_vault_claim = 0
    namespace.fuel_vault_released = 0
    namespace.supply_fuel_drawn = 294
    namespace.fuel_feed_log = [
        ("coal_drill", 197, 1, ""),
        ("boiler", 96, 1, ""),
        ("copper_drill", 0, 1, ""),
        ("iron_baseline_drill", 0, 1, ""),
        ("iron_scale_drill", 0, 1, ""),
        ("iron_smelt_drill", 0, 1, ""),
        ("iron_logistics_drill", 0, 1, ""),
    ]
    return namespace


def _run_installer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rcon_answer: str,
) -> tuple[dict[str, Any], _RecordingExecutor, _Journal]:
    monkeypatch.setattr(
        curriculum_runner,
        "survey_stage_supply",
        lambda *args, **kwargs: None,
    )
    namespace = _installer_namespace()
    instance = SimpleNamespace(
        rcon_client=SimpleNamespace(send_command=lambda command: rcon_answer),
        namespace=namespace,
    )
    env = SimpleNamespace(unwrapped=SimpleNamespace(instance=instance))
    executor = _RecordingExecutor()
    journal = _Journal()
    payload = curriculum_runner._install_fuel_feeds(executor, env, namespace, journal)
    return payload, executor, journal


def test_the_installer_gives_the_boiler_its_own_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boiler is sized from its draw, not from the drill figure.

    1.8 MW against 4 MJ of coal is 1890 coal for a 4200 s generation, 2363
    with the margin every charge here carries, and one wooden chest holds
    800. The charge that reaches the script is the capped one, and both
    numbers are in the journal so the gap is readable.
    """
    payload, executor, _ = _run_installer(monkeypatch, rcon_answer=RUNTIME_FIGURES)
    assert payload["boiler_power_w"] == pytest.approx(1_800_000.0)
    assert payload["boiler_power_source"] == "runtime_prototype"
    assert payload["boiler_coal_horizon_need"] == 2363.0
    assert payload["boiler_feed_capacity_coal"] == 800.0
    assert payload["boiler_coal_target"] == 800.0
    assert payload["coal_target_per_machine"] == 197.0
    assert executor.code is not None
    assert "'boiler',800" in executor.code.replace(" ", ""), (
        "the boiler charge never reached the remote script"
    )


def test_the_installer_measures_how_long_the_boiler_charge_lasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, journal = _run_installer(monkeypatch, rcon_answer=RUNTIME_FIGURES)
    assert payload["boiler_coal_loaded"] == 96.0
    # 96 coal at 1.8 MW is 213 s of a 4200 s generation.
    assert payload["boiler_covered_seconds"] == pytest.approx(213.3, abs=0.5)
    refusals = [message for kind, message in journal.events if kind == "refusal"]
    assert refusals, "a charge that runs out mid-generation was recorded silently"
    assert "213" in refusals[0] and "4200" in refusals[0]


def test_an_unanswered_prototype_read_leaves_the_boiler_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No answer is not a zero, and not a literal either.

    Without the draw there is no coverage to report and no charge to size,
    so the boiler keeps the drill charge the split has always given it and
    the journal says which of the two it got.
    """
    payload, _, journal = _run_installer(monkeypatch, rcon_answer="")
    assert payload["boiler_power_w"] is None
    assert payload["boiler_power_source"] == "unmeasured"
    assert payload["boiler_coal_target"] is None
    assert payload["boiler_covered_seconds"] is None
    assert payload["boiler_coal_loaded"] == 96.0
    assert [kind for kind, _ in journal.events] == ["fuel_feed"]
