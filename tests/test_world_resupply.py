"""A stage assembles its cell from the world, or says why it cannot.

Generation 38 inherited ``coal: 8`` and no container. Stage 0 spent the eight
on the drill it adopted, and all eight placement trials of stage 1 died one
line later on ``insert_item(Prototype.Coal, trial_drill, quantity=12)`` with
``No coal to insert from your inventory``. Nothing was broken: the script
assumed a starting kit that inheritance never promised, and the service
restarted into that assumption 110 times.

The world was not empty. Measured live over RCON on 2026-09-23 it held 74
entities, 13 wooden chests, four of them holding coal, and exactly one chest
that no material edge touched. What is asserted here is that a stage reaches
for that, that it reaches for nothing that is feeding a machine, that it says
so in words when the world holds neither, and that whatever it took is
written down. The last one is not decoration: two of this project's recent
commits exist because a generation was credited with its ancestor's factory.
"""

from __future__ import annotations

import ast
import re
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.planning import resupply
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_tiles

CENTRE = (27.0, 83.0)
DRILL = "burner-mining-drill"
CHEST = "wooden-chest"

#: Substrings FLE turns into a failed step, from the same source
#: tests/test_fle_triggers.py cites: fle/env/gym_env/environment.py:451.
TRIGGERS = ("error", "exception: ")

_POSITION = re.compile(r"Position\(x=(-?[\d.]+), y=(-?[\d.]+)\)")
_FIND_ENTITY = re.compile(r"find_entity\('([a-z-]+)',\{x=(-?[\d.]+),y=(-?[\d.]+)\}\)")
_ITEM_COUNT = re.compile(r"get_item_count\((?:'([a-z-]+)')?\)")
_CARRIED = re.compile(r"get_main_inventory\(\)")


def _entity(name: str, x: float, y: float, direction: int = 0) -> dict[str, Any]:
    return {"name": name, "position": {"x": x, "y": y}, "direction": direction}


#: The inherited factory, in the shape RCON reported it. The chest at
#: (32.5, 8.5) is the one measurement that matters most here: it was the only
#: one of the thirteen that no material edge touched.
INHERITED_WORLD: tuple[dict[str, Any], ...] = (
    _entity(CHEST, 27.5, 70.5),
    _entity("burner-inserter", 27.5, 71.5),
    _entity(DRILL, 27.0, 73.0, 8),
    _entity("stone-furnace", 27.0, 75.0),
    _entity(CHEST, 19.5, 80.5),
    _entity("burner-inserter", 19.5, 81.5),
    _entity(CHEST, 27.5, 80.5),
    _entity("burner-inserter", 27.5, 81.5),
    _entity(CHEST, 32.5, 80.5),
    _entity("burner-inserter", 32.5, 81.5),
    _entity(DRILL, 19.0, 83.0, 8),
    _entity(DRILL, 23.0, 83.0, 8),
    _entity(DRILL, 27.0, 83.0, 8),
    _entity(DRILL, 32.0, 83.0, 8),
    _entity("transport-belt", 19.5, 84.5, 4),
    _entity("transport-belt", 20.5, 84.5, 4),
    _entity(CHEST, 27.5, 84.5),
    _entity(CHEST, 32.5, 84.5),
    _entity("transport-belt", 24.5, 86.5, 4),
    _entity("burner-inserter", 25.5, 86.5, 12),
    _entity("burner-inserter", 27.5, 86.5, 12),
    _entity(CHEST, 26.5, 86.5),
    _entity("stone-furnace", 29.0, 87.0),
    _entity(CHEST, 32.5, 8.5),
)

#: Coal as the containers were measured to hold it. The two by the patch are
#: the near ones; the vault at (27.5, 10.5) is 73 tiles away and only reached
#: when the near ones cannot cover the charge.
COAL_STOCK: dict[tuple[float, float], int] = {
    (27.5, 80.5): 9,
    (27.5, 70.5): 34,
}

