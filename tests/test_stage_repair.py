"""A failed stage must be answered by a repair, not by the end of the run.

`factorio_ai_lab.learning.repair_loop` decides a repair from readings the lab
already takes, and for one commit it decided them for nobody: it had no caller.
These tests are about the caller. Every one of them asserts what the runner
does to a world and writes to the ledger, never the shape of the code that does
it: three tests in this repository once asserted the wrong belt rotation in the
abstract syntax tree and passed while the game dropped coal on the ground.

The invariants under test, in the order they matter:

* a repair that could not be measured is never learned from as a success;
* a step nobody executed is a row in the ledger with its reason, never a
  silence and never a reward;
* the retry budget is hard, per stage and per generation;
* nothing that feeds a live chain is ever torn down;
* with an empty ledger the fixed rule decides, and says that it did.
"""

from __future__ import annotations

import ast
import builtins
import json

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.learning.repair_loop import (
    DIRECTION_DECREASE,
    TOOL_REBUILD,
    Deficit,
    Diagnosis,
    Prediction,
    RepairAction,
    RepairProposal,
    RepairStep,
    detect_deficits,
)

FUEL_ARM = "resupply:insert_fuel_from_world_container"
POWER_ARM = "placement:extend_power_supply_to_machine"
BUILD_ARM = "placement:place_processing_for_buffered_output"
REROUTE_ARM = "rebuild:reroute_producer_logistics"


def entity(name, x, y, *, unit, status="working", direction=0):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        "unit_number": unit,
        "status": status,
    }


#: A drill with no coal and the chest it drops into. The drill is the entity
#: the repair has to be able to name; the chest is the container it draws from.
STARVED_WORLD = [
    entity("burner-mining-drill", 10.0, 10.0, unit=1, status="no_fuel", direction=8),
    entity("wooden-chest", 10.0, 11.5, unit=2),
]

#: The same world after a refuel: the drill reports working, and the steam path
#: the world never had is alive. The second half exists so a step nobody
#: executed would score as held if it were scored at all.
HEALED_WORLD = [
    entity("burner-mining-drill", 10.0, 10.0, unit=1, status="working", direction=8),
    entity("wooden-chest", 10.0, 11.5, unit=2),
    entity("offshore-pump", 40.0, 40.0, unit=3),
    entity("steam-engine", 42.0, 40.0, unit=4),
]

#: An assembler that reports no power, with the pole line that stopped short of
#: it. `connect_entities` answers success while the machine draws nothing.
POWERLESS_WORLD = [
    entity("assembling-machine-2", 20.0, 20.0, unit=1, status="no_power"),
    entity("medium-electric-pole", 28.0, 20.0, unit=2),
]

POWERED_WORLD = [
    entity("assembling-machine-2", 20.0, 20.0, unit=1, status="working"),
    entity("medium-electric-pole", 28.0, 20.0, unit=2),
    entity("medium-electric-pole", 21.5, 20.0, unit=3),
]

#: A drill whose ore reaches a chest and no machine at all. Two tools answer
#: this symptom, which is the whole reason the choice is worth learning.
BUFFERED_WORLD = [
    entity("burner-mining-drill", 10.0, 10.0, unit=1, direction=8),
    entity("wooden-chest", 10.0, 11.5, unit=2),
]


class _Namespace:
    """The long-lived FLE namespace: entities in, script variables out."""

    def __init__(self, world):
        self.world = list(world)

    def _save_entity_state(self, **_kwargs):
        return list(self.world)


class _Rcon:
    """Every chest holds 200 coal and the agent carries 200."""

    def __init__(self, answer="200"):
        self.answer = answer
        self.commands = []

    def send_command(self, command):
        self.commands.append(command)
        return self.answer


class _Instance:
    def __init__(self, world, rcon):
        self.namespace = _Namespace(world)
        self.rcon_client = rcon


class _Env:
    def __init__(self, world, *, rcon=None):
        self.instance = _Instance(world, rcon or _Rcon())
        self.unwrapped = self


class _Step:
    def __init__(self):
        self.info = {"error_occurred": False}
        self.candidate_game_state = "state-1"
        self.accepted = False


