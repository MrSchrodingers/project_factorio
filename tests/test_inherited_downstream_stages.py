"""The stages after the mining cell also run inside an inherited factory.

Generation 37 was promoted, so generations 38 and 39 started on top of its
factory and both stopped at the same line: ``smelt_drill = place_entity(...)``
answered ``entity already exists at the target position {x = 27, y = 73}``,
because stage 3 aims at ``(center[0], center[1] - 10.5)`` and the ancestor's
own smelting cell is standing there. Three stages died with it -- the A*
belt line, belt-fed smelting and everything the curriculum reaches through
them -- and the report went from 14 completed stages to 3.

The world below is the promoted checkpoint as it was decoded on 2026-09-23:
the drill at (27, 73) with its furnace at (27, 75), the four mining cells at
y=83, the belt lane, and the single chest at (32.5, 8.5) that no material
edge touches. The inventory is that checkpoint's inheritance ledger: 44
drills, 7 stone furnaces, 492 belts, 41 inserters, 24 iron ore, and neither
coal nor a chest.

Two things are kept apart here. What a stage built is what it placed and
what its own machine holds; what the inherited factory produces is the world
counter, which keeps moving whatever this generation does. A stage that took
its reading from the second would report the ancestor's flow as its own.
"""

from __future__ import annotations

import ast
import math
import re
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning import resupply
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_tiles

CENTRE = (27.0, 83.0)
DRILL = "burner-mining-drill"
CHEST = "wooden-chest"
IRON_CHEST = "iron-chest"
FURNACE = "stone-furnace"
INSERTER = "burner-inserter"
BELT = "transport-belt"

#: Iron patch bounding box measured live; all of its tiles carry ore.
PATCH_BOUNDS = (15.5, 70.5, 38.5, 95.5)

#: Substrings FLE turns into a failed step, from the same source
#: tests/test_fle_triggers.py cites: fle/env/gym_env/environment.py:451.
TRIGGERS = ("error", "exception: ")

#: 136 game seconds at 60 ticks per second.
STEP_TICKS = 8160

#: Tile footprint per entity, as the Factorio 2.0 prototypes give it. Stated
#: here rather than imported so the simulator does not agree with the code
#: under test by construction.
FOOTPRINTS: dict[str, tuple[int, int]] = {
    DRILL: (2, 2),
    FURNACE: (2, 2),
    CHEST: (1, 1),
    IRON_CHEST: (1, 1),
    INSERTER: (1, 1),
    BELT: (1, 1),
}

#: Entity name per FLE prototype name, for reading a script back.
PROTOTYPE_NAMES: dict[str, str] = {
    "BurnerMiningDrill": DRILL,
    "StoneFurnace": FURNACE,
    "WoodenChest": CHEST,
    "IronChest": IRON_CHEST,
    "BurnerInserter": INSERTER,
    "TransportBelt": BELT,
}

#: A script is read back with its whitespace removed, so a prototype name
#: runs into whatever follows it. The alternation is what keeps
#: ``Prototype.WoodenChest`` from being read as ``WoodenChestlogistics``.
_BINDING = re.compile(
    r"(\w+)=Prototype\.(" + "|".join(sorted(PROTOTYPE_NAMES, reverse=True)) + r")"
)
_PLACE = re.compile(
    r"place_entity\(([A-Za-z_.]+),position=Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)"
    r"(,exact=False)?"
)
_PLACE_NEXT = re.compile(
    r"place_entity_next_to\(([A-Za-z_.]+),(\w+)\.position,direction=Direction\.(\w+)"
)
_EXTRACT = re.compile(
    r"extract_item\(Prototype\.(\w+),Position\(x=(-?[\d.]+),y=(-?[\d.]+)\),"
    r"quantity=min\(supply_short,(\d+)\)"
)
_FIND_ENTITY = re.compile(r"find_entity\('([a-z-]+)',\{x=(-?[\d.]+),y=(-?[\d.]+)\}\)")
_ITEM_COUNT = re.compile(r"get_item_count\((?:'([a-z-]+)')?\)")
_CARRIED = re.compile(r"get_main_inventory\(\)")


def _entity(name: str, x: float, y: float, direction: int = 0) -> dict[str, Any]:
    return {"name": name, "position": {"x": x, "y": y}, "direction": direction}


