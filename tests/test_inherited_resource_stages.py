"""Coal, copper and copper smelting also run inside an inherited factory.

Generation 41 was the first to reach stage 6, and it died there. The stage
aims its drill at the exact centre of the coal patch, which is deterministic
on a fixed map, and the ancestor's own coal cell is standing on it: drill
(27, 9), fed from the chest at (27.5, 6.5), dropping into the chest at
(27.5, 10.5). Before that placement is even attempted the stage asks for a
wooden chest to quarantine its bootstrap coal in, and the inheritance ledger
of the promoted checkpoint carries none -- the rejected step recorded
``bootstrap_total`` 0.0 and every later reading unmeasured.

Stage 7 has the same collision one patch over: the copper drill at (-58, 83)
with its chest at (-57.5, 84.5) stands on the exact centre of the copper
patch.

The world below is that promoted checkpoint as it was decoded on 2026-09-23,
restricted to the coal and copper patches plus the containers the stages
around them reach. Two things are kept apart throughout: what a stage built
is the container or furnace it placed, and what the inherited factory
produces is the world counter, which keeps moving whatever this generation
does.
"""

from __future__ import annotations

import ast
import math
import re
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_tiles

#: Patch geometry as the live engine measured it, from the generation 37
#: research state: runs/research/curriculum-20260923T035414Z.json.
COAL_CENTRE = (27.0, 8.5)
COAL_BOUNDS = (15.5, -3.5, 38.5, 20.5)
COPPER_CENTRE = (-58.5, 83.0)
COPPER_BOUNDS = (-70.5, 70.5, -46.5, 95.5)

DRILL = "burner-mining-drill"
CHEST = "wooden-chest"
IRON_CHEST = "iron-chest"
FURNACE = "stone-furnace"
INSERTER = "burner-inserter"

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
}

PROTOTYPE_NAMES: dict[str, str] = {
    "BurnerMiningDrill": DRILL,
    "StoneFurnace": FURNACE,
    "WoodenChest": CHEST,
    "IronChest": IRON_CHEST,
    "BurnerInserter": INSERTER,
}

_BINDING = re.compile(
    r"(\w+)=Prototype\.(" + "|".join(sorted(PROTOTYPE_NAMES, reverse=True)) + r")"
)
#: Every placement these stages make is bound to a name, and the name is
#: what the next line places against: ``coal_chest`` goes beside
#: ``coal_drill`` and not beside whatever was placed most recently. A
#: simulator that used "most recently" would put the cell's chest beside the
#: furnace the supply prelude placed, and would then pass a script the engine
#: refuses.
_PLACE = re.compile(
    r"(\w+)=place_entity\(([A-Za-z_.]+),position=Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)"
    r"(,exact=False)?"
)
_PLACE_NEXT = re.compile(
    r"(\w+)=place_entity_next_to\(([A-Za-z_.]+),(\w+)\.position,"
    r"direction=Direction\.(\w+)"
)
_DRAW = re.compile(
    r"extract_item\(Prototype\.(\w+),Position\(x=(-?[\d.]+),y=(-?[\d.]+)\),"
    r"quantity=min\(supply_short,(\d+)\)"
)
_ADOPT = re.compile(
    r"get_entity\(Prototype\.(\w+),Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)\)"
)
_FIND_ENTITY = re.compile(r"find_entity\('([a-z-]+)',\{x=(-?[\d.]+),y=(-?[\d.]+)\}\)")
_ITEM_COUNT = re.compile(r"get_item_count\((?:'([a-z-]+)')?\)")
_CARRIED = re.compile(r"get_main_inventory\(\)")


def _compact(code: str) -> str:
    """The script with its layout removed but its statements still apart.

    Collapsing every run of whitespace would run one statement into the next:
    ``Prototype.IronChest`` followed by ``coal_drill=place_entity(`` reads
    back as a binding named ``IronChestcoal_drill``, and the reference the
    cell's chest is placed against is then a name nothing was bound to. Only
    the continuations of a call are joined -- a line ending in ``(`` or ``,``,
    and a closing parenthesis on its own line.
    """
    text = re.sub(r"[ \t]+", "", code)
    text = re.sub(r"\(\n", "(", text)
    text = re.sub(r",\n", ",", text)
    return re.sub(r"\n\)", ")", text)