class _Executor:
    """Runs the repair script the way the engine would, and can be told to
    refuse it. `heals_into` is the world the script leaves behind."""

    def __init__(self, env, *, heals_into=None, accepts=True):
        self.env = env
        self.heals_into = heals_into
        self.accepts = accepts
        self.codes = []
        self.purposes = []

    def execute(self, code, *, accept, use_checkpoint_for_action=True, purpose="operation"):
        self.codes.append(code)
        self.purposes.append(purpose)
        namespace = self.env.instance.namespace
        if "repair_inserted" in code:
            namespace.repair_inserted = 16.0 if self.accepts else 0.0
            namespace.repair_drawn = 16.0
            namespace.repair_note = ""
        if "repair_poles" in code:
            namespace.repair_poles = 1.0 if self.accepts else 0.0
            namespace.repair_pole_stock = 4.0
            namespace.repair_note = ""
        step = _Step()
        step.accepted = bool(accept(step))
        if step.accepted and self.heals_into is not None:
            namespace.world = list(self.heals_into)
        return step


class _Journal:
    def __init__(self, metrics=None):
        self.run_id = "curriculum-test"
        self.state = {
            "metrics": dict(metrics or {}),
            "evolution": {"generation": 7},
        }
        self.events = []

    def event(self, event_type, message, **extra):
        self.events.append({"type": event_type, "message": message, **extra})


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "repairs.jsonl"
    monkeypatch.setattr(curriculum_runner, "REPAIR_LEDGER", path)
    return path


