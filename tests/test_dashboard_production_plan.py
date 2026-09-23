"""The panel has to plan against a catalog it could actually execute.

Three defects are pinned here, all of them measured on the live box before
this file existed.

First, the plan came out of ``RuntimeFactorioCatalog.planner()``. That planner
reads neither ``enabled`` nor the technology that unlocks a recipe, never asks
whether a machine exists to run the recipe, and never looks at what the world
already holds. With 3 of 196 technologies researched that decides almost the
whole graph, so a recipe nobody can run was served as the next thing to build.

Second, the plan call sat outside the ``try`` that guarded the catalog choice.
A target the planner refuses raised inside the HTTP handler instead of being
reported as a refusal.

Third, the generation report names why a machine produced nothing
(``stall_cause``) beside the status, the energy and the electric network the
machine answered with, and the panel dropped all of it. "Output 0" reached the
screen with no way to tell a machine with no power from one whose measurement
window was too short.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from factorio_ai_lab.dashboard import app as dashboard_app
from factorio_ai_lab.dashboard.state import DashboardState

PLAN_URL = "/api/production-plan"
DIAGNOSTICS_URL = "/api/machine-diagnostics"


def _recipe(
    name: str,
    *,
    energy: float,
    category: str,
    ingredients: dict[str, float],
    output: float = 1.0,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "energy": energy,
        "categories": [category],
        "ingredients": [
            {"name": item, "type": "item", "amount": amount}
            for item, amount in ingredients.items()
        ],
        "products": [{"name": name, "type": "item", "amount": output}],
        "enabled": enabled,
        "enabled_by_default": enabled,
    }


def _catalog_payload(*, automation_researched: bool = False) -> dict[str, Any]:
    """A live prototype payload shaped like the one RCON returns.

    ``assembling-machine-1`` is the machine the crafting steps need and its
    own recipe is locked behind ``automation``, which is the state the live
    box is in: the panel proposed it as craftable anyway.
    """
    return {
        "connected": True,
        "factorio_version": "test",
        "recipes": [
            _recipe(
                "electronic-circuit",
                energy=0.5,
                category="crafting",
                ingredients={"iron-plate": 1.0, "copper-cable": 3.0},
            ),
            _recipe(
                "copper-cable",
                energy=0.5,
                category="crafting",
                ingredients={"copper-plate": 1.0},
                output=2.0,
            ),
            _recipe(
                "iron-gear-wheel",
                energy=0.5,
                category="crafting",
                ingredients={"iron-plate": 2.0},
            ),
            _recipe(
                "iron-plate",
                energy=3.2,
                category="smelting",
                ingredients={"iron-ore": 1.0},
            ),
            _recipe(
                "copper-plate",
                energy=3.2,
                category="smelting",
                ingredients={"copper-ore": 1.0},
            ),
            _recipe(
                "stone-furnace",
                energy=0.5,
                category="crafting",
                ingredients={"stone": 5.0},
            ),
            _recipe(
                "assembling-machine-1",
                energy=0.5,
                category="crafting",
                ingredients={
                    "iron-plate": 9.0,
                    "iron-gear-wheel": 5.0,
                    "electronic-circuit": 3.0,
                },
                enabled=automation_researched,
            ),
        ],
        "technologies": [
            {
                "name": "automation",
                "prerequisites": [],
                "unlocks": ["assembling-machine-1"],
                "researched": automation_researched,
                "enabled": True,
            }
        ],
        "machines": [
            {
                "name": "assembling-machine-1",
                "type": "assembling-machine",
                "crafting_categories": ["crafting"],
                "crafting_speed": 0.5,
                "crafting_speed_status": "measured",
                "resource_categories": [],
            },
            {
                "name": "stone-furnace",
                "type": "furnace",
                "crafting_categories": ["smelting"],
                "crafting_speed": 1.0,
                "crafting_speed_status": "measured",
                "resource_categories": [],
            },
        ],
        "belts": [],
        "counts": {"recipes": 7, "technologies": 1, "machines": 2, "belts": 0},
    }


def _world(*entities: str) -> dict[str, Any]:
    return {
        "connected": True,
        "tick": 1,
        "entities": [{"name": name, "type": "assembling-machine"} for name in entities],
        "entity_count": len(entities),
    }


def _progression() -> dict[str, Any]:
    return {
        "next_goal": {
            "goal_id": "electronic_circuits",
            "label": "Electronic circuits",
        }
    }


def _state_with(payload: dict[str, Any]) -> DashboardState:
    state = DashboardState()
    state.factorio.game_knowledge = lambda **_: payload  # type: ignore[method-assign]
    return state


def _plan_payload(
    payload: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any]:
    return _state_with(payload).production_plan_data(
        research={},
        progression=_progression(),
        world=world,
    )


def _parse_like_browser(text: str) -> Any:
    """``json.loads`` accepts Infinity/NaN by default; ``JSON.parse`` does not."""

    def reject(token: str) -> Any:
        raise AssertionError(f"non-JSON constant on the wire: {token}")

    return json.loads(text, parse_constant=reject)


def _step(plan: dict[str, Any], item: str) -> dict[str, Any] | None:
    return next(
        (row for row in plan["steps"] if row["item"] == item),
        None,
    )


def test_locked_machine_is_named_as_blocked_not_proposed_as_craftable() -> None:
    payload = _plan_payload(_catalog_payload(), _world())

    plan = payload["plan"]
    assert plan is not None, "the live path must plan with the dependency planner"
    assert payload["planner"] == "dependency_planner"
    assert payload["catalog_source"] == "live_factorio_prototypes"

    machine = _step(plan, "assembling-machine-1")
    assert machine is not None, "the machine the steps need has to be planned too"
    assert machine["craftable_now"] is False
    assert machine["blocked_by_technologies"] == ["automation"]

    assert plan["missing_technologies"] == ["automation"]
    assert plan["feasible"] is False
    kinds = {blocker["kind"] for blocker in plan["blockers"]}
    assert "technology_locked" in kinds


def test_researching_the_technology_makes_the_same_step_craftable() -> None:
    payload = _plan_payload(
        _catalog_payload(automation_researched=True),
        _world(),
    )

    plan = payload["plan"]
    machine = _step(plan, "assembling-machine-1")
    assert machine is not None
    assert machine["craftable_now"] is True
    assert machine["blocked_by_technologies"] == []
    assert plan["missing_technologies"] == []
    kinds = {blocker["kind"] for blocker in plan["blockers"]}
    assert "technology_locked" not in kinds


def test_machines_the_world_already_holds_are_not_planned_again() -> None:
    empty = _plan_payload(_catalog_payload(), _world())
    stocked = _plan_payload(
        _catalog_payload(),
        _world(*(["assembling-machine-1"] * 40), *(["stone-furnace"] * 40)),
    )

    assert "assembling-machine-1" in empty["plan"]["machine_requirements"]
    assert "stone-furnace" in empty["plan"]["machine_requirements"]
    assert empty["availability"]["measured"] is True
    assert empty["availability"]["counts"] == {}

    assert stocked["plan"]["machine_requirements"] == {}
    assert _step(stocked["plan"], "assembling-machine-1") is None
    assert stocked["plan"]["feasible"] is True
    assert stocked["availability"]["counts"]["assembling-machine-1"] == 40.0


def test_an_unmeasured_world_never_reads_as_an_empty_one() -> None:
    payload = _plan_payload(
        _catalog_payload(),
        {"connected": False, "entities": [], "error": "RCON unavailable"},
    )

    assert payload["availability"]["measured"] is False
    assert payload["availability"]["counts"] is None
    assert payload["availability"]["source"] is None


def test_the_dag_contour_the_browser_reads_is_preserved() -> None:
    payload = _plan_payload(_catalog_payload(), _world())

    dag = payload["dag"]
    assert dag["target_item"] == "electronic-circuit"
    assert dag["target_rate_per_s"] == 0.20
    assert set(dag["raw_requirements_per_s"]) == {"iron-ore", "copper-ore"}

    node = next(row for row in dag["nodes"] if row["item"] == "electronic-circuit")
    for key in (
        "item",
        "target_rate_per_s",
        "crafts_per_s",
        "crafting_time_s",
        "minimum_machines_at_speed_1",
        "ingredients",
    ):
        assert key in node, f"the renderer reads {key}"
    assert node["minimum_machines_at_speed_1"] >= 1
    assert {row["item"] for row in node["ingredients"]} == {
        "iron-plate",
        "copper-cable",
    }
    assert node["machine"] == "assembling-machine-1"


def test_a_refused_plan_is_declared_not_raised(monkeypatch) -> None:
    class _Refusing:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def plan(self, *_: Any, **__: Any) -> Any:
            raise ValueError("recipe dependency cycle")

    monkeypatch.setattr(
        "factorio_ai_lab.dashboard.state.DependencyPlanner",
        _Refusing,
    )
    payload = _plan_payload(_catalog_payload(), _world())

    assert payload["plan"] is None
    assert payload["dag"] is None
    assert payload["plan_error"].startswith("ValueError:")
    assert payload["goal_id"] == "electronic_circuits"


def test_the_endpoint_answers_a_refusal_with_200_and_strict_json(monkeypatch) -> None:
    class _Refusing:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def plan(self, *_: Any, **__: Any) -> Any:
            raise ValueError("recipe dependency cycle")

    monkeypatch.setattr(
        "factorio_ai_lab.dashboard.state.DependencyPlanner",
        _Refusing,
    )
    monkeypatch.setattr(
        dashboard_app.state.factorio,
        "game_knowledge",
        lambda **_: _catalog_payload(),
    )
    monkeypatch.setattr(
        dashboard_app.state.factorio,
        "snapshot",
        lambda: _world(),
    )
    monkeypatch.setattr(dashboard_app.state, "research_data", dict)
    monkeypatch.setattr(
        dashboard_app.state,
        "engineering_progression_data",
        lambda **_: _progression(),
    )

    response = TestClient(dashboard_app.app).get(PLAN_URL)

    assert response.status_code == 200
    body = _parse_like_browser(response.text)
    assert body["plan_error"].startswith("ValueError:")


def test_plan_cache_follows_the_catalog_not_the_clock() -> None:
    state = _state_with(_catalog_payload())
    first = state.production_plan_data(
        research={},
        progression=_progression(),
        world=_world(),
    )
    cached = state.production_plan_data(
        research={},
        progression=_progression(),
        world=_world(),
    )
    assert cached["plan"] is first["plan"], "an unchanged catalog must be reused"

    state.factorio.game_knowledge = (  # type: ignore[method-assign]
        lambda **_: _catalog_payload(automation_researched=True)
    )
    refreshed = state.production_plan_data(
        research={},
        progression=_progression(),
        world=_world(),
    )
    assert refreshed["plan"] is not first["plan"], "research has to force a replan"
    assert refreshed["plan"]["missing_technologies"] == []


# -- machine diagnostics ------------------------------------------------


def _write_report(
    directory: Path,
    generation: int,
    *,
    diagnostics: dict[str, Any] | None,
    output: float | None = 0.0,
) -> Path:
    metrics: dict[str, Any] = {"electronic_circuit_output": output}
    if diagnostics is not None:
        metrics["electronic_circuit_diagnostics"] = diagnostics
    path = directory / f"generation-{generation:04d}-curriculum.json"
    path.write_text(
        json.dumps({"generation": generation, "metrics": metrics}),
        encoding="utf-8",
    )
    return path


def _diagnostics_block(stall_cause: str) -> dict[str, Any]:
    return {
        "accepted": False,
        "error_occurred": False,
        "error": None,
        "circuit_assembler": {
            "stall_cause": stall_cause,
            "status_before": "no_power",
            "status_after": "no_power",
            "energy_before": 0.0,
            "energy_after": 0.0,
            "network_id": -1.0,
            "recipe": "electronic-circuit",
            "output_before": 0.0,
            "output_after": 0.0,
            "inputs_before": {"copper_cable": 19.0},
            "inputs_after": {"copper_cable": 19.0},
            "machine_stock": 3.0,
            "pole_gap": 12.0,
            "tap_placed": None,
            "tap_gap": None,
        },
        "power": {
            "engine_status_after": "no_fuel",
            "engine_energy_after": 0.0,
            "circuit_pole_network_id": -1.0,
            "note": "engine ran dry",
        },
    }


def test_the_measured_cause_reaches_the_payload(tmp_path: Path) -> None:
    _write_report(tmp_path, 7, diagnostics=_diagnostics_block("no_power"))

    payload = DashboardState().machine_diagnostics_data(reports_dir=tmp_path)

    assert payload["measured"] is True
    assert payload["generation"] == 7
    assert payload["output"] == 0.0
    machine = next(
        row for row in payload["machines"] if row["machine"] == "circuit_assembler"
    )
    assert machine["stall_cause"] == "no_power"
    assert machine["status_after"] == "no_power"
    assert machine["energy_after"] == 0.0
    assert machine["network_id"] == -1.0
    assert machine["inputs_after"] == {"copper_cable": 19.0}
    assert payload["power"]["engine_status_after"] == "no_fuel"


def test_a_reading_never_taken_stays_absent(tmp_path: Path) -> None:
    block = _diagnostics_block("no_power")
    block["circuit_assembler"]["energy_after"] = None
    del block["circuit_assembler"]["network_id"]
    _write_report(tmp_path, 8, diagnostics=block, output=None)

    payload = DashboardState().machine_diagnostics_data(reports_dir=tmp_path)

    machine = payload["machines"][0]
    assert machine["energy_after"] is None
    assert machine["network_id"] is None
    assert machine["tap_gap"] is None
    assert payload["output"] is None


def test_a_report_without_the_block_is_declared_absent(tmp_path: Path) -> None:
    _write_report(tmp_path, 9, diagnostics=None)

    payload = DashboardState().machine_diagnostics_data(reports_dir=tmp_path)

    assert payload["measured"] is False
    assert payload["machines"] == []
    assert payload["reason"].strip()
    assert payload["source"]["reports_read"] == 1


def test_an_empty_directory_is_declared_absent(tmp_path: Path) -> None:
    payload = DashboardState().machine_diagnostics_data(reports_dir=tmp_path)

    assert payload["measured"] is False
    assert payload["machines"] == []
    assert payload["reason"].strip()
    assert payload["source"]["reports_found"] == 0


def test_diagnostics_cache_follows_the_reports_not_the_clock(tmp_path: Path) -> None:
    _write_report(tmp_path, 1, diagnostics=_diagnostics_block("no_power"))
    state = DashboardState()

    first = state.machine_diagnostics_data(reports_dir=tmp_path)
    cached = state.machine_diagnostics_data(reports_dir=tmp_path)
    assert cached is first, "an unchanged report set must be served from cache"

    _write_report(tmp_path, 2, diagnostics=_diagnostics_block("no_fuel"))
    refreshed = state.machine_diagnostics_data(reports_dir=tmp_path)

    assert refreshed is not first
    assert refreshed["generation"] == 2
    assert refreshed["machines"][0]["stall_cause"] == "no_fuel"


def test_the_diagnostics_endpoint_serves_strict_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_report(tmp_path, 3, diagnostics=_diagnostics_block("window_too_short"))
    monkeypatch.setattr(
        dashboard_app.state,
        "generation_reports_dir",
        tmp_path,
    )

    response = TestClient(dashboard_app.app).get(DIAGNOSTICS_URL)

    assert response.status_code == 200
    body = _parse_like_browser(response.text)
    assert body["machines"][0]["stall_cause"] == "window_too_short"