def _entity(name: str, x: float, y: float, direction: int = 0) -> dict[str, Any]:
    return {"name": name, "position": {"x": x, "y": y}, "direction": direction}


#: The promoted checkpoint around the two patches these stages reach for.
INHERITED_WORLD: tuple[dict[str, Any], ...] = (
    _entity(CHEST, 27.5, 6.5),
    _entity(INSERTER, 27.5, 7.5),
    _entity(DRILL, 27.0, 9.0, 8),
    _entity(CHEST, 27.5, 10.5),
    _entity(CHEST, -57.5, 80.5),
    _entity(INSERTER, -57.5, 81.5),
    _entity(DRILL, -58.0, 83.0, 8),
    _entity(CHEST, -57.5, 84.5),
    _entity(FURNACE, -52.0, 89.0),
)

#: Coal as those containers were measured to hold it. (27.5, 10.5) is the
#: ancestor's coal output chest and the deepest fuel source on the map.
COAL_STOCK: dict[tuple[float, float], int] = {
    (27.5, 6.5): 36,
    (27.5, 10.5): 313,
}

#: The inheritance ledger of the promoted checkpoint, in the items these
#: stages reach for, after stages 0 to 5 have spent their share: no chest,
#: no coal, and the 16 iron ore that stage 4's smelted chest left behind.
INHERITED_INVENTORY: dict[str, int] = {
    "coal": 0,
    CHEST: 0,
    IRON_CHEST: 0,
    "iron-ore": 16,
    "iron-plate": 0,
    FURNACE: 7,
}


def _snap(value: float, size: int) -> float:
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
    """Where ``place_entity_next_to`` lands, by the engine's own arithmetic."""
    ref_w, ref_h = FOOTPRINTS.get(reference["name"], (1, 1))
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


class _Patch:
    """What ``get_resource_patch`` answers, as the stages read it."""

    def __init__(self, bounds: tuple[float, float, float, float], size: int) -> None:
        left, top, right, bottom = bounds
        self.size = size
        self.bounding_box = SimpleNamespace(
            left_top=SimpleNamespace(x=left, y=top),
            right_bottom=SimpleNamespace(x=right, y=bottom),
        )


class _World:
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
        self.counters: dict[str, float] = {
            "coal": 0.0,
            "copper-ore": 0.0,
            "copper-plate": 0.0,
            "iron-ore": 0.0,
            "iron-plate": 0.0,
        }
        self.ticks = 1000

    def snapshot(self) -> tuple[Any, ...]:
        return (
            [dict(e) for e in self.entities],
            dict(self.stock),
            dict(self.carried),
            dict(self.counters),
            self.ticks,
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        entities, stock, carried, counters, ticks = snapshot
        self.entities = [dict(e) for e in entities]
        self.stock = dict(stock)
        self.carried = dict(carried)
        self.counters = dict(counters)
        self.ticks = ticks

    def taken(self) -> set[Any]:
        return blocked_tiles(self.entities, FOOTPRINTS)

    def add(self, name: str, position: tuple[float, float]) -> bool:
        placed = _entity(name, position[0], position[1])
        if entity_tiles(placed, FOOTPRINTS) & self.taken():
            return False
        self.entities.append(placed)
        return True

    def free_near(self, name: str, position: tuple[float, float]) -> tuple[float, float]:
        for radius in range(8):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    candidate = _snapped(name, (position[0] + dx, position[1] + dy))
                    probe = _entity(name, candidate[0], candidate[1])
                    if not entity_tiles(probe, FOOTPRINTS) & self.taken():
                        return candidate
        return position

    def item_count(self, x: float, y: float, item: str | None) -> int:
        if item in (None, "coal"):
            return int(self.stock.get((x, y), 0))
        return 0


class _Rcon:
    def __init__(self, world: _World, *, answers: bool = True) -> None:
        self.world = world
        self.answers = answers

    def send_command(self, command: str) -> str:
        if "teleport" in command:
            return "0,0"
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


class _Namespace:
    def __init__(self, world: _World) -> None:
        self.world = world
        self.player_location = None

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self.world.entities]

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": dict(self.world.counters)}