def rows(ledger):
    return [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def only(rows_, **match):
    found = [
        row
        for row in rows_
        if all(row.get(key) == value for key, value in match.items())
    ]
    assert len(found) == 1, f"esperava uma linha {match}, achei {len(found)}"
    return found[0]


# -- the world, not the report ----------------------------------------------


def test_the_observation_names_the_entity_the_report_could_only_count():
    env = _Env(STARVED_WORLD)
    observation = curriculum_runner.repair_observation(env, _Journal())
    deficits = detect_deficits(observation)
    starved = [row for row in deficits if row.kind == "fuel_starved"]
    assert starved, "o mundo vivo nao produziu o deficit de combustivel"
    assert starved[0].entities == ("u1",), (
        "a observacao veio de metricas agregadas: sem no, sem entidade nomeada"
    )
    assert starved[0].category == "extraction"


def test_a_machine_report_is_read_from_the_diagnostics_the_stage_wrote():
    env = _Env(STARVED_WORLD)
    journal = _Journal(
        metrics={
            "electronic_circuit_diagnostics": {
                "circuit_assembler": {
                    "recipe": "electronic-circuit",
                    "status_after": "working",
                    "output_before": 5,
                    "output_after": 5,
                    "inputs_after": {"iron_plate": 3},
                },
                "power": {"circuit_pole_network_id": 9020.0},
            }
        }
    )
    observation = curriculum_runner.repair_observation(env, journal)
    report = observation.report("circuit_assembler")
    assert report is not None
    assert report.output_count == 0.0
    assert report.output_metric == (
        "electronic_circuit_diagnostics.circuit_assembler.output_after"
    )


# -- the cycle ---------------------------------------------------------------


def test_a_failed_stage_is_repaired_and_retried_once(ledger):
    env = _Env(STARVED_WORLD)
    executor = _Executor(env, heals_into=HEALED_WORLD)
    journal = _Journal()
    calls = []

    def attempt():
        calls.append(1)
        return len(calls) > 1

    outcome = curriculum_runner.run_stage_with_repair(
        attempt,
        stage="Logistic science",
        executor=executor,
        env=env,
        journal=journal,
        budget=curriculum_runner.RepairBudget(),
    )

    assert outcome is True, "o estagio nao foi retentado depois do reparo"
    assert len(calls) == 2
    row = only(rows(ledger), action_key=FUEL_ARM, executed=True)
    assert row["symptom"] == "fuel_starved:no_fuel"
    assert row["targets"] == ["u1"]
    assert row["outcome"]["prediction"] == {
        "metric": "fuel_starved_entities",
        "direction": DIRECTION_DECREASE,
    }
    assert row["outcome"]["before"] == 1.0
    assert row["outcome"]["after"] == 0.0
    assert row["outcome"]["verdict"] == "held"
    assert row["outcome"]["reward"] == 1.0
    assert row["stage"] == "Logistic science"
    assert row["generation"] == 7
    assert executor.purposes == ["operation"], (
        "alimentar maquina na mao e logistica manual, nao infraestrutura"
    )


def test_a_machine_with_no_power_gets_the_pole_the_wire_reach_never_placed(ledger):
    env = _Env(POWERLESS_WORLD)
    executor = _Executor(env, heals_into=POWERED_WORLD)
    calls = []

    def attempt():
        calls.append(1)
        return len(calls) > 1

    outcome = curriculum_runner.run_stage_with_repair(
        attempt,
        stage="Electronic circuits",
        executor=executor,
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )

    assert outcome is True
    row = only(rows(ledger), action_key=POWER_ARM, executed=True)
    assert row["outcome"]["before"] == 1.0
    assert row["outcome"]["after"] == 0.0
    assert row["outcome"]["verdict"] == "held"
    assert executor.purposes == ["infrastructure"], (
        "um poste fica de pe: e investimento, nao carregamento manual"
    )


def test_a_repair_nobody_executed_is_a_row_with_a_reason_and_no_reward(ledger):
    # The steam path this world never had is a deficit no tool here answers.
    # It is repaired by nobody, and the world still changes underneath it
    # because the refuel beside it worked: a step scored against a change it
    # did not cause would enter the ledger as a repair that held.
    env = _Env(STARVED_WORLD)
    executor = _Executor(env, heals_into=HEALED_WORLD)
    calls = []

    curriculum_runner.run_stage_with_repair(
        lambda: bool(calls.append(1)) or len(calls) > 1,
        stage="Logistic science",
        executor=executor,
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )

    written = rows(ledger)
    refused = only(written, symptom="steam_path_dead:steam_path_broken")
    assert refused["executed"] is False
    assert refused["not_executed"] == curriculum_runner.REPAIR_NO_BINDING
    assert refused["outcome"]["verdict"] == "unmeasured", (
        "um reparo sem medida nao pode ser pontuado contra a mudanca de outro"
    )
    assert refused["outcome"]["reward"] is None
    assert refused["outcome"]["before"] is None
    assert refused["outcome"]["after"] is None

    history = curriculum_runner.read_repair_history(ledger)
    assert set(history) == {"fuel_starved:no_fuel"}, (
        "o historico aprendeu com uma acao que ninguem executou"
    )
    assert history["fuel_starved:no_fuel"][FUEL_ARM] == [1.0]


def test_a_declared_refusal_is_recorded_as_a_result(ledger):
    journal = _Journal(
        metrics={
            "electronic_circuit_diagnostics": {
                "circuit_assembler": {
                    "recipe": "electronic-circuit",
                    "status_after": "working",
                    "output_before": 5,
                    "output_after": 5,
                    "inputs_after": {"iron_plate": 3},
                },
            }
        }
    )
    env = _Env(HEALED_WORLD)
    executor = _Executor(env)

    curriculum_runner.run_stage_with_repair(
        lambda: False,
        stage="Electronic circuits",
        executor=executor,
        env=env,
        journal=journal,
        budget=curriculum_runner.RepairBudget(),
    )

    row = only(rows(ledger), symptom="machine_stalled:window_too_short")
    assert row["refusal"] == "window_too_short_to_judge"
    assert row["executed"] is False
    assert not executor.codes, "uma recusa nao pode ter tocado o mundo"


# -- the budget --------------------------------------------------------------


def test_one_stage_is_retried_once_however_often_it_fails(ledger):
    env = _Env(STARVED_WORLD)
    executor = _Executor(env)
    budget = curriculum_runner.RepairBudget()
    calls = []

    def attempt():
        calls.append(1)
        return False

    for _ in range(4):
        curriculum_runner.run_stage_with_repair(
            attempt,
            stage="Logistic science",
            executor=executor,
            env=env,
            journal=_Journal(),
            budget=budget,
        )
    assert len(calls) == 4 + curriculum_runner.REPAIR_ATTEMPTS_PER_STAGE, (
        "um estagio gasta uma retentativa por geracao, nao uma por falha: "
        f"{len(calls)} execucoes para 4 chamadas"
    )
    spent = [
        row
        for row in rows(ledger)
        if row.get("refusal") == curriculum_runner.REPAIR_ATTEMPT_BUDGET_SPENT
    ]
    assert len(spent) == 3, "o limite por estagio tem de ser registrado"


def test_one_generation_stops_repairing_when_its_budget_is_spent(ledger):
    env = _Env(STARVED_WORLD)
    executor = _Executor(env)
    budget = curriculum_runner.RepairBudget()
    calls = []

    def attempt():
        calls.append(1)
        return False

    stages = ["one", "two", "three", "four", "five"]
    for stage in stages:
        curriculum_runner.run_stage_with_repair(
            attempt,
            stage=stage,
            executor=executor,
            env=env,
            journal=_Journal(),
            budget=budget,
        )

    assert budget.spent == curriculum_runner.REPAIR_ATTEMPTS_PER_GENERATION
    assert len(calls) == len(stages) + curriculum_runner.REPAIR_ATTEMPTS_PER_GENERATION
    spent = [
        row
        for row in rows(ledger)
        if row.get("refusal") == curriculum_runner.REPAIR_ATTEMPT_BUDGET_SPENT
    ]
    assert len(spent) == 2, "o limite gasto tem de ser registrado, nao silenciado"


# -- the ledger decides ------------------------------------------------------


def test_with_an_empty_ledger_the_fixed_rule_decides_and_says_so(ledger):
    env = _Env(BUFFERED_WORLD)
    executor = _Executor(env)

    curriculum_runner.run_stage_with_repair(
        lambda: False,
        stage="Belt-fed smelting",
        executor=executor,
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )

    row = only(rows(ledger), symptom="producer_output_unprocessed:output_buffered_not_processed")
    assert row["action_key"] == BUILD_ARM
    assert row["choice_basis"] == curriculum_runner.REPAIR_CHOICE_FIXED_RULE


def test_a_scored_ledger_overrules_the_fixed_rule(ledger):
    symptom = "producer_output_unprocessed:output_buffered_not_processed"
    for arm, reward in ((BUILD_ARM, 0.0), (REROUTE_ARM, 1.0)):
        curriculum_runner.append_jsonl(
            ledger,
            {
                "symptom": symptom,
                "action_key": arm,
                "executed": True,
                "outcome": {"reward": reward, "verdict": "x"},
            },
        )
    env = _Env(BUFFERED_WORLD)
    executor = _Executor(env)

    curriculum_runner.run_stage_with_repair(
        lambda: False,
        stage="Belt-fed smelting",
        executor=executor,
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )

    row = only(
        [entry for entry in rows(ledger) if entry.get("stage")],
        symptom=symptom,
    )
    assert row["action_key"] == REROUTE_ARM, (
        "o historico mediu que o outro braco falhou e a escolha nao mudou"
    )
    assert row["choice_basis"] == curriculum_runner.REPAIR_CHOICE_HISTORY


def test_the_history_survives_a_line_a_writer_had_not_finished(ledger):
    curriculum_runner.append_jsonl(
        ledger,
        {
            "symptom": "fuel_starved:no_fuel",
            "action_key": FUEL_ARM,
            "executed": True,
            "outcome": {"reward": 1.0},
        },
    )
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"symptom": "fuel_starved:no_f')

    history = curriculum_runner.read_repair_history(ledger)
    assert history == {"fuel_starved:no_fuel": {FUEL_ARM: [1.0]}}


# -- what must never happen --------------------------------------------------


def test_nothing_that_feeds_a_live_chain_is_ever_torn_down():
    env = _Env(BUFFERED_WORLD)
    observation = curriculum_runner.repair_observation(env, _Journal())
    deficit = Deficit(kind="producer_chain_reaches_no_sink", source="graph_topology", severity=1.0)
    step = RepairStep(
        proposal=RepairProposal(
            diagnosis=Diagnosis(deficit=deficit, cause="chain_reaches_no_sink", basis="graph_topology"),
        ),
        action=RepairAction(
            tool=TOOL_REBUILD,
            intent="reroute_producer_logistics",
            prediction=Prediction("producers_reaching_processor", DIRECTION_DECREASE),
            # The drill fills a chest, and a chest is a chain: the graph says
            # so, which is exactly what makes this demolition unprovable.
            removes=("u1",),
        ),
        rank=0,
        rank_basis="no_prerequisite",
    )

    ran, reason, _ = curriculum_runner.execute_repair_step(
        step,
        observation=observation,
        executor=_Executor(env),
        env=env,
    )
    assert ran is False
    assert reason == curriculum_runner.REPAIR_REMOVAL_UNPROVEN


def test_a_world_that_cannot_be_read_repairs_nothing(ledger):
    class _BlindEnv(_Env):
        def __init__(self):
            super().__init__(STARVED_WORLD)
            self.instance.namespace._save_entity_state = self._refuse

        @staticmethod
        def _refuse(**_kwargs):
            raise RuntimeError("rcon down")

    env = _BlindEnv()
    calls = []
    curriculum_runner.run_stage_with_repair(
        lambda: bool(calls.append(1)) and False,
        stage="Logistic science",
        executor=_Executor(env),
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )
    assert len(calls) == 1, "um mundo nao lido nao autoriza retentativa"
    assert only(rows(ledger), refusal=curriculum_runner.REPAIR_WORLD_UNREAD)


def test_a_rejected_repair_is_not_a_repair(ledger):
    env = _Env(STARVED_WORLD)
    executor = _Executor(env, heals_into=HEALED_WORLD, accepts=False)
    calls = []

    curriculum_runner.run_stage_with_repair(
        lambda: bool(calls.append(1)) and False,
        stage="Logistic science",
        executor=executor,
        env=env,
        journal=_Journal(),
        budget=curriculum_runner.RepairBudget(),
    )

    row = only(rows(ledger), action_key=FUEL_ARM)
    assert row["executed"] is False
    assert row["not_executed"] == curriculum_runner.REPAIR_REJECTED
    assert row["outcome"]["reward"] is None
    assert len(calls) == 1, "nada mudou no mundo, nada a retentar"


# -- the scripts -------------------------------------------------------------


def test_the_scripts_are_valid_python_and_do_not_trip_the_failure_heuristic():
    targets = curriculum_runner.repair_entity_targets(
        curriculum_runner.repair_observation(_Env(STARVED_WORLD), _Journal()),
        ("u1",),
    )
    refuel = curriculum_runner.repair_refuel_code(
        ((10.0, 11.5, 200),),
        targets,
        dose=16,
    )
    tap = curriculum_runner.repair_power_tap_code(targets)
    for code in (refuel, tap):
        ast.parse(code)
        lowered = code.lower()
        assert "error" not in lowered, (
            "o FLE marca a acao como falha pela substring impressa "
            "(environment.py:451)"
        )
        assert "exception: " not in lowered
    assert "Prototype.BurnerMiningDrill" in refuel
    assert "(10.0,10.0)" in tap


#: What the FLE namespace hands every script. Anything else a repair script
#: reads would have to have been bound by a stage that ran before it -- and a
#: repair runs precisely when a stage did not finish.
FLE_API = frozenset(
    {
        "Direction",
        "Position",
        "Prototype",
        "extract_item",
        "get_entity",
        "insert_item",
        "inspect_inventory",
        "move_to",
        "place_entity_next_to",
    }
)


def test_a_repair_script_reads_no_name_a_stage_left_behind():
    """Generation 44 died on ``NameError: name 'chest' is not defined``.

    A repair script is the one script with no stage before it it may count
    on: it runs because a stage did not finish, and the names that stage
    would have bound may not exist. So it reads the engine and itself, and
    nothing else.
    """
    targets = (("u1", "BurnerMiningDrill", 10.0, 10.0),)
    for code in (
        curriculum_runner.repair_refuel_code(((1.0, 2.0, 30),), targets, dose=20),
        curriculum_runner.repair_power_tap_code(targets),
    ):
        tree = ast.parse(code)
        bound = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        bound |= {
            handler.name
            for handler in ast.walk(tree)
            if isinstance(handler, ast.ExceptHandler) and handler.name
        }
        read = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        outside = sorted(
            name
            for name in read - bound - FLE_API
            if not hasattr(builtins, name)
        )
        assert not outside, (
            f"o script de reparo le nomes que ninguem vinculou: {outside}"
        )