ORPHAN = (32.5, 8.5)

#: 136 game seconds at 60 ticks per second.
STEP_TICKS = 8160


# --------------------------------------------------------------------------
# The graph the wiring decision is made over.
# --------------------------------------------------------------------------


def _graph(entities: tuple[dict[str, Any], ...] = INHERITED_WORLD) -> dict[str, Any]:
    return build_factory_graph([dict(entity) for entity in entities])


def _roles_at(graph: dict[str, Any]) -> dict[tuple[float, float], resupply.ContainerRole]:
    return {role.position: role for role in resupply.container_roles(graph)}


def test_every_container_of_the_inherited_world_is_surveyed() -> None:
    roles = _roles_at(_graph())

    assert len(roles) == sum(entity["name"] == CHEST for entity in INHERITED_WORLD)


def test_a_container_a_chain_drops_into_is_not_recoverable() -> None:
    # The output chest of a mining cell: the drill mines into it, so it is
    # the end of a chain. (27.5, 80.5) used to stand here, which the graph
    # read as fed only while it had every inserter backwards; that chest is
    # the cell's feed chest and it supplies the drill rather than receiving
    # from it.
    roles = _roles_at(_graph())
    fed = roles[(27.5, 84.5)]

    assert fed.fed_by_chain, "a broca que enche este bau nao foi lida como aresta"
    assert not fed.unattached
    assert (27.5, 84.5) not in {
        role.position for role in resupply.unattached_containers(_graph())
    }


def test_a_container_a_machine_draws_from_is_not_recoverable() -> None:
    roles = _roles_at(_graph())
    supplying = roles[(26.5, 86.5)]

    assert supplying.supplies_chain, (
        "o inserter que retira deste bau nao foi lido como aresta"
    )
    assert not supplying.unattached
    assert (26.5, 86.5) not in {
        role.position for role in resupply.unattached_containers(_graph())
    }


def test_only_the_container_no_material_edge_touches_is_recoverable() -> None:
    spare = resupply.unattached_containers(_graph(), names={CHEST})

    assert [role.position for role in spare] == [ORPHAN]


def test_a_power_edge_does_not_make_a_container_part_of_a_chain() -> None:
    graph = {
        "nodes": [
            {"id": "u1", "name": CHEST, "category": "buffer", "x": 5.5, "y": 5.5},
        ],
        "edges": [
            {"source": "u2", "target": "u1", "relation": "power_supply"},
            {"source": "u1", "target": "u3", "relation": "fluid_link"},
        ],
    }

    assert [role.position for role in resupply.unattached_containers(graph)] == [
        (5.5, 5.5)
    ]


# --------------------------------------------------------------------------
# What the plan draws, and what it refuses.
# --------------------------------------------------------------------------


def _sources() -> tuple[resupply.FuelSource, ...]:
    return tuple(
        resupply.FuelSource(position=position, available=available)
        for position, available in COAL_STOCK.items()
    )


def test_only_the_shortfall_is_drawn() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=8,
        fuel_sources=_sources(),
    )

    assert plan.fuel_planned == 4
    assert not plan.refused


def test_a_stage_that_already_carries_the_kit_draws_nothing() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=20,
        fuel_sources=_sources(),
        container_needed=True,
        containers_carried=1,
        spare_containers=(
            resupply.ContainerSalvage(position=ORPHAN, name=CHEST),
        ),
    )

    assert plan.fuel_draws == ()
    assert plan.salvage is None
    assert not plan.refused


def test_a_draw_never_exceeds_what_the_container_was_measured_to_hold() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=0,
        fuel_sources=_sources(),
    )

    assert [(draw.position, draw.quantity) for draw in plan.fuel_draws] == [
        ((27.5, 80.5), 9),
        ((27.5, 70.5), 3),
    ]
    assert plan.fuel_planned == 12
    assert not plan.refused


