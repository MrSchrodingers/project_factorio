"""The baseline stage must recognise the factory it inherited, not rebuild it.

Lifelong inheritance restores the promoted factory before the curriculum runs,
and ``patch_center`` is deterministic for a fixed map, so every heir computes
the same tiles its ancestor already built on. ``place_entity`` answers
``entity already exists at the target position`` there, FLE marks the step
failed on its substring heuristic, and stage 0 raises. That stalled the
evolution loop for 46 consecutive generations after the first promotion since
generation 6 put a working drill on the patch centre.

The geometry here is the geometry measured on the live world: the iron patch
bounding box (15.5, 70.5)-(38.5, 95.5) puts ``patch_center`` at (27.0, 83.0),
and the inherited factory holds a working burner mining drill at exactly
(27.0, 83.0) with a wooden chest at (27.5, 84.5).

Recognising the cell must not swing the other way either. A drill that arrived
with the inheritance is not something this genome built, so the stage records
its flow apart from ``baseline_iron_rate_per_s``, the key that
``survival.fitness_from_research`` credits as endogenous iron-ore output of
this generation.

Recognising it is also not enough. The cell is a drill and the container it
drops into, and the construction path binds both under names the stages
downstream read: generation 44 ran thirteen stages and died on ``NameError:
name 'chest' is not defined`` because the adoption path bound only the drill.
The two paths leave the same names behind, and the container is found on the
tile the construction path would have put it on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner

#: Centre of the iron patch as measured on the live world.
CENTRE = (27.0, 83.0)

#: The drill generation 37 left on the patch centre, in the shape
#: ``_save_entity_state`` reports.
INHERITED_DRILL: dict[str, Any] = {
    "name": "burner-mining-drill",
    "position": {"x": 27.0, "y": 83.0},
    "direction": 4,
}

INHERITED_CHEST: dict[str, Any] = {
    "name": "wooden-chest",
    "position": {"x": 27.5, "y": 84.5},
    "direction": 0,
}

#: Another inherited drill, far enough that the baseline tiles stay free.
DISTANT_DRILL: dict[str, Any] = {
    "name": "burner-mining-drill",
    "position": {"x": 19.0, "y": 83.0},
    "direction": 4,
}

#: 136 game seconds at 60 ticks per second: the window generation 37 measured.
STEP_TICKS = 8160


class _FakeRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def send_command(self, command: str) -> str:
        self.commands.append(command)
        if "teleport" in command:
            return f"{CENTRE[0]},{CENTRE[1]}"
        # No prototype payload: footprints fall back to the static table,
        # which is the path a runtime that answers nothing already takes.
        return ""


class _FakeNamespace:
    def __init__(self, entities: list[dict[str, Any]], iron_output: float) -> None:
        self._entities = list(entities)
        self._iron_output = float(iron_output)
        self.player_location = None
        self.iron = SimpleNamespace(x=15.5, y=70.5)
        self.patch = SimpleNamespace(
            size=6240000,
            bounding_box=SimpleNamespace(
                left_top=SimpleNamespace(x=15.5, y=70.5),
                right_bottom=SimpleNamespace(x=38.5, y=95.5),
            ),
        )

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self._entities]

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": {"iron-ore": self._iron_output}}


class _FakeInstance:
    def __init__(self, namespace: _FakeNamespace) -> None:
        self.namespace = namespace
        self.namespaces = [namespace]
        self.rcon_client = _FakeRcon()
        self.ticks = 1000

    def get_elapsed_ticks(self) -> int:
        return self.ticks


class _FakeEnv:
    def __init__(self, instance: _FakeInstance) -> None:
        self.unwrapped = SimpleNamespace(instance=instance)


class _FakeStep:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False}
        self.candidate_game_state: Any = object()
        self.reward = 45.0
        self.accepted = False


class _FakeExecutor:
    """Stand-in reproducing the two behaviours this stage depends on.

    The game clock advances while a step runs, and a ``place_entity`` aimed at
    an occupied tile comes back marked failed, which is what server.lua does
    at ``entity already exists at the target position``.
    """

    def __init__(self, instance: _FakeInstance, *, tiles_occupied: bool) -> None:
        self.instance = instance
        self.tiles_occupied = tiles_occupied
        self.scripts: list[str] = []

    def execute(
        self,
        code: str,
        *,
        accept: Any,
        use_checkpoint_for_action: bool = True,
        purpose: str = "operation",
    ) -> _FakeStep:
        self.scripts.append(code)
        self.instance.ticks += STEP_TICKS
        step = _FakeStep()
        if "place_entity(" in code and self.tiles_occupied:
            step.info = {"error_occurred": True}
        step.accepted = bool(accept(step))
        return step


class _FakeJournal:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "metrics": {},
            "world": {},
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


def _world(
    entities: list[dict[str, Any]],
    *,
    iron_output: float = 34.0,
) -> _FakeEnv:
    return _FakeEnv(_FakeInstance(_FakeNamespace(entities, iron_output)))


def _run_stage(
    env: _FakeEnv,
    *,
    tiles_occupied: bool,
) -> tuple[_FakeExecutor, _FakeJournal]:
    executor = _FakeExecutor(env.unwrapped.instance, tiles_occupied=tiles_occupied)
    journal = _FakeJournal()
    curriculum_runner.stage_baseline(executor, env, journal, settle_seconds=16)
    return executor, journal


@pytest.fixture(autouse=True)
def _offline_lesson(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lesson synthesis calls an LLM router; what is under test here is the
    # placement decision, not the notebook prose.
    monkeypatch.setattr(
        curriculum_runner,
        "synthesize_lesson",
        lambda **_kwargs: {
            "lesson": "stand-in",
            "next_hypothesis": "stand-in",
            "evidence_keys": ["iron_output"],
        },
    )


def _adopted(entities: list[dict[str, Any]]) -> Any:
    """The adoption plan for a world, or None when there is nothing to adopt."""
    return curriculum_runner.inherited_mining_cell(
        curriculum_runner.survey_world(_world(entities)),
        CENTRE,
    )


def test_the_survey_finds_the_drill_the_ancestor_left_on_the_baseline_tiles() -> None:
    found = _adopted([INHERITED_DRILL, INHERITED_CHEST])
    assert found is not None
    assert found.position == CENTRE


def test_the_survey_answers_none_when_nothing_was_built() -> None:
    assert _adopted([]) is None


def test_a_drill_clear_of_the_baseline_tiles_is_not_adopted() -> None:
    # Placing at the centre still succeeds, so there is nothing to adopt and
    # the stage has to keep building.
    assert _adopted([DISTANT_DRILL]) is None


def test_the_survey_reads_tiles_not_the_centre_point() -> None:
    # Guard the instrument. A 2x2 drill at (28.0, 83.0) shares no centre with
    # the baseline cell but shares two of its four tiles, so place_entity is
    # still refused. A centre-equality check would miss it and stall again.
    overlapping = {
        "name": "burner-mining-drill",
        "position": {"x": 28.0, "y": 83.0},
        "direction": 4,
    }
    found = _adopted([overlapping])
    assert found is not None
    assert found.position == (28.0, 83.0)


def test_the_container_of_the_adopted_cell_is_found_on_the_tile_it_stands_on() -> None:
    found = _adopted([INHERITED_DRILL, INHERITED_CHEST])
    assert found is not None
    container = curriculum_runner.inherited_cell_container(
        curriculum_runner.survey_world(_world([INHERITED_DRILL, INHERITED_CHEST])),
        found.tiles,
    )
    assert container == ((27.5, 84.5), "wooden-chest")


def test_a_cell_whose_container_is_gone_reports_no_container() -> None:
    # The drill is standing and nothing receives its ore. That is a reading
    # about the world, not a container measured to be empty, and the stage
    # binds the name to None rather than leaving it undefined.
    found = _adopted([INHERITED_DRILL])
    assert found is not None
    assert (
        curriculum_runner.inherited_cell_container(
            curriculum_runner.survey_world(_world([INHERITED_DRILL])),
            found.tiles,
        )
        is None
    )


def test_the_neighbouring_cell_container_is_not_taken_for_this_one() -> None:
    # The inherited world holds a second mining cell at (32, 83) with its own
    # chest at (32.5, 84.5). Reading that one would hand stage 13 the wrong
    # buffer and credit this cell with the ore the other one mined.
    neighbour = {
        "name": "wooden-chest",
        "position": {"x": 32.5, "y": 84.5},
        "direction": 0,
    }
    found = _adopted([INHERITED_DRILL, neighbour])
    assert found is not None
    assert (
        curriculum_runner.inherited_cell_container(
            curriculum_runner.survey_world(_world([INHERITED_DRILL, neighbour])),
            found.tiles,
        )
        is None
    )


def test_an_inherited_cell_is_not_rebuilt() -> None:
    executor, journal = _run_stage(
        _world([INHERITED_DRILL, INHERITED_CHEST]),
        tiles_occupied=True,
    )

    building = [script for script in executor.scripts if "place_entity(" in script]
    assert building == [], (
        f"o estagio tentou construir sobre a fabrica herdada: {building}"
    )
    assert journal.completed, "o estagio herdado nao foi concluido"
    assert journal.state["metrics"]["baseline_cell_origin"] == "inherited"


def test_the_commissioned_cell_binds_the_container_for_the_stages_after_it() -> None:
    """The name generation 44 died on, bound on the path that killed it."""
    executor, journal = _run_stage(
        _world([INHERITED_DRILL, INHERITED_CHEST]),
        tiles_occupied=True,
    )
    commissioning = executor.scripts[-1]

    assert "chest = get_entity(" in commissioning
    assert "Position(x=27.5, y=84.5)" in commissioning
    assert journal.state["metrics"]["inherited_baseline_container"] == {
        "x": 27.5,
        "y": 84.5,
        "name": "wooden-chest",
    }


def test_a_commissioned_cell_with_no_container_binds_the_name_anyway() -> None:
    executor, journal = _run_stage(_world([INHERITED_DRILL]), tiles_occupied=True)
    commissioning = executor.scripts[-1]

    assert "chest = None" in commissioning
    assert journal.state["metrics"]["inherited_baseline_container"] is None


def test_an_empty_world_is_still_built_from_scratch() -> None:
    executor, journal = _run_stage(_world([]), tiles_occupied=False)

    assert any("place_entity(" in script for script in executor.scripts), (
        "sem heranca o estagio precisa continuar construindo a celula"
    )
    assert journal.state["metrics"]["baseline_cell_origin"] == "built"
    assert journal.state["metrics"]["baseline_iron_rate_per_s"] == pytest.approx(0.25)


def test_inherited_flow_is_not_credited_as_this_generation_output() -> None:
    _, journal = _run_stage(
        _world([INHERITED_DRILL, INHERITED_CHEST]),
        tiles_occupied=True,
    )
    metrics = journal.state["metrics"]

    # survival.fitness_from_research reads baseline_iron_rate_per_s and files
    # it as endogenous output of this genome. An inherited factory must not
    # reach that key.
    assert "baseline_iron_rate_per_s" not in metrics, (
        "fluxo herdado creditado como producao endogena desta geracao"
    )
    assert "baseline_iron_output" not in metrics
    assert metrics["inherited_iron_rate_per_s"] == pytest.approx(0.25)
    assert metrics["inherited_iron_duration_source"] == "observed_game_ticks"


def test_the_stage_returns_the_patch_centre_either_way() -> None:
    # Every later stage anchors on the centre this one returns.
    for entities, occupied in (([INHERITED_DRILL], True), ([], False)):
        env = _world(entities)
        executor = _FakeExecutor(env.unwrapped.instance, tiles_occupied=occupied)
        _, centre = curriculum_runner.stage_baseline(
            executor,
            env,
            _FakeJournal(),
            settle_seconds=16,
        )
        assert centre == CENTRE
