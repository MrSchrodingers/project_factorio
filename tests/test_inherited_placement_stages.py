"""Placement stages have to run in a world that already holds a factory.

Generation 37 was the first promotion since generation 6, so the world now
starts with the ancestral factory standing on it. ``patch_center`` is
deterministic on a fixed map, the placement arms are fixed offsets around it,
and ``place_entity`` answers ``entity already exists at the target position``
on the tiles the ancestor took. Every trial of stage 1 failed on that, no arm
carried a measurement, and the service restarted into the same failure 98
times.

The world below is the inherited factory as RCON reported it on 2026-09-23:
four burner mining drills at y=83, the chests and inserters of their cells,
the belt lane at y=84 and two furnaces.

Two things are measured here and must not be confused. What the trial built
is the drill and the chest the trial itself placed. What the world produced
is the counter ``_get_production_stats`` reports, which counts the inherited
drills as well: the rejected step rewinds it, so every trial read
``before=0.0, after=95.0`` from the ancestor's flow. A reward taken from the
second number is a reward the arm did not earn.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_tiles

CENTRE = (27.0, 83.0)
DRILL = "burner-mining-drill"
CHEST = "wooden-chest"

#: Iron patch bounding box measured live; all 624 of its tiles carry ore.
PATCH_BOUNDS = (15.5, 70.5, 38.5, 95.5)

#: What the inherited factory produces while a step runs, as measured: the
#: world counter moved 0 -> 95 in seven of eight trials of generation 37,
#: including trials whose placement was refused.
INHERITED_FLOW = 95.0

#: 136 game seconds at 60 ticks per second.
STEP_TICKS = 8160

_POSITION = re.compile(r"Position\(x=(-?[\d.]+), y=(-?[\d.]+)\)")
_FIND_CHEST = re.compile(r"find_entity\('wooden-chest',\{x=(-?[\d.]+),y=(-?[\d.]+)\}\)")


def _entity(name: str, x: float, y: float, direction: int = 0) -> dict[str, Any]:
    return {"name": name, "position": {"x": x, "y": y}, "direction": direction}


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
    _entity("transport-belt", 21.5, 84.5, 4),
    _entity("transport-belt", 22.5, 84.5, 4),
    _entity("transport-belt", 23.5, 84.5, 4),
    _entity("transport-belt", 24.5, 84.5, 8),
    _entity("transport-belt", 24.5, 85.5, 8),
    _entity(CHEST, 27.5, 84.5),
    _entity(CHEST, 32.5, 84.5),
    _entity("burner-inserter", 25.5, 86.5, 12),
    _entity("transport-belt", 24.5, 86.5, 4),
    _entity("burner-inserter", 27.5, 86.5, 12),
    _entity(CHEST, 26.5, 86.5),
    _entity("stone-furnace", 29.0, 87.0),
)


def _cell_yield(position: tuple[float, float]) -> float:
    """Ore a trial cell drops into its chest, by where the cell stands.

    The far southeast corner is the productive one here, and it is also the
    least compact arm. That ordering is the instrument: a reward read off the
    inherited world counter is the same number for every arm, so the tie is
    broken by compactness and the nearest arm wins. Only a reward measured on
    the cell the trial built can promote the far one.
    """
    return 5.0 if position[1] > 90.0 else 1.0


class _FakeRcon:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.commands: list[str] = []

    def send_command(self, command: str) -> str:
        self.commands.append(command)
        if "teleport" in command:
            return f"{CENTRE[0]},{CENTRE[1]}"
        found = _FIND_CHEST.search(command)
        if found is not None:
            return str(self.world.chest_contents(float(found[1]), float(found[2])))
        # No prototype payload: footprints fall back to the static table,
        # which is the path a runtime that answers nothing already takes.
        return ""


class _FakeWorld:
    """The live world, as far as these stages can observe it."""

    def __init__(self) -> None:
        self.entities: list[dict[str, Any]] = [dict(e) for e in INHERITED_WORLD]
        self.contents: dict[tuple[float, float], float] = {}
        self.iron_produced = 0.0
        self.ticks = 1000

    def snapshot(self) -> tuple[Any, ...]:
        return (
            [dict(e) for e in self.entities],
            dict(self.contents),
            self.iron_produced,
            self.ticks,
        )

    def restore(self, snapshot: tuple[Any, ...]) -> None:
        entities, contents, produced, ticks = snapshot
        self.entities = [dict(e) for e in entities]
        self.contents = dict(contents)
        self.iron_produced = produced
        self.ticks = ticks

    def taken(self) -> set[Any]:
        return blocked_tiles(self.entities)

    def chest_contents(self, x: float, y: float) -> float:
        return self.contents.get((x, y), 0.0)

    def place_cell(self, position: tuple[float, float]) -> bool:
        """Place a drill and the chest under it, as the engine would.

        Answers False exactly where the engine refuses: tiles already
        occupied. That is the substring FLE turns into a failed step.
        """
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
        self.contents[(chest_x, chest_y)] = _cell_yield(position)
        return True


class _FakeNamespace:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.player_location = None

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self.world.entities]

    def _get_production_stats(self) -> dict[str, dict[str, float]]:
        return {"output": {"iron-ore": self.world.iron_produced}}


class _FakeInstance:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.namespace = _FakeNamespace(world)
        self.namespaces = [self.namespace]
        self.rcon_client = _FakeRcon(world)

    def get_elapsed_ticks(self) -> int:
        return self.world.ticks


class _FakeEnv:
    def __init__(self, world: _FakeWorld) -> None:
        self.world = world
        self.unwrapped = SimpleNamespace(instance=_FakeInstance(world))


class _FakeStep:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"error_occurred": False}
        self.candidate_game_state: Any = object()
        self.reward = 12.0
        self.accepted = False


class _FakeExecutor:
    """Stand-in for the transactional executor over the fake world.

    Reproduces the three behaviours these stages depend on: a placement on
    taken tiles comes back marked failed, the game clock and the world
    production counter advance while the step runs even when the placement
    failed, and a rejected step is rolled back to the checkpoint.
    """

    def __init__(self, env: _FakeEnv) -> None:
        self.world = env.world
        self.scripts: list[str] = []
        self.refused: list[tuple[float, float]] = []
        self.placed: list[tuple[float, float]] = []

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
        self.world.iron_produced += INHERITED_FLOW
        step = _FakeStep()
        found = _POSITION.search(code)
        if found is not None and "place_entity(" in code:
            target = (float(found[1]), float(found[2]))
            if self.world.place_cell(target):
                self.placed.append(target)
            else:
                self.refused.append(target)
                step.info = {"error_occurred": True}
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


def _learn() -> tuple[str, _FakeExecutor, _FakeJournal, _FakeEnv]:
    env = _FakeEnv(_FakeWorld())
    executor = _FakeExecutor(env)
    journal = _FakeJournal()
    best = curriculum_runner.stage_online_learning(
        executor,
        env,
        journal,
        center=CENTRE,
        episodes=8,
        settle_seconds=8,
        exploration=2.0,
        radius_scale=1.0,
    )
    return best, executor, journal, env


def test_no_trial_is_placed_on_tiles_the_inherited_factory_occupies() -> None:
    _, executor, _, _ = _learn()

    assert executor.refused == [], (
        f"a etapa construiu sobre a fabrica herdada em {executor.refused}"
    )
    assert len(executor.placed) == 8


def test_every_arm_is_trialled_after_being_moved_off_the_taken_tiles() -> None:
    _, _, journal, _ = _learn()
    history = journal.state["online_learning"]["history"]

    assert len(history) == 8
    assert all(row["valid"] for row in history), (
        f"episodios invalidos: {[row for row in history if not row['valid']]}"
    )
    assert {row["arm"] for row in history} == set(curriculum_runner.PLACEMENT_ARMS)


def test_each_trial_records_the_placement_it_was_given() -> None:
    _, _, journal, _ = _learn()
    plans = journal.state["online_learning"]["placement_plans"]

    assert set(plans) == set(curriculum_runner.PLACEMENT_ARMS)
    assert plans["east_near"]["outcome"] == "build"
    # A drill of the ancestor stands on the east arm; the plan moves the
    # trial off it instead of asking the engine to build on top.
    assert plans["east_near"]["shift"] == {"x": -2, "y": 0}
    assert plans["north_near"]["shift"] == {"x": 0, "y": 0}


def test_the_reward_measures_the_cell_the_trial_built() -> None:
    _, _, journal, _ = _learn()
    history = journal.state["online_learning"]["history"]

    for row in history:
        assert row["output"] in (1.0, 5.0), (
            f"recompensa {row['output']} nao veio da celula do trial: {row}"
        )
        assert row["reward"] == row["output"]
        # The world counter moved by the inherited factory's flow in the same
        # window. Recording it is fine; rewarding it is not.
        assert row["world_output_after"] >= INHERITED_FLOW


def test_the_promoted_arm_is_the_one_whose_own_cell_produced_most() -> None:
    best, _, journal, _ = _learn()

    # north_near is the most compact arm and measures 1.0 on its own cell.
    # Promoting it would mean the rewards were all equal, which is what a
    # reward read off the inherited world counter produces.
    assert best == "southeast_edge"
    assert journal.state["metrics"]["placement_best_arm"] == "southeast_edge"
    assert journal.state["online_learning"]["status"] == "learned"


def test_the_same_world_plans_the_same_placements_twice() -> None:
    first, first_executor, first_journal, _ = _learn()
    second, second_executor, second_journal, _ = _learn()

    assert first == second
    assert first_executor.placed == second_executor.placed
    assert [
        (row["arm"], row["placement"]["position"], row["output"])
        for row in first_journal.state["online_learning"]["history"]
    ] == [
        (row["arm"], row["placement"]["position"], row["output"])
        for row in second_journal.state["online_learning"]["history"]
    ]


def test_the_promotion_commits_the_cell_it_measured() -> None:
    env = _FakeEnv(_FakeWorld())
    executor = _FakeExecutor(env)
    journal = _FakeJournal()

    curriculum_runner.stage_scale_mining(
        executor,
        env,
        journal,
        center=CENTRE,
        best_arm="east_near",
        settle_seconds=8,
        radius_scale=1.0,
    )

    assert executor.refused == []
    assert executor.placed == [(30.0, 83.0)], (
        "a promocao nao usou o mesmo tile que o trial mediu"
    )
    assert journal.state["metrics"]["scaled_iron_output"] > 0
    assert journal.completed


def test_the_promotion_records_where_the_cell_actually_went() -> None:
    env = _FakeEnv(_FakeWorld())
    journal = _FakeJournal()

    curriculum_runner.stage_scale_mining(
        _FakeExecutor(env),
        env,
        journal,
        center=CENTRE,
        best_arm="east_near",
        settle_seconds=8,
        radius_scale=1.0,
    )

    placement = journal.state["metrics"]["scaled_placement"]
    assert placement["outcome"] == "build"
    assert placement["position"] == {"x": 30.0, "y": 83.0}
    assert placement["shift"] == {"x": -2, "y": 0}