def test_a_container_a_machine_waits_on_is_drawn_last() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=6,
        fuel_carried=0,
        fuel_sources=(
            # Nearer, but a machine is waiting on it.
            resupply.FuelSource(
                position=(27.5, 80.5),
                available=50,
                supplies_chain=True,
            ),
            resupply.FuelSource(position=(27.5, 70.5), available=50),
        ),
    )

    assert [draw.position for draw in plan.fuel_draws] == [(27.5, 70.5)]


def test_a_world_holding_no_fuel_refuses_by_name() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=0,
        fuel_sources=(),
    )

    assert plan.refusals == (resupply.REFUSAL_NO_FUEL_IN_WORLD,)
    assert plan.refused


def test_a_world_short_of_fuel_refuses_by_name() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=0,
        fuel_sources=(resupply.FuelSource(position=(27.5, 80.5), available=3),),
    )

    assert plan.refusals == (resupply.REFUSAL_FUEL_SHORT,)
    assert plan.fuel_planned == 3


def test_a_world_whose_containers_all_belong_to_a_chain_refuses_by_name() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=12,
        container_needed=True,
        containers_carried=0,
        spare_containers=(),
    )

    assert plan.refusals == (resupply.REFUSAL_NO_SPARE_CONTAINER,)
    assert plan.salvage is None


def test_exactly_one_container_is_salvaged_and_it_is_the_emptiest() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=0,
        container_needed=True,
        spare_containers=(
            resupply.ContainerSalvage(position=(27.5, 84.5), name=CHEST, holding=391),
            resupply.ContainerSalvage(position=ORPHAN, name=CHEST, holding=0),
        ),
    )

    assert plan.salvage is not None
    assert plan.salvage.position == ORPHAN
    assert plan.salvage.reason == resupply.REASON_NO_MATERIAL_EDGE
    assert plan.to_dict()["salvage"]["reason"] == resupply.REASON_NO_MATERIAL_EDGE


# --------------------------------------------------------------------------
# The second source: a container the heir smelts when none can be salvaged.
# --------------------------------------------------------------------------

#: What the heir of generation 37 carries and what one chest costs, measured
#: over RCON on 2026-09-23: `iron-chest` is enabled, costs 8 iron plates and
#: is crafted by hand; the inheritance ledger of the promoted checkpoint has
#: 24 iron ore and 7 stone furnaces in it, and no chest at all.
FURNACE_SPOT = (30.0, 79.0)


def _smelting(**overrides: Any) -> resupply.SmeltingOption:
    fields: dict[str, Any] = {
        "container_name": "iron-chest",
        "plates_needed": 8,
        "ore_carried": 24,
        "plates_carried": 0,
        "furnaces_carried": 7,
        "furnace_position": FURNACE_SPOT,
        "fuel_per_smelt": 1,
        "seconds": 26,
    }
    fields.update(overrides)
    return resupply.SmeltingOption(**fields)


def _smelted_plan(**overrides: Any) -> resupply.SupplyPlan:
    return resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=0,
        fuel_sources=_sources(),
        container_needed=True,
        containers_carried=0,
        spare_containers=(),
        smelting=_smelting(**overrides),
    )


def test_a_world_with_no_spare_container_smelts_one_from_the_carried_ore() -> None:
    """The world one promotion after stage 2 commits the only orphan chest."""
    plan = _smelted_plan()

    assert not plan.refused
    assert plan.salvage is None
    assert plan.smelt is not None
    assert plan.smelt.ore_to_smelt == 8
    assert plan.smelt.position == FURNACE_SPOT
    assert plan.smelt.reason == resupply.REASON_SMELTED_FROM_CARRIED_ORE
    assert plan.container_name == "iron-chest"