INHERITED_WORLD: tuple[dict[str, Any], ...] = (
    _entity(CHEST, 27.5, 70.5),
    _entity(INSERTER, 27.5, 71.5),
    _entity(DRILL, 27.0, 73.0, 8),
    _entity(FURNACE, 27.0, 75.0),
    _entity(CHEST, 19.5, 80.5),
    _entity(INSERTER, 19.5, 81.5),
    _entity(CHEST, 27.5, 80.5),
    _entity(INSERTER, 27.5, 81.5),
    _entity(CHEST, 32.5, 80.5),
    _entity(INSERTER, 32.5, 81.5),
    _entity(DRILL, 19.0, 83.0, 8),
    _entity(DRILL, 23.0, 83.0, 8),
    _entity(DRILL, 27.0, 83.0, 8),
    _entity(DRILL, 32.0, 83.0, 8),
    _entity(BELT, 19.5, 84.5, 4),
    _entity(BELT, 20.5, 84.5, 4),
    _entity(CHEST, 27.5, 84.5),
    _entity(CHEST, 32.5, 84.5),
    _entity(BELT, 24.5, 86.5, 4),
    _entity(INSERTER, 25.5, 86.5, 12),
    _entity(INSERTER, 27.5, 86.5, 12),
    _entity(CHEST, 26.5, 86.5),
    _entity(FURNACE, 29.0, 87.0),
    _entity(CHEST, 32.5, 8.5),
)

#: Coal as the containers of this subset were measured to hold it, read off
#: the same checkpoint. The world holds more of it in chests further away;
#: these four are the ones near the patch, and they are what the stages
#: around the patch actually reach.
COAL_STOCK: dict[tuple[float, float], int] = {
    (27.5, 70.5): 36,
    (19.5, 80.5): 13,
    (27.5, 80.5): 14,
    (32.5, 80.5): 14,
}

#: The one chest no material edge touches.
ORPHAN = (32.5, 8.5)

#: The inheritance ledger of the promoted checkpoint, in the items these
#: stages reach for. No coal and no chest is the whole problem.
INHERITED_INVENTORY: dict[str, int] = {
    "coal": 0,
    CHEST: 0,
    IRON_CHEST: 0,
    "iron-ore": 24,
    "iron-plate": 0,
    FURNACE: 7,
}


def _snap(value: float, size: int) -> float:
    """Where the engine actually anchors an entity of this size."""
    if size % 2 == 0:
        return float(math.floor(value + 0.5))
    return float(math.floor(value)) + 0.5


def _snapped(name: str, position: tuple[float, float]) -> tuple[float, float]:
    width, height = FOOTPRINTS.get(name, (1, 1))
    return (_snap(position[0], width), _snap(position[1], height))


def _next_to(
    name: str,
    reference: dict[str, Any],
    direction: str,
) -> tuple[float, float]:
    """Where ``place_entity_next_to`` lands, by the engine's own arithmetic.

    ``ceil(ref_side + side) / 2`` from the reference centre
    (place_entity_next_to/server.lua:179), then snapped. Measured against the
    inherited world it answers the pair that is standing there: the 2x2
    furnace below the 2x2 drill at (27, 73) lands on (27, 75).
    """
    ref_name = reference["name"]
    ref_w, ref_h = FOOTPRINTS.get(ref_name, (1, 1))
    width, height = FOOTPRINTS.get(name, (1, 1))
    x = float(reference["position"]["x"])
    y = float(reference["position"]["y"])
    if direction == "DOWN":
        y += math.ceil(ref_h + height) / 2
    elif direction == "UP":
        y -= math.ceil(ref_h + height) / 2
    elif direction == "RIGHT":
        x += math.ceil(ref_w + width) / 2
    else:
        x -= math.ceil(ref_w + width) / 2
    return _snapped(name, (x, y))


