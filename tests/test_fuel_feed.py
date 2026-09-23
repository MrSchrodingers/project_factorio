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

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.fuel import BURNER_MINING_DRILL

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

OPPOSITE_DIRECTION = {
    "UP": "DOWN",
    "DOWN": "UP",
    "LEFT": "RIGHT",
    "RIGHT": "LEFT",
}

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


def _direction_pairs(source: str) -> list[tuple[str, str]]:
    """(placement side, rotation side) pairs the feed script iterates over."""
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Tuple) or not isinstance(node.iter, ast.Tuple):
            continue
        for element in node.iter.elts:
            if not isinstance(element, ast.Tuple) or len(element.elts) != 2:
                continue
            sides = [
                piece.attr
                for piece in element.elts
                if isinstance(piece, ast.Attribute)
            ]
            if len(sides) == 2:
                pairs.append((sides[0], sides[1]))
    return pairs


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


def _keyword_name(call: ast.Call, keyword: str) -> str | None:
    for entry in call.keywords:
        if entry.arg == keyword and isinstance(entry.value, ast.Name):
            return entry.value.id
    return None


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


def test_the_inserter_is_rotated_back_towards_the_machine() -> None:
    pairs = _direction_pairs(_feed_script())
    assert pairs, "the feed script tries no placement side at all"
    assert {side for side, _ in pairs} == set(OPPOSITE_DIRECTION)
    for side, rotation in pairs:
        assert rotation == OPPOSITE_DIRECTION[side], (
            f"an inserter placed on the {side} side of the machine and rotated "
            f"{rotation} drops its coal away from the machine"
        )


def test_the_rotation_probe_catches_a_known_positive() -> None:
    # A feed that rotates the inserter to the side it was placed on delivers
    # into empty ground; the predicate above must reject exactly that.
    mutated = (
        "for fuel_side,fuel_back in ("
        "(Direction.UP,Direction.UP),"
        "(Direction.DOWN,Direction.DOWN),"
        "(Direction.LEFT,Direction.LEFT),"
        "(Direction.RIGHT,Direction.RIGHT),"
        "):\n    pass\n"
    )
    pairs = _direction_pairs(mutated)
    assert pairs
    assert any(rotation != OPPOSITE_DIRECTION[side] for side, rotation in pairs)


def test_the_chest_sits_on_the_inserter_pickup_tile() -> None:
    source = _feed_script()
    inserters = _placement_calls(source, "BurnerInserter")
    chests = _placement_calls(source, "WoodenChest")
    assert len(inserters) == 1, "expected exactly one burner inserter placement"
    assert len(chests) == 1, "expected exactly one fuel chest placement"
    inserter_side = _keyword_name(inserters[0], "direction")
    chest_side = _keyword_name(chests[0], "direction")
    assert inserter_side is not None and chest_side == inserter_side, (
        "the chest must extend away from the machine along the same axis, "
        "which is the tile a Factorio inserter picks up from"
    )
    reference = chests[0].args[1]
    assert isinstance(reference, ast.Attribute)
    assert reference.attr == "position"
    assert isinstance(reference.value, ast.Name)
    assert reference.value.id.endswith("_inserter")


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
    called = _called_function_names("_install_fuel_feeds")
    assert "_fuel_feed_script" in called
    assert "_bootstrap_vault_release_script" in called, (
        "the 359 coal quarantined in the bootstrap vault stay unreachable"
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


class _Direction:
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)


class _Entity:
    def __init__(self, name: str, position: tuple[int, int]) -> None:
        self.name = name
        self.position = position
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

    def spawn(self, name: str, position: tuple[int, int]) -> _Entity:
        entity = _Entity(name, position)
        self.entities[position] = entity
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
        position: tuple[int, int],
        direction: tuple[int, int],
        spacing: int = 0,
    ) -> _Entity:
        target = (
            position[0] + direction[0] * (spacing + 1),
            position[1] + direction[1] * (spacing + 1),
        )
        if target in self.blocked or target in self.entities:
            raise ValueError(f"cannot place {prototype.name} at {target}")
        entity = self.spawn(prototype.name, target)
        entity.direction = direction
        self.placed.append(entity)
        return entity

    def rotate_entity(self, entity: _Entity, direction: tuple[int, int]) -> _Entity:
        entity.direction = direction
        return entity

    def pickup_entity(self, entity: _Entity) -> bool:
        self.entities.pop(entity.position, None)
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

    def extract_item(
        self,
        prototype: _Proto,
        entity: _Entity,
        quantity: int,
    ) -> int:
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
) -> tuple[_World, dict[str, object]]:
    world = _World(blocked=blocked)
    scope = world.namespace()
    for variable, position in MACHINE_POSITIONS.items():
        if variable in (missing or set()):
            continue
        scope[variable] = world.spawn(variable, position)
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


def test_the_charge_is_split_and_never_exceeds_the_stock() -> None:
    world, scope = _run_feed()
    chests = [e for e in world.placed if e.name == "wooden-chest"]
    loaded = [chest.inventory.get("coal", 0) for chest in chests]
    assert all(amount > 0 for amount in loaded), (
        "the first machine spent the whole stock and the rest got nothing"
    )
    assert sum(loaded) <= 359
    assert scope["fuel_coal_loaded_total"] == sum(loaded)
    assert scope["fuel_fed_count"] == len(MACHINE_POSITIONS)
    inserters = [e for e in world.placed if e.name == "burner-inserter"]
    assert all(entity.inventory.get("coal", 0) == 1 for entity in inserters), (
        "a burner inserter with no coal never makes its first swing"
    )


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