def test_the_salvage_is_the_first_choice_and_the_smelt_the_second() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=12,
        container_needed=True,
        containers_carried=0,
        spare_containers=(
            resupply.ContainerSalvage(position=ORPHAN, name=CHEST, holding=0),
        ),
        smelting=_smelting(),
    )

    assert plan.smelt is None, (
        "a etapa fundiu um bau tendo um de graca no mundo"
    )
    assert plan.salvage is not None
    assert plan.container_name == CHEST


def test_a_heir_with_no_ore_refuses_with_both_causes_named() -> None:
    plan = _smelted_plan(ore_carried=0)

    assert plan.smelt is None
    assert plan.refusals == (
        resupply.REFUSAL_NO_SPARE_CONTAINER,
        resupply.REFUSAL_CANNOT_SMELT_CONTAINER,
    )


def test_a_heir_with_no_furnace_refuses_instead_of_planning_a_smelt() -> None:
    plan = _smelted_plan(furnaces_carried=0)

    assert plan.smelt is None
    assert resupply.REFUSAL_CANNOT_SMELT_CONTAINER in plan.refusals


def test_a_world_with_nowhere_free_for_the_furnace_refuses_by_name() -> None:
    plan = _smelted_plan(furnace_position=None)

    assert plan.smelt is None
    assert resupply.REFUSAL_CANNOT_SMELT_CONTAINER in plan.refusals


def test_a_heir_that_already_carries_the_plates_smelts_no_ore() -> None:
    plan = _smelted_plan(plates_carried=8, ore_carried=0, furnaces_carried=0)

    assert plan.smelt is not None
    assert plan.smelt.ore_to_smelt == 0
    assert plan.smelt.fuel_to_insert == 0
    assert plan.fuel_needed == 12, (
        "a carga cresceu por uma fundicao que nao vai acontecer"
    )


def test_the_charge_of_the_smelt_is_drawn_with_the_rest() -> None:
    plan = _smelted_plan()

    assert plan.fuel_needed == 13
    assert plan.fuel_planned == 13, (
        "o combustivel da fornalha saiu da carga do furo em vez do mundo"
    )


def test_the_script_smelts_the_plates_and_crafts_the_container() -> None:
    script = _script(_smelted_plan())

    assert (
        "place_entity(Prototype.StoneFurnace,"
        f"position=Position(x={FURNACE_SPOT[0]},y={FURNACE_SPOT[1]}),exact=False)"
    ) in script
    assert "insert_item(Prototype.IronOre,supply_furnace,quantity=supply_smelt_ore)" in (
        script
    )
    assert "sleep(26)" in script
    assert "craft_item(Prototype.IronChest,quantity=1)" in script
    assert "supply_container_smelted=" in script


def test_the_script_smelts_nothing_when_the_plan_salvages() -> None:
    script = _script(_full_plan())

    assert "craft_item" not in script
    assert "supply_container_smelted=0" in script, (
        "o contador da fundicao nao foi declarado para um passo que nao funde"
    )


def test_the_smelting_script_is_valid_python_for_the_engine() -> None:
    ast.parse(_script(_smelted_plan()))


def test_the_smelting_script_trips_no_fle_failure_trigger() -> None:
    offenders: list[str] = []
    for node in ast.walk(ast.parse(_script(_smelted_plan()))):
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

    assert not offenders, (
        f"{offenders} contem uma palavra que o FLE usa para marcar a acao "
        "como falha (environment.py:451)"
    )


# --------------------------------------------------------------------------
# The script the stage runs in the game.
# --------------------------------------------------------------------------


def _script(plan: resupply.SupplyPlan | None, needed: int = 12) -> str:
    return curriculum_runner.mining_cell_supply_script(plan, fuel_needed=needed)


def _full_plan() -> resupply.SupplyPlan:
    return resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=0,
        fuel_sources=_sources(),
        container_needed=True,
        containers_carried=0,
        spare_containers=(
            resupply.ContainerSalvage(position=ORPHAN, name=CHEST, holding=0),
        ),
    )