class _FakeWorld:
    """The live world, as far as these stages can observe it."""

    def __init__(
        self,
        *,
        entities: tuple[dict[str, Any], ...] = INHERITED_WORLD,
        stock: dict[tuple[float, float], int] | None = None,
        carried: dict[str, int] | None = None,
    ) -> None:
        self.entities: list[dict[str, Any]] = [dict(e) for e in entities]
        self.stock = dict(COAL_STOCK if stock is None else stock)
        self.carried = dict(INHERITED_INVENTORY if carried is None else carried)
        self.contents: dict[tuple[float, float], dict[str, float]] = {}
        self.iron_produced = 0.0
        self.plate_produced = 0.0
        self.ticks = 1000

    def snapshot(self) -> tuple[Any, ...]:
        return (
            [dict(e) for e in self.entities],
            dict(self.stock),
            dict(self.carried),
            {key: dict(value) for key, value in self.contents.items()},
            self.iron_produced,
            self.plate_produced,
            self.ticks,
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        (
            entities,
            stock,
            carried,
            contents,
            iron,
            plate,
            ticks,
        ) = snapshot
        self.entities = [dict(e) for e in entities]
        self.stock = dict(stock)
        self.carried = dict(carried)
        self.contents = {key: dict(value) for key, value in contents.items()}
        self.iron_produced = iron
        self.plate_produced = plate
        self.ticks = ticks

    def taken(self) -> set[Any]:
        return blocked_tiles(self.entities, FOOTPRINTS)

    def add(self, name: str, position: tuple[float, float]) -> bool:
        """Place one entity where the engine would, or refuse the tiles."""
        placed = _entity(name, position[0], position[1])
        if entity_tiles(placed, FOOTPRINTS) & self.taken():
            return False
        self.entities.append(placed)
        return True

    def free_near(self, name: str, position: tuple[float, float]) -> tuple[float, float]:
        """Where ``exact=False`` would put it: the first free tile outwards."""
        for radius in range(8):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    candidate = _snapped(name, (position[0] + dx, position[1] + dy))
                    probe = _entity(name, candidate[0], candidate[1])
                    if not entity_tiles(probe, FOOTPRINTS) & self.taken():
                        return candidate
        return position

    def item_count(self, x: float, y: float, item: str | None) -> int:
        held = self.contents.get((x, y), {})
        if item == "coal" or (item is None and (x, y) in self.stock):
            return int(self.stock.get((x, y), held.get("coal", 0)))
        if item is None:
            return int(sum(held.values()))
        return int(held.get(item, 0))


class _FakeRcon:
    """Answers the readings the placement and supply layers take."""

    def __init__(self, world: _FakeWorld, *, answers: bool = True) -> None:
        self.world = world
        self.answers = answers
        self.commands: list[str] = []

    def send_command(self, command: str) -> str:
        self.commands.append(command)
        if "teleport" in command:
            return f"{CENTRE[0]},{CENTRE[1]}"
        if not self.answers:
            return ""
        item = _ITEM_COUNT.search(command)
        if _CARRIED.search(command) is not None:
            if item is None or item[1] is None:
                return ""
            return str(self.world.carried.get(item[1], 0))
        found = _FIND_ENTITY.search(command)
        if found is not None and item is not None:
            return str(self.world.item_count(float(found[2]), float(found[3]), item[1]))
        return ""


class _FakeNamespace:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.player_location = None

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self.world.entities]

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {
            "output": {
                "iron-ore": self.world.iron_produced,
                "iron-plate": self.world.plate_produced,
            }
        }


class _FakeInstance:
    def __init__(self, world: _FakeWorld, *, answers: bool = True) -> None:
        self.world = world
        self.namespace = _FakeNamespace(world)
        self.namespaces = [self.namespace]
        self.rcon_client = _FakeRcon(world, answers=answers)

    def get_elapsed_ticks(self) -> int:
        return self.world.ticks


class _FakeEnv:
    def __init__(self, world: _FakeWorld, *, answers: bool = True) -> None:
        self.world = world
        self.unwrapped = SimpleNamespace(
            instance=_FakeInstance(world, answers=answers)
        )


class _FakeStep:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False}
        self.candidate_game_state: Any = object()
        self.reward = 12.0
        self.accepted = False


#: What the inherited factory adds to the world counters while a step runs,
#: whatever the step itself does. Every rejected step rewinds it, which is
#: why a stage that reads its output from here reads the same number whether
#: it produced anything or not.
INHERITED_IRON_FLOW = 95.0
INHERITED_PLATE_FLOW = 40.0

#: What one furnace of this generation actually smelts in the window.
PROBE_PLATE_FLOW = 7.0

#: What the belt-fed furnace of stage 5 actually smelts in its own window.
#: Distinct from both numbers above on purpose: the stage is only measuring
#: itself if its reading matches this one and not their sum.
BELT_PLATE_FLOW = 9.0

#: What one cell of this generation actually mines into its own container.
CELL_ORE_FLOW = 31.0