class _Instance:
    def __init__(self, world: _World, *, answers: bool = True) -> None:
        self.world = world
        self.namespace = _Namespace(world)
        self.namespaces = [self.namespace]
        self.rcon_client = _Rcon(world, answers=answers)

    def get_elapsed_ticks(self) -> int:
        return self.world.ticks


class _Env:
    def __init__(self, world: _World, *, answers: bool = True) -> None:
        self.world = world
        self.unwrapped = SimpleNamespace(instance=_Instance(world, answers=answers))


class _Step:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False}
        self.candidate_game_state: Any = object()
        self.reward = 11.0
        self.accepted = False


#: What the inherited coal drill and copper cell add to the world counters
#: while a step runs, whatever the step itself does.
INHERITED_COAL_FLOW = 88.0
INHERITED_COPPER_ORE_FLOW = 64.0
INHERITED_COPPER_PLATE_FLOW = 30.0

#: What the cell this generation builds actually puts in its own container.
CELL_SEED_COAL = 9.0
CELL_COAL_GROWTH = 6.0
CELL_COPPER_ORE = 23.0
CELL_COPPER_PLATE = 12.0

#: Every name a stage of this file reads back off the namespace.
_SCRIPT_READINGS: dict[str, float] = {
    "bootstrap_total": 0.0,
    "bootstrap_quarantine": 0.0,
    "coal_seed": 1.0,
    "seed_phase_count": CELL_SEED_COAL,
    "transfer_1": 3.0,
    "internal_stock_before": 7.0,
    "internal_stock_after": 13.0,
    "endogenous_growth": CELL_COAL_GROWTH,
    "endogenous_stockpile": 11.0,
    "operational_refuel": 2.0,
    "copper_mining_fuel": 31.0,
    "copper_drill_fuel": 30.0,
    "copper_cell_output": CELL_COPPER_ORE,
    "copper_ore_transfer": 20.0,
    "copper_smelting_fuel": 26.0,
    "copper_furnace_inventory": CELL_COPPER_PLATE,
}