def test_the_script_draws_from_the_planned_containers_and_no_other() -> None:
    script = _script(_full_plan())
    drawn = {
        (float(x), float(y))
        for x, y in re.findall(
            r"extract_item\(Prototype\.Coal,Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)",
            script,
        )
    }

    assert drawn == set(COAL_STOCK)


def test_the_script_carries_off_the_planned_container_and_no_other() -> None:
    script = _script(_full_plan())
    picked = re.findall(
        r"pickup_entity\(Prototype\.WoodenChest,Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)",
        script,
    )

    assert [(float(x), float(y)) for x, y in picked] == [ORPHAN]


def test_the_script_takes_nothing_when_the_plan_salvages_nothing() -> None:
    plan = resupply.plan_supply(
        anchor=CENTRE,
        fuel_needed=12,
        fuel_carried=12,
        container_needed=True,
        spare_containers=(),
    )

    assert "pickup_entity" not in _script(plan)


def test_an_unsurveyed_world_still_declares_the_supply_counters() -> None:
    script = _script(None)

    assert "extract_item" not in script
    assert "pickup_entity" not in script
    for name in (
        "supply_fuel_drawn",
        "supply_fuel_log",
        "supply_container_recovered",
        "supply_note",
    ):
        assert f"{name}=" in script


def test_the_script_is_valid_python_for_the_engine() -> None:
    ast.parse(_script(_full_plan()))


def test_the_script_trips_no_fle_failure_trigger() -> None:
    script = _script(_full_plan())
    tree = ast.parse(script)
    offenders: list[str] = []
    for node in ast.walk(tree):
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

    assert not offenders, (
        f"{offenders} contem uma palavra que o FLE usa para marcar a acao "
        "como falha (environment.py:451)"
    )


def test_the_script_neutralises_whatever_the_engine_says() -> None:
    script = _script(_full_plan())

    assert script.count("replace('rror','rr0r')") == len(COAL_STOCK) + 1
    assert script.count("replace('xception','xcepti0n')") == len(COAL_STOCK) + 1


# --------------------------------------------------------------------------
# The stage, against a world that answers.
# --------------------------------------------------------------------------


class _FakeWorld:
    def __init__(self, *, entities: tuple[dict[str, Any], ...] = INHERITED_WORLD) -> None:
        self.entities: list[dict[str, Any]] = [dict(e) for e in entities]
        self.stock: dict[tuple[float, float], int] = dict(COAL_STOCK)
        self.carried: dict[str, int] = {"coal": 0, CHEST: 0}
        self.contents: dict[tuple[float, float], float] = {}
        self.iron_produced = 0.0
        self.ticks = 1000

    def snapshot(self) -> tuple[Any, ...]:
        return (
            [dict(e) for e in self.entities],
            dict(self.stock),
            dict(self.carried),
            dict(self.contents),
            self.iron_produced,
            self.ticks,
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        entities, stock, carried, contents, produced, ticks = snapshot
        self.entities = [dict(e) for e in entities]
        self.stock = dict(stock)
        self.carried = dict(carried)
        self.contents = dict(contents)
        self.iron_produced = produced
        self.ticks = ticks

    def taken(self) -> set[Any]:
        return blocked_tiles(self.entities)

    def item_count(self, x: float, y: float, item: str | None) -> int:
        if item is None:
            return self.stock.get((x, y), 0)
        if item != "coal":
            return int(self.contents.get((x, y), 0.0))
        return self.stock.get((x, y), 0)

    def place_cell(self, position: tuple[float, float]) -> bool:
        drill = _entity(DRILL, position[0], position[1], 8)
        tiles = entity_tiles(drill)
        if tiles & self.taken():
            return False
        chest_x = max(tile.x for tile in tiles) + 0.5
        chest_y = max(tile.y for tile in tiles) + 1.5
        chest = _entity(CHEST, chest_x, chest_y)
        if entity_tiles(chest) & (self.taken() | tiles):
            return False
        self.entities.append(drill)
        self.entities.append(chest)
        self.contents[(chest_x, chest_y)] = 1.0
        return True


class _FakeRcon:
    """Answers the three readings the supply layer takes off the world."""

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
            return str(
                self.world.item_count(float(found[2]), float(found[3]), item[1])
            )
        return ""


class _FakeNamespace:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.player_location = None

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self.world.entities]

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": {"iron-ore": self.world.iron_produced}}


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