class _FakeExecutor:
    """Runs a stage script against the fake world, as the engine would.

    Reproduces what these stages depend on: a placement on occupied tiles
    fails the step, the world counters and the game clock advance while the
    step runs even when the placement failed, a rejected step is rolled back,
    and the variables the script assigned are readable off the namespace
    exactly when the script got that far.
    """

    def __init__(self, env: _FakeEnv) -> None:
        self.world = env.world
        self.namespace = env.unwrapped.instance.namespace
        self.scripts: list[str] = []
        self.placed: list[tuple[str, tuple[float, float]]] = []
        self.refused: list[tuple[str, tuple[float, float]]] = []
        self.drawn: list[tuple[str, tuple[float, float]]] = []

    def _clear(self) -> None:
        for name in (
            "smelt_plates",
            "smelt_drill_fuel",
            "smelt_furnace_fuel",
            "logistics_drill_fuel",
            "logistics_inserter_fuel",
            "belt_inserter_fuel",
            "belt_furnace_fuel",
            "belt_plates",
            "supply_fuel_drawn",
            "supply_fuel_log",
            "supply_container_recovered",
            "supply_container_smelted",
            "supply_note",
        ):
            if hasattr(self.namespace, name):
                delattr(self.namespace, name)

    def _run(self, code: str) -> bool:
        compact = re.sub(r"\s+", "", code)
        bindings = {
            name: PROTOTYPE_NAMES[proto]
            for name, proto in _BINDING.findall(compact)
            if proto in PROTOTYPE_NAMES
        }

        def resolve(token: str) -> str | None:
            if token.startswith("Prototype."):
                return PROTOTYPE_NAMES.get(token.split(".", 1)[1])
            return bindings.get(token)

        for item, x, y, quantity in _EXTRACT.findall(compact):
            position = (float(x), float(y))
            self.drawn.append((item, position))
            # Only what the draw asked for. A container emptied wholesale
            # would leave the stage after this one with nothing to burn for
            # reasons of the simulator rather than of the world.
            self.world.stock[position] = max(
                0,
                self.world.stock.get(position, 0) - int(quantity),
            )

        last: dict[str, Any] | None = None
        ok = True
        for token, x, y, inexact in _PLACE.findall(compact):
            name = resolve(token)
            if name is None:
                continue
            target = _snapped(name, (float(x), float(y)))
            if inexact:
                target = self.world.free_near(name, target)
            if self.world.add(name, target):
                self.placed.append((name, target))
                last = self.world.entities[-1]
            else:
                self.refused.append((name, target))
                ok = False
                break
        if ok:
            for token, reference, direction in _PLACE_NEXT.findall(compact):
                name = resolve(token)
                if name is None or last is None:
                    continue
                target = _next_to(name, last, direction)
                if self.world.add(name, target):
                    self.placed.append((name, target))
                    last = self.world.entities[-1]
                else:
                    self.refused.append((name, target))
                    ok = False
                    break
        if not ok:
            return False

        for name, position in self.placed:
            if name in (CHEST, IRON_CHEST):
                self.world.contents.setdefault(position, {})["iron-ore"] = CELL_ORE_FLOW
        self.namespace.supply_fuel_drawn = float(len(self.drawn))
        self.namespace.supply_fuel_log = [(x, y, 1) for _, (x, y) in self.drawn]
        self.namespace.supply_container_recovered = float("pickup_entity" in code)
        self.namespace.supply_container_smelted = float("craft_item" in code)
        self.namespace.supply_note = ""
        self.namespace.smelt_plates = PROBE_PLATE_FLOW
        self.namespace.smelt_drill_fuel = 20.0
        self.namespace.smelt_furnace_fuel = 20.0
        self.namespace.logistics_drill_fuel = 20.0
        self.namespace.logistics_inserter_fuel = 10.0
        self.namespace.belt_inserter_fuel = 10.0
        self.namespace.belt_furnace_fuel = 20.0
        self.namespace.belt_plates = BELT_PLATE_FLOW
        return True

    def execute(
        self,
        code: str,
        *,
        accept: Any,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> _FakeStep:
        self.scripts.append(code)
        checkpoint = self.world.snapshot()
        self._clear()
        self.world.ticks += STEP_TICKS
        # The inherited factory keeps producing whatever this step does.
        self.world.iron_produced += INHERITED_IRON_FLOW
        self.world.plate_produced += INHERITED_PLATE_FLOW
        step = _FakeStep()
        if not self._run(code):
            step.info = {"error_occurred": True, "result": "tiles already occupied"}
        else:
            self.world.plate_produced += PROBE_PLATE_FLOW
        step.accepted = bool(accept(step))
        if not step.accepted:
            self.world.restore(checkpoint)
        return step


class _FakeJournal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "metrics": {},
            "world": {
                "patch_bounds": {
                    "left_top": {"x": PATCH_BOUNDS[0], "y": PATCH_BOUNDS[1]},
                    "right_bottom": {"x": PATCH_BOUNDS[2], "y": PATCH_BOUNDS[3]},
                }
            },
            "online_learning": {},
            "evolution": {"champion": {}},
            "updated_at": None,
            "next_action": None,
        }
        self.run_id = "curriculum-test"
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.completed: list[tuple[int, str]] = []
        self.failed: list[tuple[int, str]] = []

    def set_stage(self, index: int, **_kwargs: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def event(self, event_type: str, message: str, **extra: Any) -> None:
        self.events.append((event_type, message, extra))

    def complete_stage(self, index: int, detail: str) -> None:
        self.completed.append((index, detail))

    def fail_stage(self, index: int, detail: str) -> None:
        self.failed.append((index, detail))


@pytest.fixture(autouse=True)
def _offline_and_unwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """No language model, no trained policy on disk, no file written."""
    monkeypatch.setattr(
        curriculum_runner,
        "synthesize_lesson",
        lambda **_kwargs: {
            "lesson": "stand-in",
            "next_hypothesis": "stand-in",
            "evidence_keys": ["output"],
        },
    )
    monkeypatch.setattr(
        curriculum_runner,
        "append_jsonl",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(curriculum_runner, "RUNS_DIR", tmp_path)


def _probe(
    world: _FakeWorld | None = None,
) -> tuple[bool, _FakeExecutor, _FakeJournal, _FakeEnv]:
    env = _FakeEnv(world or _FakeWorld())
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    accepted = curriculum_runner.stage_smelting_probe(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=24,
        region=PATCH_BOUNDS,
    )
    return accepted, executor, journal, env


def _logistics(
    world: _FakeWorld | None = None,
) -> tuple[dict[str, Any] | None, _FakeExecutor, _FakeJournal, _FakeEnv]:
    env = _FakeEnv(world or _FakeWorld())
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    result = curriculum_runner.stage_astar_logistics(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=20,
        turn_penalty=0.5,
        region=PATCH_BOUNDS,
    )
    return result, executor, journal, env


def _no_triggers(script: str) -> list[str]:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Name) and any(
            trigger.strip() in node.id.lower() for trigger in TRIGGERS
        ):
            offenders.append(node.id)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(trigger in node.value.lower() for trigger in TRIGGERS)
        ):
            offenders.append(node.value[:60])
    return offenders


