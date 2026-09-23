"""Stage 13 has to leave the circuit assembler on the engine's network.

Generations 53 to 56 ran the stage clean -- ``error_occurred: false``, boiler
and engine both ``working``, recipe set, 22 copper cable and 24 iron plate
inside the machine -- and produced no circuit. Generation 56 measured why:

    cable_assembler    network_id 7624   pole_gap 4.243   producing, 2755 J
    circuit_assembler  network_id   -1   pole_gap 6.403   no_power,     0 J
    nearest pole to the circuit assembler: network 7624, the live one
    poles laid by that second connect_entities: 8 (stock 489 -> 481)

``connect_entities`` stops laying poles once a position is saturated, and it
measures saturation against the wire reach of an electric pole rather than
against the supply area a machine needs
(fle/env/tools/agent/connect_entities/server.lua:61-91, 446-450). The two
numbers, read off the live prototypes over RCON on Factorio 2.0.73, are far
apart:

    medium-electric-pole get_max_wire_distance()     9
    medium-electric-pole get_supply_area_distance()  3.5
    assembling-machine-2 tile_width / tile_height    3

A 3x3 machine reaches 1.5 tiles from its centre, so a medium pole covers it
only within 5.0. Both gaps above are inside wire reach; only the second is
outside 5.0, and 6.403 is sqrt(4^2 + 5^2) -- one axis exactly on the boundary.
The chain was connected and the machine was not powered, which is why the call
returned success.

The world below carries those measurements and nothing else: the two gaps
``connect_entities`` left, the reach the prototypes report, and the rule that a
machine draws power only where a pole overlaps its footprint. The stage passes
here only if it ends with the machine on the live network.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner

#: Read over RCON from the running Factorio 2.0.73 prototypes, not from memory.
MAX_WIRE_DISTANCE = 9.0
SUPPLY_AREA_DISTANCE = 3.5
#: ``prototypes.entity["assembling-machine-2"].tile_width`` is 3.
MACHINE_HALF_EXTENT = 1.5
#: How near a medium pole has to be for a 3x3 machine to draw from it.
SUPPLY_LIMIT = SUPPLY_AREA_DISTANCE + MACHINE_HALF_EXTENT

#: Where the two ``connect_entities`` calls of generation 0056 left their last
#: pole, relative to the machine each was asked to power, in call order.
MEASURED_CONNECT_OFFSETS = ((3.0, 3.0), (4.0, 5.0))

#: The live network every pole on this map belongs to, as measured: the pole
#: nearest the unpowered machine reported the same 7624 the producing one used.
LIVE_NETWORK_ID = 7624

#: Positions generation 0056 measured, reproducing engine distances 25.807 and
#: 33.838. The stage's own placement search is not modelled.
ENGINE_POSITION = (2.5, 21.5)
SCIENCE_ASSEMBLER_POSITION = (16.5, 32.5)
CABLE_ASSEMBLER_POSITION = (23.5, 36.5)
CIRCUIT_ASSEMBLER_POSITION = (30.5, 40.5)

_RECIPE_NAMES = {
    "CopperCable": "copper-cable",
    "ElectronicCircuit": "electronic-circuit",
}


class _Position:
    def __init__(self, x: float, y: float) -> None:
        self.x = float(x)
        self.y = float(y)

    def __repr__(self) -> str:
        return f"Position({self.x},{self.y})"


class _Prototype:
    def __init__(self, name: str) -> None:
        self.name = name
        self.value = (name,)

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Prototype) and other.name == self.name

    def __repr__(self) -> str:
        return self.name


class _Prototypes:
    def __getattr__(self, item: str) -> _Prototype:
        proto = _Prototype(item)
        setattr(self, item, proto)
        return proto


class _Counter(dict):
    def __missing__(self, _key: Any) -> float:
        return 0.0


class _Entity:
    def __init__(self, name: str, position: _Position, half_extent: float) -> None:
        self.name = name
        self.position = position
        self.half_extent = half_extent
        self.status = "no_power"
        self.energy = 0.0
        self.electrical_id: int | None = None
        self.recipe: Any = None
        self.inventory: _Counter = _Counter()


class _World:
    """A map holding the measured geometry and the measured electric reach."""

    def __init__(self, protos: _Prototypes, *, tap_refused: bool = False) -> None:
        self.protos = protos
        self.poles: list[_Entity] = []
        self.machines: list[_Entity] = []
        self.engine = _Entity("steam-engine", _Position(*ENGINE_POSITION), 1.0)
        self.player_poles = 490.0
        self.connect_offsets = list(MEASURED_CONNECT_OFFSETS)
        self.tap_refused = tap_refused
        self.taps: list[_Entity] = []

    def covers(self, pole: _Entity, entity: _Entity) -> bool:
        reach = SUPPLY_AREA_DISTANCE + entity.half_extent
        return (
            abs(pole.position.x - entity.position.x) < reach
            and abs(pole.position.y - entity.position.y) < reach
        )

    def network_id(self, entity: _Entity) -> int | None:
        if any(self.covers(pole, entity) for pole in self.poles):
            return LIVE_NETWORK_ID
        return None

    def pole_gap(self, entity: _Entity) -> float:
        if not self.poles:
            return -1.0
        return min(
            math.dist(
                (pole.position.x, pole.position.y),
                (entity.position.x, entity.position.y),
            )
            for pole in self.poles
        )

    def refresh(self, entity: _Entity) -> _Entity:
        entity.electrical_id = self.network_id(entity)
        if entity.electrical_id is None:
            entity.status = "no_power"
            entity.energy = 0.0
            return entity
        entity.energy = 2755.5555555556
        recipe = getattr(entity.recipe, "name", "")
        produced = (
            entity.inventory[self.protos.CopperCable]
            if recipe == "copper-cable"
            else entity.inventory[self.protos.ElectronicCircuit]
        )
        entity.status = "full_output" if produced >= 22 else "working"
        return entity

    def add_pole(self, x: float, y: float) -> _Entity:
        self.player_poles -= 1
        pole = _Entity("medium-electric-pole", _Position(x, y), 0.5)
        self.poles.append(pole)
        return pole

    def connect_power(self, target: _Entity) -> None:
        """Lay the pole line the way generation 0056 measured it.

        Eight poles were spent on the second call and the nearest one still
        stopped 6.403 tiles away, so what is reproduced is the end of the line,
        not the walk.
        """
        offset = self.connect_offsets.pop(0) if self.connect_offsets else (4.0, 5.0)
        self.player_poles -= 7
        self.add_pole(
            target.position.x - offset[0],
            target.position.y - offset[1],
        )


def _build_namespace(*, tap_refused: bool = False) -> tuple[dict[str, Any], _World]:
    protos = _Prototypes()
    world = _World(protos, tap_refused=tap_refused)

    def _machine(position: tuple[float, float]) -> _Entity:
        entity = _Entity(
            "assembling-machine-2", _Position(*position), MACHINE_HALF_EXTENT
        )
        world.machines.append(entity)
        return entity

    science = _machine(SCIENCE_ASSEMBLER_POSITION)
    buildable = iter((CABLE_ASSEMBLER_POSITION, CIRCUIT_ASSEMBLER_POSITION))

    def nearest_buildable(_proto: Any, _box: Any, _position: Any) -> Any:
        return SimpleNamespace(center=_Position(*next(buildable)))

    def place_entity(proto: Any, position: _Position, **_kwargs: Any) -> _Entity:
        if proto == protos.MediumElectricPole:
            return world.add_pole(position.x, position.y)
        return world.refresh(_machine((position.x, position.y)))

    def place_entity_next_to(
        proto: Any,
        reference_position: _Position,
        direction: Any = None,
        **_kwargs: Any,
    ) -> _Entity:
        if proto != protos.MediumElectricPole:
            raise AssertionError("only a pole is placed beside a machine here")
        if world.tap_refused:
            raise RuntimeError("no room beside the machine")
        offset = {
            "LEFT": (-2.0, 0.0),
            "RIGHT": (2.0, 0.0),
            "UP": (0.0, -2.0),
            "DOWN": (0.0, 2.0),
        }[str(direction)]
        tap = world.add_pole(
            reference_position.x + offset[0],
            reference_position.y + offset[1],
        )
        world.taps.append(tap)
        return tap

    def connect_entities(_source: _Entity, target: _Entity, _proto: Any) -> Any:
        world.connect_power(target)
        world.refresh(target)
        return SimpleNamespace(poles=list(world.poles))

    def get_entity(_proto: Any, position: _Position) -> _Entity:
        for entity in world.machines:
            if (entity.position.x, entity.position.y) == (position.x, position.y):
                return world.refresh(entity)
        return world.engine

    def get_entities(asked: Any = None, **_kwargs: Any) -> list[_Entity]:
        wanted = asked if isinstance(asked, set) else {asked}
        if protos.MediumElectricPole in wanted:
            return list(world.poles)
        return []

    def inspect_inventory(entity: Any = None) -> _Counter:
        if entity is None:
            return _Counter(
                {
                    protos.MediumElectricPole: world.player_poles,
                    protos.AssemblingMachine2: 4.0,
                }
            )
        if isinstance(entity, _Entity):
            return entity.inventory
        return _Counter()

    def insert_item(proto: Any, entity: _Entity, quantity: float = 0) -> _Entity:
        entity.inventory[proto] = entity.inventory[proto] + quantity
        return world.refresh(entity) if entity in world.machines else entity

    def extract_item(proto: Any, entity: Any, quantity: float = 0) -> float:
        if isinstance(entity, _Entity):
            taken = min(entity.inventory[proto], quantity)
            entity.inventory[proto] = entity.inventory[proto] - taken
            return taken
        return quantity

    def set_entity_recipe(entity: _Entity, proto: Any) -> _Entity:
        entity.recipe = SimpleNamespace(name=_RECIPE_NAMES[proto.name])
        return world.refresh(entity)

    def sleep(_seconds: float) -> None:
        for machine in world.machines:
            world.refresh(machine)
            if machine.electrical_id is None:
                continue
            recipe = getattr(machine.recipe, "name", "")
            if recipe == "copper-cable":
                machine.inventory[protos.CopperCable] = 22.0
            elif (
                recipe == "electronic-circuit"
                and machine.inventory[protos.CopperCable] >= 3
            ):
                machine.inventory[protos.ElectronicCircuit] = 7.0
            world.refresh(machine)

    def _chest(position: tuple[float, float], stock: dict[Any, float]) -> _Entity:
        entity = _Entity("wooden-chest", _Position(*position), 0.5)
        entity.inventory = _Counter(stock)
        return entity

    namespace: dict[str, Any] = {
        "Prototype": protos,
        "Direction": SimpleNamespace(
            LEFT="LEFT", RIGHT="RIGHT", UP="UP", DOWN="DOWN"
        ),
        "BuildingBox": lambda **kwargs: SimpleNamespace(**kwargs),
        "Position": _Position,
        "nearest_buildable": nearest_buildable,
        "place_entity": place_entity,
        "place_entity_next_to": place_entity_next_to,
        "connect_entities": connect_entities,
        "get_entity": get_entity,
        "get_entities": get_entities,
        "inspect_inventory": inspect_inventory,
        "insert_item": insert_item,
        "extract_item": extract_item,
        "set_entity_recipe": set_entity_recipe,
        "move_to": lambda _position: None,
        "sleep": sleep,
        "print": lambda _payload: None,
        "science_assembler": science,
        "steam_engine": world.engine,
        "boiler": _Entity("boiler", _Position(-4.0, 16.5), 1.0),
        "chest": _chest((0.0, 0.0), {protos.IronOre: 24.0}),
        "coal_chest": _chest((1.0, 1.0), {protos.Coal: 136.0}),
        "copper_chest": _chest((2.0, 2.0), {protos.CopperOre: 149.0}),
        "copper_furnace": _chest((3.0, 3.0), {protos.CopperPlate: 24.0}),
        "smelt_furnace": _chest((4.0, 4.0), {protos.IronPlate: 24.0}),
        "coal_drill": _chest((5.0, 5.0), {}),
        "copper_drill": _chest((6.0, 6.0), {}),
        "drill": _chest((7.0, 7.0), {}),
        "scale_drill": _chest((8.0, 8.0), {}),
    }
    namespace["boiler"].status = "working"
    world.engine.status = "working"
    world.engine.energy = 14416.666666667
    return namespace, world


class _Step:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False, "result": ""}
        self.candidate_game_state: Any = object()
        self.accepted = False


class _Journal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"metrics": {}}
        self.failed: list[tuple[int, str]] = []
        self.completed: list[tuple[int, str]] = []

    def set_stage(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def event(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def complete_stage(self, index: int, detail: str) -> None:
        self.completed.append((index, detail))

    def fail_stage(self, index: int, detail: str) -> None:
        self.failed.append((index, detail))


class _RunNamespace:
    """What the stage reads its measurements back off after the script ran.

    The stage binds this object once, before the step runs, so the readings
    have to land on the same instance the stage already holds.
    """

    def __init__(self) -> None:
        self._produced = 0.0

    def adopt(self, bindings: dict[str, Any], produced: float) -> None:
        self._produced = produced
        for key, value in bindings.items():
            if key.startswith("_") or callable(value):
                continue
            try:
                setattr(self, key, value)
            except AttributeError:
                pass

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": {"electronic-circuit": self._produced}}


class _Instance:
    def __init__(self) -> None:
        self.namespace = _RunNamespace()
        self.ticks = 0

    def get_elapsed_ticks(self) -> int:
        return self.ticks


class _Env:
    def __init__(self) -> None:
        self.unwrapped = SimpleNamespace(instance=_Instance())


class _Executor:
    def __init__(self, env: _Env, *, tap_refused: bool = False) -> None:
        self.env = env
        self.tap_refused = tap_refused
        self.world: _World | None = None
        self.scripts: list[str] = []

    def execute(
        self,
        code: str,
        *,
        accept: Any,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> _Step:
        self.scripts.append(code)
        bindings, world = _build_namespace(tap_refused=self.tap_refused)
        self.world = world
        exec(compile(code, "<stage13>", "exec"), bindings)  # noqa: S102
        circuits = 0.0
        for machine in world.machines:
            if getattr(machine.recipe, "name", "") == "electronic-circuit":
                circuits = machine.inventory[
                    bindings["Prototype"].ElectronicCircuit
                ]
        instance = self.env.unwrapped.instance
        instance.ticks += 1200
        instance.namespace.adopt(bindings, circuits)
        step = _Step()
        step.accepted = bool(accept(step))
        return step


def _run_stage(*, tap_refused: bool = False) -> tuple[bool, _Journal, _World]:
    env = _Env()
    executor = _Executor(env, tap_refused=tap_refused)
    journal = _Journal()
    accepted = curriculum_runner.stage_electronic_circuits(
        executor,
        env,
        journal,
        settle_seconds=20,
    )
    assert executor.world is not None
    return accepted, journal, executor.world


def _circuit_machine(world: _World) -> _Entity:
    machines = [
        machine
        for machine in world.machines
        if getattr(machine.recipe, "name", "") == "electronic-circuit"
    ]
    assert machines, "a etapa nao chegou a montar a assembladora de circuitos"
    return machines[0]


def test_the_world_reproduces_what_generation_0056_measured() -> None:
    """The model has to make the same mistake before it can judge the fix."""
    protos = _Prototypes()
    world = _World(protos)
    cable = _Entity(
        "assembling-machine-2",
        _Position(*CABLE_ASSEMBLER_POSITION),
        MACHINE_HALF_EXTENT,
    )
    circuit = _Entity(
        "assembling-machine-2",
        _Position(*CIRCUIT_ASSEMBLER_POSITION),
        MACHINE_HALF_EXTENT,
    )
    world.connect_power(cable)
    assert world.pole_gap(cable) == pytest.approx(4.2426, abs=1e-3)
    assert world.network_id(cable) == LIVE_NETWORK_ID
    world.connect_power(circuit)
    assert world.pole_gap(circuit) == pytest.approx(6.4031, abs=1e-3)
    # Inside wire reach and outside the supply area: connected on the wire,
    # unpowered on the machine, which is why the call reported success.
    assert world.pole_gap(circuit) < MAX_WIRE_DISTANCE
    assert world.pole_gap(circuit) > SUPPLY_LIMIT
    assert world.network_id(circuit) is None


def test_the_stage_leaves_the_circuit_assembler_on_the_live_network() -> None:
    _, _, world = _run_stage()
    circuit = _circuit_machine(world)
    assert circuit.electrical_id == LIVE_NETWORK_ID, (
        "a assembladora de circuitos terminou fora da rede; poste mais "
        f"proximo a {world.pole_gap(circuit):.3f} tiles, limite de "
        f"cobertura {SUPPLY_LIMIT}"
    )


def test_the_stage_produces_circuits_once_the_machine_is_powered() -> None:
    accepted, journal, _ = _run_stage()
    diagnostics = journal.state["metrics"]["electronic_circuit_diagnostics"]
    assert diagnostics["circuit_assembler"]["status_after"] != "no_power"
    assert journal.state["metrics"]["electronic_circuit_output"] > 0
    assert accepted is True


def test_the_supply_tap_is_recorded_with_its_distance() -> None:
    """Which pole powered the machine has to be readable.

    A machine reached by the chain and a machine reached by one pole placed
    beside it are different results, and only the recorded tap tells them
    apart in the next generation's report.
    """
    _, journal, world = _run_stage()
    circuit = journal.state["metrics"]["electronic_circuit_diagnostics"][
        "circuit_assembler"
    ]
    assert circuit["tap_placed"] == 1.0
    assert circuit["tap_gap"] is not None
    assert circuit["tap_gap"] < SUPPLY_LIMIT
    assert circuit["network_id"] == float(LIVE_NETWORK_ID)
    assert len(world.taps) == 1


def test_the_tap_is_spent_only_on_a_machine_that_has_no_network() -> None:
    """The cable assembler is already powered, so nothing is placed beside it.

    Spending a pole on a machine that is already drawing would hide the
    defect instead of fixing it, and would spend one pole per generation for
    nothing.
    """
    _, journal, world = _run_stage()
    cable = journal.state["metrics"]["electronic_circuit_diagnostics"][
        "cable_assembler"
    ]
    assert cable["tap_placed"] == 0.0
    assert cable["tap_gap"] is None
    assert len(world.taps) == 1


def test_a_machine_that_cannot_be_tapped_is_refused_with_the_measurement() -> None:
    """No room for the pole is an answer, and it has to be the recorded one."""
    accepted, journal, world = _run_stage(tap_refused=True)
    assert accepted is False
    circuit = journal.state["metrics"]["electronic_circuit_diagnostics"][
        "circuit_assembler"
    ]
    assert circuit["network_id"] == -1.0
    assert circuit["tap_placed"] == 0.0
    assert circuit["pole_gap"] == pytest.approx(6.4031, abs=1e-3)
    assert _circuit_machine(world).electrical_id is None
    note = journal.state["metrics"]["electronic_circuit_diagnostics"]["power"][
        "note"
    ]
    assert note and "tap" in note


def test_an_unread_tap_is_none_and_never_zero() -> None:
    probe = curriculum_runner._assembler_probe(
        SimpleNamespace(),
        prefix="circuit",
        inputs={},
        output_after="circuit_inventory",
    )
    assert probe["tap_placed"] is None
    assert probe["tap_gap"] is None
    assert probe["network_id"] is None


class _Rcon:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.commands: list[str] = []

    def send_command(self, command: str) -> Any:
        self.commands.append(command)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_pole_reach_comes_from_the_live_prototypes() -> None:
    """Both numbers, from the runtime, in one read.

    The live server answers 3.5 and 9 for a medium pole on Factorio 2.0.73;
    what is asserted here is that the reading is taken and carried, not the
    values, which belong to the game and not to this file.
    """
    rcon = _Rcon('{"supply_area_distance":3.5,"max_wire_distance":9}')
    reach = curriculum_runner._runtime_pole_reach(SimpleNamespace(rcon_client=rcon))
    assert reach == {"supply_area_distance": 3.5, "max_wire_distance": 9.0}
    assert "medium-electric-pole" in rcon.commands[0]
    assert "get_supply_area_distance" in rcon.commands[0]
    assert "get_max_wire_distance" in rcon.commands[0]


@pytest.mark.parametrize(
    "answer",
    ["", "{}", "not json", '{"supply_area_distance":null}', OSError("no socket")],
)
def test_a_runtime_that_does_not_answer_leaves_the_reach_unmeasured(
    answer: Any,
) -> None:
    # A reach defaulted to a literal would let the stage claim a machine is
    # out of range, or in it, on a number nobody read.
    reach = curriculum_runner._runtime_pole_reach(
        SimpleNamespace(rcon_client=_Rcon(answer))
    )
    assert reach.get("supply_area_distance") is None
    assert reach.get("max_wire_distance") is None


def test_the_diagnostics_carry_the_reach_or_say_it_was_not_read() -> None:
    _, journal, _ = _run_stage()
    diagnostics = journal.state["metrics"]["electronic_circuit_diagnostics"]
    assert "pole_reach" in diagnostics
    assert diagnostics["pole_reach"] is None