class _FakeExecutor:
    def __init__(self, env: _FakeEnv) -> None:
        self.world = env.world
        self.namespace = env.unwrapped.instance.namespace
        self.scripts: list[str] = []
        self.placed: list[tuple[float, float]] = []
        self.refused: list[tuple[float, float]] = []

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
        self.world.ticks += STEP_TICKS
        self.world.iron_produced += 95.0
        step = _FakeStep()
        found = _POSITION.search(code)
        if found is not None and "place_entity(" in code:
            target = (float(found[1]), float(found[2]))
            if self.world.place_cell(target):
                self.placed.append(target)
            else:
                self.refused.append(target)
                step.info = {"error_occurred": True}
        self.namespace.trial_fuel = 12.0
        self.namespace.scale_fuel = 20.0
        self.namespace.supply_fuel_drawn = 12.0
        self.namespace.supply_container_recovered = 1.0
        self.namespace.supply_fuel_log = [(27.5, 80.5, 9), (27.5, 70.5, 3)]
        self.namespace.supply_note = ""
        step.accepted = bool(accept(step))
        if not step.accepted:
            self.world.restore(checkpoint)
        return step


class _FakeJournal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "metrics": {},
            "world": {},
            "online_learning": {},
            "evolution": {"champion": {}},
            "updated_at": None,
            "next_action": None,
        }
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.completed: list[tuple[int, str]] = []

    def set_stage(self, index: int, **_kwargs: Any) -> None:
        return None

    def flush(self) -> None:
        return None

    def event(self, event_type: str, message: str, **extra: Any) -> None:
        self.events.append((event_type, message, extra))

    def complete_stage(self, index: int, detail: str) -> None:
        self.completed.append((index, detail))


@pytest.fixture(autouse=True)
def _offline_lesson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        curriculum_runner,
        "synthesize_lesson",
        lambda **_kwargs: {
            "lesson": "stand-in",
            "next_hypothesis": "stand-in",
            "evidence_keys": ["output"],
        },
    )


def _learn(
    world: _FakeWorld | None = None,
    *,
    answers: bool = True,
) -> tuple[_FakeExecutor, _FakeJournal]:
    env = _FakeEnv(world or _FakeWorld(), answers=answers)
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    curriculum_runner.stage_online_learning(
        executor,
        env,
        journal,
        center=CENTRE,
        episodes=2,
        settle_seconds=8,
        exploration=2.0,
        radius_scale=1.0,
    )
    return executor, journal


def test_the_trial_no_longer_asks_for_coal_the_heir_does_not_carry() -> None:
    """The exact line generation 38 died on, eight times per restart."""
    executor, _ = _learn()

    assert "insert_item(Prototype.Coal, trial_drill, quantity=12)" not in (
        executor.scripts[0]
    )


def test_the_trial_draws_the_fuel_the_heir_does_not_carry() -> None:
    executor, journal = _learn()
    script = executor.scripts[0]

    assert "extract_item(Prototype.Coal,Position(x=27.5,y=80.5)" in script
    assert journal.state["metrics"]["placement_trial_supply"]["status"] == (
        curriculum_runner.SUPPLY_PLANNED
    )