# --------------------------------------------------------------------------
# Stage 3: the smelting probe.
# --------------------------------------------------------------------------


def test_the_probe_does_not_place_on_the_inherited_smelting_cell() -> None:
    """The exact failure generations 38 and 39 ended on, 3 completed stages in."""
    accepted, executor, _, _ = _probe()

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )
    assert accepted


def test_the_probe_reserves_the_tiles_its_furnace_needs() -> None:
    world = _FakeWorld()
    standing = blocked_tiles([dict(e) for e in world.entities], FOOTPRINTS)
    accepted, executor, journal, _ = _probe(world)

    placed = dict(executor.placed)
    assert accepted
    assert FURNACE in placed
    furnace_tiles = entity_tiles(
        _entity(FURNACE, placed[FURNACE][0], placed[FURNACE][1]),
        FOOTPRINTS,
    )
    assert not furnace_tiles & standing, (
        "a fornalha foi planejada sobre tiles que ja estavam ocupados"
    )
    assert journal.state["metrics"]["smelting_furnace_placement"]["outcome"] == "build"


def test_the_probe_moves_further_when_only_the_furnace_tiles_are_taken() -> None:
    """A drill fits where its furnace does not, and the step fails anyway.

    One container standing on the tiles the furnace needs is enough. The
    drill footprint two tiles west of the anchor is free, so a plan that
    looked at the drill alone would take it, and ``place_entity_next_to``
    would answer that an entity already exists one line further down: the
    same failure, one line later.
    """
    blocker = _entity(CHEST, 24.5, 74.5)
    world = _FakeWorld(entities=(*INHERITED_WORLD, blocker))
    standing = blocked_tiles([dict(e) for e in world.entities], FOOTPRINTS)
    accepted, executor, _, _ = _probe(world)

    assert accepted, "a etapa parou num tile que so a fornalha ocupava"
    assert executor.refused == []
    placed = dict(executor.placed)
    furnace_tiles = entity_tiles(
        _entity(FURNACE, placed[FURNACE][0], placed[FURNACE][1]),
        FOOTPRINTS,
    )
    assert not furnace_tiles & standing