class _Executor:
    """Runs a stage script against the fake world, as the engine would.

    Reproduces what these stages depend on: a placement on occupied tiles
    fails the step, the world counters and the game clock advance while the
    step runs even when the placement failed, a rejected step is rolled back,
    and the variables the script assigned are readable off the namespace
    exactly when the script got that far.
    """

    def __init__(self, env: _Env, *, readings: dict[str, float] | None = None) -> None:
        self.world = env.world
        self.namespace = env.unwrapped.instance.namespace
        self.readings = dict(_SCRIPT_READINGS if readings is None else readings)
        self.scripts: list[str] = []
        self.placed: list[tuple[str, tuple[float, float]]] = []
        self.refused: list[tuple[str, tuple[float, float]]] = []
        self.drawn: list[tuple[str, tuple[float, float], int]] = []
        self.adopted: list[tuple[str, tuple[float, float]]] = []

    def _clear(self) -> None:
        for name in (
            *_SCRIPT_READINGS,
            "supply_fuel_drawn",
            "supply_fuel_log",
            "supply_container_recovered",
            "supply_container_smelted",
            "supply_note",
            "bootstrap_note",
            "bootstrap_vault",
            "coal_chest",
            "copper_chest",
        ):
            if hasattr(self.namespace, name):
                delattr(self.namespace, name)

    def _run(self, code: str) -> bool:
        compact = _compact(code)
        if "get_resource_patch(Resource.Coal" in compact:
            self.namespace.coal_patch = _Patch(COAL_BOUNDS, 6000000)
            return True
        if "get_resource_patch(Resource.CopperOre" in compact:
            self.namespace.copper_patch = _Patch(COPPER_BOUNDS, 6500000)
            return True

        bindings = {
            name: PROTOTYPE_NAMES[proto]
            for name, proto in _BINDING.findall(compact)
            if proto in PROTOTYPE_NAMES
        }

        def resolve(token: str) -> str | None:
            if token.startswith("Prototype."):
                return PROTOTYPE_NAMES.get(token.split(".", 1)[1])
            return bindings.get(token)

        for proto, x, y in _ADOPT.findall(compact):
            self.adopted.append(
                (PROTOTYPE_NAMES.get(proto, proto), (float(x), float(y)))
            )
        for item, x, y, quantity in _DRAW.findall(compact):
            position = (float(x), float(y))
            self.drawn.append((item, position, int(quantity)))
            self.world.stock[position] = max(
                0,
                self.world.stock.get(position, 0) - int(quantity),
            )

        bound: dict[str, dict[str, Any]] = {}
        ok = True
        steps = sorted(
            [("place", match) for match in _PLACE.finditer(compact)]
            + [("next_to", match) for match in _PLACE_NEXT.finditer(compact)],
            key=lambda step: step[1].start(),
        )
        for kind, match in steps:
            if kind == "place":
                variable, token, x, y, inexact = match.groups()
                name = resolve(token)
                if name is None:
                    continue
                target = _snapped(name, (float(x), float(y)))
                if inexact:
                    target = self.world.free_near(name, target)
            else:
                variable, token, reference, direction = match.groups()
                name = resolve(token)
                anchor = bound.get(reference)
                if name is None or anchor is None:
                    continue
                target = _next_to(name, anchor, direction)
            if self.world.add(name, target):
                self.placed.append((name, target))
                bound[variable] = self.world.entities[-1]
            else:
                self.refused.append((name, target))
                ok = False
                break
        if not ok:
            return False

        for key, value in self.readings.items():
            setattr(self.namespace, key, value)
        self.namespace.supply_fuel_drawn = float(
            sum(quantity for _, _, quantity in self.drawn)
        )
        self.namespace.supply_fuel_log = [
            (x, y, quantity) for _, (x, y), quantity in self.drawn
        ]
        self.namespace.supply_container_recovered = float("pickup_entity" in code)
        self.namespace.supply_container_smelted = float("craft_item" in code)
        self.namespace.supply_note = ""
        self.namespace.bootstrap_note = ""
        self.namespace.bootstrap_vault = (
            None if not self.adopted else SimpleNamespace(position=SimpleNamespace())
        )
        # The cell containers this generation placed, as the next stage
        # reads them back off the namespace.
        for name in ("coal_chest", "copper_chest"):
            entity = bound.get(name)
            if entity is not None:
                setattr(
                    self.namespace,
                    name,
                    SimpleNamespace(
                        position=SimpleNamespace(
                            x=entity["position"]["x"],
                            y=entity["position"]["y"],
                        )
                    ),
                )
        return True

    def execute(
        self,
        code: str,
        *,
        accept: Any,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> _Step:
        self.scripts.append(code)
        checkpoint = self.world.snapshot()
        self._clear()
        self.world.ticks += STEP_TICKS
        # The inherited factory keeps producing whatever this step does.
        self.world.counters["coal"] += INHERITED_COAL_FLOW
        self.world.counters["copper-ore"] += INHERITED_COPPER_ORE_FLOW
        self.world.counters["copper-plate"] += INHERITED_COPPER_PLATE_FLOW
        step = _Step()
        if not self._run(code):
            step.info = {"error_occurred": True, "result": "tiles already occupied"}
        step.accepted = bool(accept(step))
        if not step.accepted:
            self.world.restore(checkpoint)
        return step


class _Journal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "metrics": {},
            "world": {},
            "resource_accounting": {"exogenous_inputs": {"coal": {}}},
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


def _coal(
    world: _World | None = None,
) -> tuple[bool, tuple[float, float] | None, _Executor, _Journal, _Env]:
    env = _Env(world or _World())
    executor = _Executor(env)
    journal = _Journal()
    accepted, centre = curriculum_runner.stage_coal_mining(
        executor,
        env,
        journal,
        settle_seconds=20,
        safety_stock=2,
        producer_refuel=3,
    )
    return accepted, centre, executor, journal, env


def _copper(
    world: _World | None = None,
) -> tuple[bool, tuple[float, float] | None, _Executor, _Journal, _Env]:
    env = _Env(world or _World())
    executor = _Executor(env)
    journal = _Journal()
    accepted, centre = curriculum_runner.stage_copper_mining(
        executor,
        env,
        journal,
        settle_seconds=20,
        safety_stock=2,
        fuel_budget=24,
    )
    return accepted, centre, executor, journal, env


def _copper_smelting(
    world: _World | None = None,
) -> tuple[bool, _Executor, _Journal, _Env]:
    env = _Env(world or _World())
    executor = _Executor(env)
    journal = _Journal()
    accepted = curriculum_runner.stage_copper_smelting(
        executor,
        env,
        journal,
        center=COPPER_CENTRE,
        settle_seconds=20,
        safety_stock=2,
        fuel_budget=24,
        buffer_target=20,
    )
    return accepted, executor, journal, env


def _placed_tiles(executor: _Executor, name: str) -> set[Any]:
    """Tiles one placed entity occupies, by the footprints of this file."""
    for placed_name, position in executor.placed:
        if placed_name == name:
            return entity_tiles(
                _entity(placed_name, position[0], position[1]),
                FOOTPRINTS,
            )
    return set()


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


def _inside(position: tuple[float, float], bounds: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = bounds
    return left <= position[0] <= right and top <= position[1] <= bottom


# --------------------------------------------------------------------------
# Stage 6: coal self-sufficiency.
# --------------------------------------------------------------------------


def test_the_coal_stage_does_not_build_on_the_inherited_coal_cell() -> None:
    """The exact failure generation 41 ended on, 6 completed stages in."""
    accepted, _, executor, _, _ = _coal()

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )
    assert accepted


def test_the_coal_cell_stays_on_the_measured_coal_patch() -> None:
    """A drill shifted off the ore places and mines nothing."""
    _, _, executor, journal, _ = _coal()

    drills = [position for name, position in executor.placed if name == DRILL]
    assert len(drills) == 1, executor.placed
    assert _inside(drills[0], COAL_BOUNDS), drills[0]
    assert journal.state["metrics"]["coal_placement"]["outcome"] == "build"


def test_the_coal_stage_takes_its_container_from_the_world() -> None:
    """No chest is placed out of an inventory that carries none."""
    _, _, executor, journal, _ = _coal()

    script = executor.scripts[-1]
    assert "Prototype.WoodenChest,\n    position=" not in script, script
    supply = journal.state["metrics"]["coal_supply"]
    assert supply["status"] == "planned", supply
    assert supply["container_needed"] is True
    assert supply["container_name"] in {CHEST, IRON_CHEST}, supply
    assert journal.state["metrics"]["coal_container"] == supply["container_name"]


def test_the_smelted_container_is_not_made_on_the_cell_tiles() -> None:
    """A furnace aimed at the cell lands on whatever is free nearest to it.

    The supply plan is drawn against a world the cell is not standing in
    yet, so a furnace anchored on the cell resolves to the cell's own tiles,
    and ``exact=False`` then puts it on the nearest free ones -- the tile the
    chest needs among them, which fails the whole step one line later.
    """
    _, _, executor, journal, _ = _coal()

    smelt = journal.state["metrics"]["coal_supply"]["smelt"]
    assert smelt is not None, journal.state["metrics"]["coal_supply"]
    furnace = entity_tiles(
        _entity(FURNACE, smelt["position"]["x"], smelt["position"]["y"]),
        FOOTPRINTS,
    )
    cell = _placed_tiles(executor, DRILL) | _placed_tiles(executor, IRON_CHEST)
    assert cell, executor.placed
    assert not furnace & cell, (smelt["position"], sorted(cell))


def test_the_coal_seed_is_drawn_from_the_standing_world() -> None:
    """An heir carrying no coal has to find the one coal it seeds with."""
    _, _, executor, journal, _ = _coal()

    assert executor.drawn, "nenhum carvao foi sacado do mundo"
    positions = {position for _, position, _ in executor.drawn}
    assert positions <= set(COAL_STOCK), positions
    supply = journal.state["metrics"]["coal_supply"]
    assert supply["fuel_planned"] >= 1, supply


def test_the_coal_stage_quarantines_into_a_container_it_did_not_place() -> None:
    """Bootstrap coal is parked in a chest the world already held."""
    world = _World(carried={**INHERITED_INVENTORY, "coal": 9})
    _, _, executor, journal, _ = _coal(world)

    assert executor.adopted, "nenhum conteiner do mundo foi adotado para quarentena"
    name, position = executor.adopted[0]
    assert (name, position) in {
        (entity["name"], (entity["position"]["x"], entity["position"]["y"]))
        for entity in INHERITED_WORLD
    }, (name, position)
    target = journal.state["metrics"]["coal_quarantine_target"]
    assert target is not None and target["name"] == name
    chests = [
        placed for placed in executor.placed if placed[0] in {CHEST, IRON_CHEST}
    ]
    assert len(chests) == 1, f"a quarentena colocou um bau: {chests}"


def test_the_quarantine_keeps_the_fuel_the_plan_still_has_to_spend() -> None:
    """Smelting the cell's container burns a charge of coal too.

    A quarantine sized to the seed alone would leave the smelt's furnace and
    the drill bidding for the same single coal, and the drill would stand
    unfuelled through a window the stage had already paid for.
    """
    world = _World(carried={**INHERITED_INVENTORY, "coal": 9})
    _, _, executor, journal, _ = _coal(world)

    supply = journal.state["metrics"]["coal_supply"]
    keep = int(supply["fuel_needed"])
    assert keep > 1, supply
    assert journal.state["metrics"]["coal_quarantine_keeps"] == keep
    compact = re.sub(r"[ \t]+", "", executor.scripts[-1])
    assert f"bootstrap_surplus=max(0,bootstrap_total-{keep})" in compact, compact


def test_the_coal_stage_refuses_when_it_cannot_quarantine() -> None:
    """A window run without quarantine proves nothing about endogenous fuel."""
    world = _World(
        entities=(_entity(DRILL, 27.0, 9.0, 8),),
        stock={},
        carried={**INHERITED_INVENTORY, "coal": 9},
    )
    accepted, _, executor, journal, _ = _coal(world)

    assert not accepted
    assert executor.scripts[-1].strip().startswith("coal ="), (
        "a etapa executou um script de construcao apos recusar"
    )
    refusals = [extra for kind, _, extra in journal.events if kind == "refusal"]
    assert refusals, journal.events
    assert any(
        extra.get("reason") == curriculum_runner.NO_QUARANTINE_IN_WORLD
        for extra in refusals
    ), refusals


def test_the_coal_output_is_the_cell_and_not_the_world_counter() -> None:
    """The inherited coal drill mines through this window too."""
    accepted, _, _, journal, _ = _coal()

    assert accepted
    metrics = journal.state["metrics"]
    assert metrics["coal_output"] == pytest.approx(CELL_SEED_COAL + CELL_COAL_GROWTH)
    assert metrics["coal_output_basis"] == curriculum_runner.COAL_OUTPUT_BASIS
    assert metrics["coal_world_flow"] == pytest.approx(INHERITED_COAL_FLOW)


def test_an_aborted_coal_window_is_unmeasured_and_not_zero() -> None:
    """A script that never reported is not a cell that produced nothing."""
    world = _World()
    env = _Env(world)
    executor = _Executor(env, readings={})
    journal = _Journal()
    accepted, _, = curriculum_runner.stage_coal_mining(
        executor,
        env,
        journal,
        settle_seconds=20,
        safety_stock=2,
        producer_refuel=3,
    )

    assert not accepted
    assert "coal_output" not in journal.state["metrics"]
    rejects = [extra for kind, _, extra in journal.events if kind == "reject"]
    assert rejects and rejects[0]["measurements"]["coal_output"] is None, rejects


def test_the_coal_script_carries_no_failure_trigger() -> None:
    """FLE fails a step on the substring "error" anywhere it printed."""
    world = _World(carried={**INHERITED_INVENTORY, "coal": 9})
    _, _, executor, _, _ = _coal(world)

    for script in executor.scripts:
        assert _no_triggers(script) == [], script


def test_an_unread_world_still_lets_the_coal_stage_build() -> None:
    """A transient RCON failure is not a reason to stop a generation."""
    world = _World(entities=())
    env = _Env(world, answers=False)
    executor = _Executor(env)
    journal = _Journal()
    accepted, _ = curriculum_runner.stage_coal_mining(
        executor,
        env,
        journal,
        settle_seconds=20,
        safety_stock=2,
        producer_refuel=3,
    )

    assert accepted
    assert journal.state["metrics"]["coal_supply"]["status"] == "world_not_surveyed"


# --------------------------------------------------------------------------
# Stage 7: copper expansion.
# --------------------------------------------------------------------------


def test_the_copper_stage_does_not_build_on_the_inherited_copper_cell() -> None:
    accepted, _, executor, _, _ = _copper()

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )
    assert accepted