def test_the_trial_carries_off_only_the_container_no_chain_touches() -> None:
    executor, _ = _learn()
    picked = re.findall(
        r"pickup_entity\(Prototype\.WoodenChest,Position\(x=(-?[\d.]+),y=(-?[\d.]+)\)",
        executor.scripts[0],
    )

    assert [(float(x), float(y)) for x, y in picked] == [ORPHAN], (
        "a etapa recuperou um bau que participa de uma cadeia"
    )


def test_the_charge_is_sized_from_the_window_with_the_curriculum_dose_as_floor() -> None:
    _, journal = _learn()

    assert journal.state["metrics"]["placement_trial_coal_dose"] >= (
        curriculum_runner.TRIAL_COAL_FLOOR
    )


def test_the_ledger_says_which_container_paid_for_the_trial() -> None:
    _, journal = _learn()
    row = journal.state["online_learning"]["history"][0]

    assert row["supply_draws"] == [
        {"position": {"x": 27.5, "y": 80.5}, "quantity": 9.0},
        {"position": {"x": 27.5, "y": 70.5}, "quantity": 3.0},
    ]
    assert row["supply_container_recovered"] == 1.0
    assert row["coal_inserted"] == 12.0
    salvage = journal.state["metrics"]["placement_trial_supply"]["salvage"]
    assert salvage["position"] == {"x": ORPHAN[0], "y": ORPHAN[1]}
    assert salvage["reason"] == resupply.REASON_NO_MATERIAL_EDGE


def test_a_world_with_nothing_to_give_refuses_instead_of_trialling() -> None:
    world = _FakeWorld(
        entities=tuple(
            entity
            for entity in INHERITED_WORLD
            if (entity["position"]["x"], entity["position"]["y"]) != ORPHAN
        )
    )
    world.stock = {}
    executor = _FakeExecutor(_FakeEnv(world))
    journal = _FakeJournal()

    with pytest.raises(curriculum_runner.PlacementNotMeasured) as raised:
        curriculum_runner.stage_online_learning(
            executor,
            _FakeEnv(world),
            journal,
            center=CENTRE,
            episodes=2,
            settle_seconds=8,
            exploration=2.0,
            radius_scale=1.0,
        )

    assert str(raised.value) == curriculum_runner.NO_TRIAL_SUPPLY
    assert journal.state["online_learning"]["status"] == (
        curriculum_runner.NO_TRIAL_SUPPLY
    )
    refusals = journal.state["metrics"]["placement_trial_supply"]["refusals"]
    assert resupply.REFUSAL_NO_FUEL_IN_WORLD in refusals
    assert resupply.REFUSAL_NO_SPARE_CONTAINER in refusals


def test_a_world_that_did_not_answer_does_not_count_as_a_world_without_supply() -> None:
    executor, journal = _learn(answers=False)

    assert journal.state["metrics"]["placement_trial_supply"] == {
        "status": curriculum_runner.SUPPLY_WORLD_UNREAD
    }
    assert len(executor.scripts) == 2, (
        "um inventario ilegivel recusou a etapa como se o mundo estivesse vazio"
    )


def test_the_promotion_writes_down_what_it_spent_of_the_world() -> None:
    world = _FakeWorld()
    env = _FakeEnv(world)
    journal = _FakeJournal()

    curriculum_runner.stage_scale_mining(
        _FakeExecutor(env),
        env,
        journal,
        center=CENTRE,
        best_arm="east_near",
        settle_seconds=14,
        radius_scale=1.0,
    )

    drawn = journal.state["metrics"]["scaled_supply_drawn"]
    assert drawn["supply_fuel_drawn"] == 12.0
    assert drawn["supply_container_recovered"] == 1.0
    assert drawn["supply_draws"] == [
        {"position": {"x": 27.5, "y": 80.5}, "quantity": 9.0},
        {"position": {"x": 27.5, "y": 70.5}, "quantity": 3.0},
    ]
    assert journal.state["metrics"]["scaled_supply"]["status"] == (
        curriculum_runner.SUPPLY_PLANNED
    )
