"""Stage 13 has to say what its assemblers were doing, not only what came out.

Generation 47 recorded ``cable: 0.0`` with ``error_occurred: false`` and no
other reading of the two machines it had just built, so the stage was
indistinguishable from four different failures: an assembler with no power, one
with no recipe, one with no ingredient, and one whose window was too short. The
series 3/16 -> 6/16 -> 13/16 stopped at 13 for four generations while that
ambiguity stood.

What is asserted here is the instrument, not the fix. The stage must read the
status, the stored energy, the recipe and both buffers of the copper-cable
assembler and of the electronic-circuit assembler, on both sides of the
production window, and it must record them whether the stage is accepted or
rejected -- a diagnostic written only on failure cannot be compared against the
run that worked.

The invariant the readings carry is the one this repository has paid for
repeatedly: a reading the script never took is ``None``. Zero is a measurement.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.learning.factory_graph import (
    STALL_CAUSE_FUEL,
    STALL_CAUSE_INGREDIENTS,
    STALL_CAUSE_POWER,
    STALL_CAUSE_PRODUCING,
    STALL_CAUSE_RECIPE,
    STALL_CAUSE_WINDOW,
    classify_assembler_stall,
)


class _Step:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False, "result": ""}
        self.candidate_game_state: Any = object()
        self.accepted = False


class _Namespace:
    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": {}}


class _Instance:
    def __init__(self) -> None:
        self.namespace = _Namespace()
        self.ticks = 0

    def get_elapsed_ticks(self) -> int:
        return self.ticks


class _Env:
    def __init__(self) -> None:
        self.unwrapped = SimpleNamespace(instance=_Instance())


class _Executor:
    """Runs one step and leaves behind exactly the readings it is given."""

    def __init__(self, env: _Env, readings: dict[str, Any]) -> None:
        self.env = env
        self.readings = readings
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
        instance = self.env.unwrapped.instance
        instance.ticks += 900
        for key, value in self.readings.items():
            setattr(instance.namespace, key, value)
        step = _Step()
        step.accepted = bool(accept(step))
        return step


class _Journal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"metrics": {}}
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.completed: list[tuple[int, str]] = []
        self.failed: list[tuple[int, str]] = []

    def set_stage(self, index: int, **_kwargs: Any) -> None:
        return None

    def event(self, event_type: str, message: str, **extra: Any) -> None:
        self.events.append((event_type, message, extra))

    def complete_stage(self, index: int, detail: str) -> None:
        self.completed.append((index, detail))

    def fail_stage(self, index: int, detail: str) -> None:
        self.failed.append((index, detail))


#: The readings generation 47 took, with the ones it never took filled in as a
#: powered-down cable assembler: the hypothesis this instrument has to be able
#: to confirm or refute, not one it may assume.
UNPOWERED_RUN: dict[str, Any] = {
    "circuit_coal_available": 136,
    "circuit_coal": 10,
    "circuit_copper_buffer_before": 149,
    "circuit_copper_ore": 24,
    "circuit_iron_ore": 0,
    "circuit_copper": 11,
    "circuit_iron": 24,
    "circuit_nav_note": "",
    "cable_machine_stock": 2,
    "cable_pole_stock": 9,
    "cable_engine_distance": 46.5,
    "cable_status_before": "no_power",
    "cable_status_after": "no_power",
    "cable_energy_before": 0.0,
    "cable_energy_after": 0.0,
    "cable_recipe_after": "copper-cable",
    "cable_input_plate_before": 11,
    "cable_input_plate_after": 11,
    "cable_output_before": 0,
    "cable_inventory": 0,
    "cable_transfer": 0,
    "circuit_machine_stock": 1,
    "circuit_pole_stock": 4,
    "circuit_engine_distance": 52.0,
    "circuit_status_before": "no_power",
    "circuit_status_after": "no_power",
    "circuit_energy_before": 0.0,
    "circuit_energy_after": 0.0,
    "circuit_recipe_after": "electronic-circuit",
    "circuit_input_cable_before": 0,
    "circuit_input_cable_after": 0,
    "circuit_input_iron_before": 24,
    "circuit_input_iron_after": 24,
    "circuit_output_before": 0,
    "circuit_inventory": 0,
    "circuit_boiler_status_before": "working",
    "circuit_boiler_status_after": "no_fuel",
    "circuit_engine_status_before": "working",
    "circuit_engine_status_after": "no_power",
    "circuit_engine_energy_before": 15000.0,
    "circuit_engine_energy_after": 0.0,
    "circuit_power_note": "",
}


def _run(readings: dict[str, Any]) -> tuple[bool, _Journal, _Executor]:
    env = _Env()
    executor = _Executor(env, readings)
    journal = _Journal()
    accepted = curriculum_runner.stage_electronic_circuits(
        executor,
        env,
        journal,
        settle_seconds=20,
    )
    return accepted, journal, executor


def _stage_script() -> str:
    _, _, executor = _run(UNPOWERED_RUN)
    return executor.scripts[0]


def _script_names(script: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(script)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


#: Every reading the stage has to take off the two assemblers it builds.
REQUIRED_READINGS = (
    "cable_status_before",
    "cable_status_after",
    "cable_energy_before",
    "cable_energy_after",
    "cable_recipe_after",
    "cable_input_plate_before",
    "cable_input_plate_after",
    "cable_output_before",
    "cable_machine_stock",
    "cable_pole_stock",
    "circuit_status_before",
    "circuit_status_after",
    "circuit_energy_before",
    "circuit_energy_after",
    "circuit_recipe_after",
    "circuit_input_cable_before",
    "circuit_input_cable_after",
    "circuit_input_iron_before",
    "circuit_input_iron_after",
    "circuit_output_before",
    "circuit_machine_stock",
    "circuit_pole_stock",
)


@pytest.mark.parametrize("reading", REQUIRED_READINGS)
def test_the_stage_script_takes_every_assembler_reading(reading: str) -> None:
    script = _stage_script()
    assert reading in _script_names(script), (
        f"a etapa 13 nao mede {reading}"
    )
    assert f"'{reading}'" in script, (
        f"{reading} e medido mas nao volta no payload impresso"
    )


def test_the_stage_reads_the_machines_back_before_and_after_the_window() -> None:
    # A status read off the entity `place_entity` returned is the status it had
    # at placement, not after the window. Only a fresh `get_entity` measures
    # what the machine did while the stage slept.
    script = _stage_script()
    assert script.count(
        "get_entity(Prototype.AssemblingMachine2,cable_assembler.position)"
    ) == 2
    assert script.count(
        "get_entity(Prototype.AssemblingMachine2,circuit_assembler.position)"
    ) == 2


def test_diagnostics_are_recorded_even_when_the_stage_is_rejected() -> None:
    accepted, journal, _ = _run(UNPOWERED_RUN)
    assert accepted is False
    diagnostics = journal.state["metrics"]["electronic_circuit_diagnostics"]
    assert diagnostics["accepted"] is False
    cable = diagnostics["cable_assembler"]
    assert cable["status_before"] == "no_power"
    assert cable["status_after"] == "no_power"
    assert cable["energy_after"] == 0.0
    assert cable["recipe"] == "copper-cable"
    assert cable["inputs_before"] == {"copper_plate": 11.0}
    assert cable["output_after"] == 0.0
    assert cable["machine_stock"] == 2.0
    assert cable["pole_stock"] == 9.0
    assert cable["engine_distance"] == pytest.approx(46.5)
    assert cable["stall_cause"] == STALL_CAUSE_POWER


def test_the_power_chain_is_recorded_on_both_sides_of_the_window() -> None:
    _, journal, _ = _run(UNPOWERED_RUN)
    power = journal.state["metrics"]["electronic_circuit_diagnostics"]["power"]
    assert power["boiler_status_before"] == "working"
    assert power["boiler_status_after"] == "no_fuel"
    assert power["engine_status_after"] == "no_power"
    assert power["engine_energy_after"] == 0.0
    assert power["note"] is None


def test_the_circuit_assembler_is_diagnosed_too() -> None:
    _, journal, _ = _run(UNPOWERED_RUN)
    circuit = journal.state["metrics"]["electronic_circuit_diagnostics"][
        "circuit_assembler"
    ]
    assert circuit["inputs_before"] == {"copper_cable": 0.0, "iron_plate": 24.0}
    assert circuit["recipe"] == "electronic-circuit"
    assert circuit["stall_cause"] == STALL_CAUSE_POWER


def test_an_unmeasured_assembler_is_none_and_never_zero() -> None:
    # The script aborting before its first reading is not the same as a machine
    # measured at zero energy, and the eleven generations lost to that
    # confusion are the reason this assertion exists.
    accepted, journal, _ = _run({})
    assert accepted is False
    diagnostics = journal.state["metrics"]["electronic_circuit_diagnostics"]
    cable = diagnostics["cable_assembler"]
    assert cable["status_before"] is None
    assert cable["energy_before"] is None
    assert cable["recipe"] is None
    assert cable["inputs_before"] == {"copper_plate": None}
    assert cable["output_after"] is None
    assert cable["stall_cause"] is None
    assert diagnostics["power"]["boiler_status_before"] is None


def test_an_accepted_stage_still_records_the_diagnostics() -> None:
    readings = dict(UNPOWERED_RUN)
    readings.update(
        {
            "cable_status_before": "working",
            "cable_status_after": "working",
            "cable_energy_before": 30000.0,
            "cable_energy_after": 30000.0,
            "cable_inventory": 22,
            "cable_transfer": 22,
            "circuit_status_before": "working",
            "circuit_status_after": "working",
            "circuit_input_cable_before": 22,
            "circuit_input_cable_after": 4,
            "circuit_inventory": 9,
        }
    )
    accepted, journal, _ = _run(readings)
    assert accepted is True
    diagnostics = journal.state["metrics"]["electronic_circuit_diagnostics"]
    assert diagnostics["accepted"] is True
    assert diagnostics["cable_assembler"]["stall_cause"] == STALL_CAUSE_PRODUCING
    assert diagnostics["circuit_assembler"]["output_after"] == 9.0


def test_the_counterexample_keeps_the_buffer_readings_it_already_had() -> None:
    # The buffer measurements are the evidence six earlier corrections were
    # argued from. Adding the assembler probe must not drop them.
    _, journal, _ = _run(UNPOWERED_RUN)
    counterexample = journal.state["metrics"][
        "electronic_circuit_counterexample"
    ]
    assert counterexample["circuit_copper"] == 11.0
    assert counterexample["circuit_copper_buffer_before"] == 149.0
    assert counterexample["cable"] == 0.0
    assert counterexample["cable_assembler"]["stall_cause"] == STALL_CAUSE_POWER


@pytest.mark.parametrize(
    "status,recipe,input_count,output_count,expected",
    [
        ("no_power", "copper-cable", 11.0, 0.0, STALL_CAUSE_POWER),
        ("low_power", "copper-cable", 11.0, 0.0, STALL_CAUSE_POWER),
        (
            "not_plugged_in_electric_network",
            "copper-cable",
            11.0,
            0.0,
            STALL_CAUSE_POWER,
        ),
        ("no_fuel", "copper-cable", 11.0, 0.0, STALL_CAUSE_FUEL),
        ("no_recipe", "", 11.0, 0.0, STALL_CAUSE_RECIPE),
        ("working", "", 11.0, 0.0, STALL_CAUSE_RECIPE),
        ("no_ingredients", "copper-cable", 0.0, 0.0, STALL_CAUSE_INGREDIENTS),
        (
            "item_ingredient_shortage",
            "copper-cable",
            0.0,
            0.0,
            STALL_CAUSE_INGREDIENTS,
        ),
        ("working", "copper-cable", 0.0, 0.0, STALL_CAUSE_INGREDIENTS),
        ("working", "copper-cable", 11.0, 0.0, STALL_CAUSE_WINDOW),
        ("normal", "copper-cable", 11.0, 0.0, STALL_CAUSE_WINDOW),
        ("full_output", "copper-cable", 11.0, 0.0, "full_output"),
    ],
)
def test_the_cause_is_named_from_the_measurement(
    status: str,
    recipe: str,
    input_count: float,
    output_count: float,
    expected: str,
) -> None:
    assert (
        classify_assembler_stall(
            status=status,
            recipe=recipe,
            input_count=input_count,
            output_count=output_count,
        )
        == expected
    )


def test_a_machine_that_produced_is_not_stalled() -> None:
    assert (
        classify_assembler_stall(
            status="no_power",
            recipe="copper-cable",
            input_count=0.0,
            output_count=16.0,
        )
        == STALL_CAUSE_PRODUCING
    )


@pytest.mark.parametrize(
    "status,recipe,input_count,output_count",
    [
        (None, None, None, None),
        (None, "copper-cable", 11.0, 0.0),
        ("unknown", "copper-cable", 11.0, 0.0),
    ],
)
def test_an_unmeasured_machine_names_no_cause(
    status: str | None,
    recipe: str | None,
    input_count: float | None,
    output_count: float | None,
) -> None:
    assert (
        classify_assembler_stall(
            status=status,
            recipe=recipe,
            input_count=input_count,
            output_count=output_count,
        )
        is None
    )


def test_a_quoted_status_is_the_same_status() -> None:
    # The mod serialiser quotes what it writes; both spellings have to reach
    # the same verdict or a starved machine reads as healthy.
    assert (
        classify_assembler_stall(
            status='"no_power"',
            recipe="copper-cable",
            input_count=11.0,
            output_count=0.0,
        )
        == STALL_CAUSE_POWER
    )