def test_the_copper_cell_stays_on_the_measured_copper_patch() -> None:
    _, _, executor, journal, _ = _copper()

    drills = [position for name, position in executor.placed if name == DRILL]
    assert len(drills) == 1, executor.placed
    assert _inside(drills[0], COPPER_BOUNDS), drills[0]
    assert journal.state["metrics"]["copper_placement"]["outcome"] == "build"


def test_the_copper_stage_takes_its_container_from_the_world() -> None:
    _, _, executor, journal, _ = _copper()

    script = executor.scripts[-1]
    assert "Prototype.WoodenChest,\n    position=" not in script, script
    supply = journal.state["metrics"]["copper_supply"]
    assert supply["container_needed"] is True
    assert supply["container_name"] in {CHEST, IRON_CHEST}, supply
    assert journal.state["metrics"]["copper_container"] == supply["container_name"]


def test_the_copper_drill_burns_only_what_it_could_insert() -> None:
    """The supply prelude may spend coal between the draw and the insert."""
    script = _copper()[2].scripts[-1]

    assert "copper_drill_fuel=min(copper_mining_fuel,inspect_inventory()" in re.sub(
        r"[ \t]+", "", script
    ), script


def test_the_copper_output_is_the_cell_and_not_the_world_counter() -> None:
    accepted, _, _, journal, _ = _copper()

    assert accepted
    metrics = journal.state["metrics"]
    assert metrics["copper_ore_output"] == pytest.approx(CELL_COPPER_ORE)
    assert metrics["copper_ore_output_basis"] == (
        curriculum_runner.COPPER_ORE_OUTPUT_BASIS
    )
    assert metrics["copper_ore_world_flow"] == pytest.approx(
        INHERITED_COPPER_ORE_FLOW
    )