def test_the_probe_keeps_its_drill_on_the_measured_patch() -> None:
    _, executor, _, _ = _probe()
    drill = dict(executor.placed)[DRILL]
    tiles = entity_tiles(_entity(DRILL, drill[0], drill[1]), FOOTPRINTS)

    left, top, right, bottom = PATCH_BOUNDS
    assert all(
        tile.x >= left and tile.x + 1 <= right
        and tile.y >= top and tile.y + 1 <= bottom
        for tile in tiles
    ), f"o furo foi deslocado para fora da mancha de minerio: {drill}"


def test_the_probe_draws_the_coal_the_heir_does_not_carry() -> None:
    accepted, executor, journal, _ = _probe()
    script = executor.scripts[0]

    assert "insert_item(Prototype.Coal, smelt_drill, quantity=20)" not in script
    assert "extract_item(Prototype.Coal,Position(" in script
    assert accepted
    assert journal.state["metrics"]["smelting_supply"]["status"] == (
        curriculum_runner.SUPPLY_PLANNED
    )


def test_the_probe_measures_its_own_furnace_and_not_the_world_counter() -> None:
    _, _, journal, _ = _probe()
    metrics = journal.state["metrics"]

    assert metrics["iron_plate_output"] == PROBE_PLATE_FLOW, (
        "a etapa creditou a si o fluxo das fornalhas herdadas"
    )
    assert metrics["smelting_plate_basis"] == curriculum_runner.SMELTING_OUTPUT_BASIS
    assert metrics["smelting_cell_origin"] == "built"


def test_a_world_holding_no_fuel_refuses_the_probe_by_name() -> None:
    world = _FakeWorld(stock={})
    accepted, executor, journal, _ = _probe(world)

    assert not accepted
    assert executor.scripts == [], "a etapa rodou um passo que nao tinha combustivel"
    assert journal.failed, "a recusa nao foi registrada como falha da etapa"
    refusals = journal.state["metrics"]["smelting_supply"]["refusals"]
    assert curriculum_runner.REFUSAL_NO_FUEL_IN_WORLD in refusals


def test_an_unread_world_still_lets_the_probe_run() -> None:
    env = _FakeEnv(_FakeWorld(), answers=False)
    executor = _FakeExecutor(env)
    journal = _FakeJournal()

    curriculum_runner.stage_smelting_probe(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=24,
        region=PATCH_BOUNDS,
    )

    assert len(executor.scripts) == 1, (
        "um mundo ilegivel recusou a etapa como se estivesse vazio"
    )
    assert journal.state["metrics"]["smelting_supply"] == {
        "status": curriculum_runner.SUPPLY_WORLD_UNREAD
    }


def test_the_probe_script_trips_no_fle_failure_trigger() -> None:
    _, executor, _, _ = _probe()

    assert _no_triggers(executor.scripts[0]) == []


# --------------------------------------------------------------------------
# Stage 4: the A* belt line.
# --------------------------------------------------------------------------


def test_the_logistics_cell_is_not_placed_on_the_inherited_drill() -> None:
    result, executor, _, _ = _logistics()

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )
    assert result is not None


def test_the_route_starts_from_where_the_drill_really_stands() -> None:
    result, executor, journal, _ = _logistics()
    assert result is not None

    drill = dict(executor.placed)[DRILL]
    assert result["drill_position"] == {"x": drill[0], "y": drill[1]}
    geometry = curriculum_runner.logistics_geometry(drill)
    assert result["chest_position"] == {
        "x": geometry.chest[0],
        "y": geometry.chest[1],
    }
    assert journal.state["metrics"]["logistics_placement"]["outcome"] == "build"


def test_the_terminal_of_the_route_is_planned_on_free_tiles() -> None:
    world = _FakeWorld()
    standing = blocked_tiles([dict(e) for e in world.entities], FOOTPRINTS)
    result, _, _, _ = _logistics(world)
    assert result is not None

    terminal = {
        (float(result["chest_position"]["x"]), float(result["chest_position"]["y"])),
        (
            float(result["inserter_position"]["x"]),
            float(result["inserter_position"]["y"]),
        ),
    }
    for position in terminal:
        tiles = entity_tiles(_entity(CHEST, position[0], position[1]), FOOTPRINTS)
        assert not tiles & standing, (
            f"o terminal foi planejado sobre tiles ocupados em {position}"
        )


