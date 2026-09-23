"""The survey has to read the ground, not only what stands on it.

``planning.placement`` has priced resource tiles since ``e4c40f4``, and the
price never applied to anything: ``survey_world`` asked
``_save_entity_state`` with ``resource_entities=False`` and left
``WorldSurvey.resources`` at None, which the layer correctly reads as "nobody
looked under these tiles". Every placement in the curriculum was therefore
decided as if ore and bare dirt were the same ground.

Ore is a separate query. Measured over RCON on the live instance on
2026-09-23, ``find_entities_filtered{area={{-500,-500},{500,500}},
type="resource"}`` answers 6702 rows in 1.86 s and 345 kB; the same sweep at
radius 320 answers the 2562 rows of the patches this curriculum mines. The
window is the fact that matters here: ``surveyed`` is a claim about where the
read looked, and a tile outside it is unknown rather than bare.

Two failure modes this file exists to keep out:

``the resource rows must not become obstacles``
    ``blocked_tiles`` treats every non-``character`` entity as an obstacle,
    so a resource row reaching ``WorldSurvey.entities`` would mark its whole
    patch taken and refuse the patch entire.

``a read that did not happen is not a world without ore``
    the RCON call is best effort, exactly as ``_runtime_entity_footprints``
    is. When it answers nothing, the survey must say it knows nothing about
    the ground rather than report bare ground everywhere.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.experiments import curriculum_runner

#: Two iron tiles and the drill standing beside them, in the shapes the two
#: reads report: ``_save_entity_state`` for the drill, the resource sweep for
#: the ore.
DRILL: dict[str, Any] = {
    "name": "burner-mining-drill",
    "position": {"x": 19.0, "y": 83.0},
    "direction": 4,
}
ORE_ROWS: list[dict[str, Any]] = [
    {"name": "iron-ore", "position": {"x": 27.5, "y": 81.5}},
    {"name": "iron-ore", "position": {"x": 27.5, "y": 82.5}},
]


class _Rcon:
    """RCON that answers the resource sweep and nothing else.

    The prototype command answers empty, which is the path a runtime that
    reports no footprints already takes, so the only new behaviour under test
    is the resource read.
    """

    def __init__(self, payload: str | None = None) -> None:
        self.commands: list[str] = []
        self._payload = payload

    def send_command(self, command: str) -> str:
        self.commands.append(command)
        if 'type="resource"' in command or "type='resource'" in command:
            if self._payload is None:
                return json.dumps({"resources": ORE_ROWS})
            return self._payload
        return ""


class _Namespace:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self._entities = list(entities)

    def _save_entity_state(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self._entities]


def _env(rcon: _Rcon, entities: list[dict[str, Any]] | None = None) -> Any:
    instance = SimpleNamespace(
        namespace=_Namespace(entities if entities is not None else [DRILL]),
        rcon_client=rcon,
    )
    return SimpleNamespace(unwrapped=SimpleNamespace(instance=instance))


def test_the_survey_asks_the_world_what_is_under_the_tiles() -> None:
    rcon = _Rcon()

    curriculum_runner.survey_world(_env(rcon))

    sweeps = [
        command
        for command in rcon.commands
        if 'type="resource"' in command or "type='resource'" in command
    ]
    assert sweeps, "survey_world nunca perguntou pelo minerio"


def test_the_ore_reaches_the_placement_layer() -> None:
    survey = curriculum_runner.survey_world(_env(_Rcon()))

    assert survey is not None
    assert survey.resources is not None
    assert survey.resources.tiles == {
        GridPoint(27, 81): "iron-ore",
        GridPoint(27, 82): "iron-ore",
    }


def test_the_window_the_read_covered_is_the_window_it_declares() -> None:
    # Both reads have to describe the same box, or ``surveyed`` claims
    # coverage the entity read never had.
    survey = curriculum_runner.survey_world(_env(_Rcon()))

    assert survey is not None
    assert survey.resources is not None
    distance = float(curriculum_runner.WORLD_SURVEY_DISTANCE)
    assert survey.resources.surveyed == (-distance, -distance, distance, distance)


def test_the_ore_never_becomes_an_obstacle() -> None:
    # The drill is the only thing standing; the two ore rows are ground.
    survey = curriculum_runner.survey_world(_env(_Rcon()))

    assert survey is not None
    assert len(survey.entities) == 1
    assert survey.entities[0]["name"] == "burner-mining-drill"

    from factorio_ai_lab.planning.footprints import blocked_tiles

    blocked = blocked_tiles(survey.entities, survey.footprints)
    assert GridPoint(27, 81) not in blocked
    assert GridPoint(27, 82) not in blocked


def test_a_sweep_that_answered_nothing_claims_nothing_about_the_ground() -> None:
    # An unanswered read is not a world without ore. Reporting bare ground
    # here is the absence-is-zero failure the placement layer exists to
    # refuse, and it would price every tile of every patch as free dirt.
    survey = curriculum_runner.survey_world(_env(_Rcon(payload="")))

    assert survey is not None
    assert survey.resources is None


def test_a_sweep_that_answered_rubbish_claims_nothing_about_the_ground() -> None:
    survey = curriculum_runner.survey_world(_env(_Rcon(payload="not json")))

    assert survey is not None
    assert survey.resources is None


def test_an_unrecognised_resource_is_still_ground_and_not_an_obstacle() -> None:
    # The sweep filtered on ``type="resource"``, so every row it returned is
    # ore whatever its name. Matching the rows by name instead would let a
    # resource this codebase never heard of block its own patch.
    payload = json.dumps(
        {"resources": [{"name": "fulgora-scrap", "position": {"x": 27.5, "y": 81.5}}]}
    )
    survey = curriculum_runner.survey_world(_env(_Rcon(payload=payload)))

    assert survey is not None
    assert survey.resources is not None
    assert survey.resources.tiles == {GridPoint(27, 81): "fulgora-scrap"}
    assert len(survey.entities) == 1