def test_an_aborted_copper_window_is_unmeasured_and_not_zero() -> None:
    world = _World()
    env = _Env(world)
    executor = _Executor(env, readings={})
    journal = _Journal()
    accepted, _ = curriculum_runner.stage_copper_mining(
        executor,
        env,
        journal,
        settle_seconds=20,
        safety_stock=2,
        fuel_budget=24,
    )

    assert not accepted
    assert "copper_ore_output" not in journal.state["metrics"]


def test_the_copper_script_carries_no_failure_trigger() -> None:
    for script in _copper()[2].scripts:
        assert _no_triggers(script) == [], script


# --------------------------------------------------------------------------
# Stage 8: copper smelting.
# --------------------------------------------------------------------------


def test_copper_plates_are_read_off_the_furnace_this_stage_placed() -> None:
    accepted, _, journal, _ = _copper_smelting()

    assert accepted
    metrics = journal.state["metrics"]
    assert metrics["copper_plate_output"] == pytest.approx(CELL_COPPER_PLATE)
    assert metrics["copper_plate_output_basis"] == (
        curriculum_runner.COPPER_PLATE_OUTPUT_BASIS
    )
    assert metrics["copper_plate_world_flow"] == pytest.approx(
        INHERITED_COPPER_PLATE_FLOW
    )


def test_an_aborted_copper_smelt_is_unmeasured_and_not_zero() -> None:
    world = _World()
    env = _Env(world)
    executor = _Executor(env, readings={})
    journal = _Journal()
    accepted = curriculum_runner.stage_copper_smelting(
        executor,
        env,
        journal,
        center=COPPER_CENTRE,
        settle_seconds=20,
        safety_stock=2,
        fuel_budget=24,
        buffer_target=20,
    )

    assert not accepted
    assert "copper_plate_output" not in journal.state["metrics"]