def test_the_logistics_stage_gets_its_container_from_the_world() -> None:
    result, executor, journal, _ = _logistics()
    assert result is not None

    assert result["chest_name"] == CHEST
    assert "pickup_entity(Prototype.WoodenChest,Position(x=32.5,y=8.5)" in (
        executor.scripts[0]
    )
    assert "logistics_container_type = Prototype.WoodenChest" in executor.scripts[0]
    assert journal.state["metrics"]["logistics_container"] == CHEST


def test_with_no_spare_chest_the_logistics_stage_smelts_one() -> None:
    world = _FakeWorld(
        entities=tuple(
            entity
            for entity in INHERITED_WORLD
            if (entity["position"]["x"], entity["position"]["y"]) != ORPHAN
        )
    )
    result, executor, journal, _ = _logistics(world)
    assert result is not None

    script = executor.scripts[0]
    assert result["chest_name"] == IRON_CHEST
    assert "craft_item(Prototype.IronChest,quantity=1)" in script
    assert "logistics_container_type = Prototype.IronChest" in script
    smelt = journal.state["metrics"]["logistics_supply"]["smelt"]
    assert smelt["ore_to_smelt"] == curriculum_runner.CONTAINER_PLATES


def test_the_logistics_stage_draws_the_coal_the_heir_does_not_carry() -> None:
    _, executor, journal, _ = _logistics()
    script = executor.scripts[0]

    assert "quantity=20,\n)" not in script
    assert "extract_item(Prototype.Coal,Position(" in script
    assert journal.state["metrics"]["logistics_supply"]["status"] == (
        curriculum_runner.SUPPLY_PLANNED
    )


def test_a_world_with_no_container_at_all_refuses_the_logistics_stage() -> None:
    world = _FakeWorld(
        entities=tuple(
            entity
            for entity in INHERITED_WORLD
            if (entity["position"]["x"], entity["position"]["y"]) != ORPHAN
        ),
        carried={**INHERITED_INVENTORY, "iron-ore": 0, FURNACE: 0},
    )
    result, executor, journal, _ = _logistics(world)

    assert result is None
    assert executor.scripts == []
    refusals = journal.state["metrics"]["logistics_supply"]["refusals"]
    assert curriculum_runner.REFUSAL_NO_SPARE_CONTAINER in refusals
    assert journal.failed


def test_the_logistics_script_trips_no_fle_failure_trigger() -> None:
    _, executor, _, _ = _logistics()

    assert _no_triggers(executor.scripts[0]) == []


# --------------------------------------------------------------------------
# Stage 5: belt-fed smelting, on the terminal stage 4 really built.
# --------------------------------------------------------------------------


def _belt_smelting(
    world: _FakeWorld | None = None,
) -> tuple[bool, _FakeExecutor, _FakeJournal]:
    """Stages 3, 4 and 5 in the order the curriculum runs them.

    The sequence is the point: stage 5 extends the terminal stage 4 built,
    and stage 4 has to route around the cell stage 3 just placed. Running
    stage 5 alone would measure a world no generation ever meets.
    """
    world = world or _FakeWorld()
    env = _FakeEnv(world)
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    assert curriculum_runner.stage_smelting_probe(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=24,
        region=PATCH_BOUNDS,
    )
    logistics = curriculum_runner.stage_astar_logistics(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=20,
        turn_penalty=0.5,
        region=PATCH_BOUNDS,
    )
    assert logistics is not None
    accepted = curriculum_runner.stage_belt_smelting(
        executor,
        env,
        journal,
        logistics=logistics,
        settle_seconds=20,
    )
    return accepted, executor, journal


def _belt_script(executor: _FakeExecutor) -> str:
    """The script stage 5 ran, or the failure that it never ran one."""
    assert len(executor.scripts) == 3, (
        "a etapa 5 nao executou passo nenhum: "
        f"{len(executor.scripts)} scripts no total"
    )
    return executor.scripts[-1]


def test_belt_smelting_opens_the_container_the_terminal_really_is() -> None:
    world = _FakeWorld(
        entities=tuple(
            entity
            for entity in INHERITED_WORLD
            if (entity["position"]["x"], entity["position"]["y"]) != ORPHAN
        )
    )
    accepted, executor, _ = _belt_smelting(world)

    assert "belt_container_type = Prototype.IronChest" in _belt_script(executor)
    assert accepted


def test_belt_smelting_draws_the_coal_the_heir_does_not_carry() -> None:
    accepted, executor, journal = _belt_smelting()
    script = _belt_script(executor)

    assert "quantity=10,\n)" not in script
    assert "extract_item(Prototype.Coal,Position(" in script
    assert accepted, "uma carga curta recusou uma janela que podia ser medida"
    # Three stages have drawn from the same four containers by now, so what
    # is left is short of the full charge. Short is not empty: the stage
    # draws what is there, inserts what it drew and records the shortfall.
    supply = journal.state["metrics"]["belt_smelting_supply"]
    assert supply["refusals"] == [resupply.REFUSAL_FUEL_SHORT]
    assert supply["fuel_planned"] > 0


def test_belt_smelting_places_nothing_on_the_inherited_factory() -> None:
    _, executor, _ = _belt_smelting()
    _belt_script(executor)

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )


def test_the_belt_smelting_script_trips_no_fle_failure_trigger() -> None:
    _, executor, _ = _belt_smelting()

    assert _no_triggers(_belt_script(executor)) == []


def test_belt_smelting_counts_its_own_furnace_and_not_the_world() -> None:
    """The plate counter carries the furnace stage 3 left burning.

    Stage 3 of this same generation leaves a fuelled probe furnace running,
    and the inherited factory smelts throughout. Reading the world counter
    credited stage 5 with both and then compared the result against stage 3 --
    the same plates on both sides of the comparison. The furnace this stage
    placed is read before and after its own window instead, which is what
    stages 3, 6, 7 and 8 already do.
    """
    accepted, _, journal = _belt_smelting()
    metrics = journal.state["metrics"]

    assert accepted
    assert metrics["belt_smelting_plate_output"] == BELT_PLATE_FLOW
    assert metrics["belt_smelting_plate_basis"] == "belt_furnace_contents"
    # The world flow over the same window is recorded, and it is a different
    # number: it gates nothing and is not this cell's output.
    assert metrics["belt_smelting_plate_world_flow"] == (
        INHERITED_PLATE_FLOW + PROBE_PLATE_FLOW
    )


def test_belt_smelting_refuses_a_window_its_own_furnace_never_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A furnace that was never read did not smelt zero plates.

    The world counter keeps moving whatever the step did, so accepting on it
    accepted windows in which this cell produced nothing measurable.
    """
    world = _FakeWorld()
    env = _FakeEnv(world)
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    assert curriculum_runner.stage_smelting_probe(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=24,
        region=PATCH_BOUNDS,
    )
    logistics = curriculum_runner.stage_astar_logistics(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=20,
        turn_penalty=0.5,
        region=PATCH_BOUNDS,
    )
    assert logistics is not None
    original = _FakeExecutor._run

    def _run_without_reading_the_furnace(self: _FakeExecutor, code: str) -> bool:
        done = original(self, code)
        if hasattr(self.namespace, "belt_plates"):
            delattr(self.namespace, "belt_plates")
        return done

    monkeypatch.setattr(_FakeExecutor, "_run", _run_without_reading_the_furnace)
    accepted = curriculum_runner.stage_belt_smelting(
        executor,
        env,
        journal,
        logistics=logistics,
        settle_seconds=20,
    )

    assert not accepted
    assert journal.state["metrics"]["belt_smelting_plate_world_flow"] > 0


def test_belt_smelting_without_a_direct_feed_baseline_reports_no_comparison() -> None:
    """A baseline no stage measured is not a baseline of zero.

    ``metrics.get("direct_smelting_duration_s", 0.0)`` handed
    ``normalized_rate_ratio`` a zero-second window, and ``rate_per_second``
    raises ``duration_s must be positive`` from inside a stage whose own
    window was measured perfectly well. The stage now reports the absence as
    no comparison and keeps the window it did measure.
    """
    world = _FakeWorld()
    env = _FakeEnv(world)
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    assert curriculum_runner.stage_smelting_probe(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=24,
        region=PATCH_BOUNDS,
    )
    logistics = curriculum_runner.stage_astar_logistics(
        executor,
        env,
        journal,
        center=CENTRE,
        settle_seconds=20,
        turn_penalty=0.5,
        region=PATCH_BOUNDS,
    )
    assert logistics is not None
    for key in (
        "iron_plate_output",
        "direct_smelting_duration_s",
        "direct_smelting_plate_rate_per_s",
    ):
        journal.state["metrics"].pop(key, None)

    accepted = curriculum_runner.stage_belt_smelting(
        executor,
        env,
        journal,
        logistics=logistics,
        settle_seconds=20,
    )

    assert accepted
    metrics = journal.state["metrics"]
    assert metrics["belt_smelting_vs_direct_rate_ratio"] is None
    assert metrics["belt_smelting_plate_rate_per_s"] > 0
