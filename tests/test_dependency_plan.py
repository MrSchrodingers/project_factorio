"""Tests for the differential dependency planner.

The stub payload is shaped exactly like ``runs/game_knowledge_graph.json``:
same keys, same nesting, same status fields.

The last block reads the real file. That file is written by a live run, so its
``enabled`` and ``researched`` fields move between runs; measured on
2026-09-23 it went from 24 to 34 enabled recipes and from 1 to 3 researched
technologies inside ten minutes. Those tests therefore assert recipe topology,
which does not move, and derive every technology expectation from the payload
they just read instead of writing the current game state down.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from factorio_ai_lab.planning.dependency_plan import (
    BLOCKER_MACHINE_ABSENT,
    BLOCKER_MACHINE_BOOTSTRAP,
    BLOCKER_MAX_DEPTH,
    BLOCKER_RAW_SOURCE_ABSENT,
    BLOCKER_RECIPE_ABSENT,
    BLOCKER_RECIPE_CYCLE,
    BLOCKER_RECIPE_DISABLED,
    BLOCKER_TECHNOLOGY_LOCKED,
    CAPACITY_DERIVED,
    CAPACITY_INDETERMINATE,
    MACHINE_COUNT_DERIVED,
    MACHINE_COUNT_MINIMUM,
    MISSING_CRAFTING_SPEED,
    MISSING_CRAFTING_TIME,
    DependencyPlanner,
)
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_UNKNOWN,
    RuntimeFactorioCatalog,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
KNOWLEDGE_GRAPH = REPO_ROOT / "runs" / "game_knowledge_graph.json"


def _item(name: str, amount: float) -> dict[str, object]:
    return {"name": name, "type": "item", "amount": amount}


def _recipe(
    name: str,
    *,
    energy: float | None,
    category: str,
    ingredients: list[dict[str, object]],
    products: list[dict[str, object]],
    enabled: bool,
) -> dict[str, object]:
    row: dict[str, object] = {
        "name": name,
        "categories": [category],
        "ingredients": ingredients,
        "products": products,
        "enabled": enabled,
        "enabled_by_default": enabled,
        "hidden_from_player_crafting": False,
    }
    if energy is not None:
        row["energy"] = energy
    return row


PAYLOAD: dict[str, object] = {
    "connected": True,
    "factorio_version": "2.0.73",
    "recipes": [
        _recipe(
            "iron-plate",
            energy=3.2,
            category="smelting",
            ingredients=[_item("iron-ore", 1)],
            products=[_item("iron-plate", 1)],
            enabled=True,
        ),
        _recipe(
            "copper-plate",
            energy=3.2,
            category="smelting",
            ingredients=[_item("copper-ore", 1)],
            products=[_item("copper-plate", 1)],
            enabled=True,
        ),
        _recipe(
            "copper-cable",
            energy=0.5,
            category="crafting",
            ingredients=[_item("copper-plate", 1)],
            products=[_item("copper-cable", 2)],
            enabled=False,
        ),
        _recipe(
            "iron-gear-wheel",
            energy=0.5,
            category="crafting",
            ingredients=[_item("iron-plate", 2)],
            products=[_item("iron-gear-wheel", 1)],
            enabled=True,
        ),
        _recipe(
            "electronic-circuit",
            energy=0.5,
            category="crafting",
            ingredients=[_item("iron-plate", 1), _item("copper-cable", 3)],
            products=[_item("electronic-circuit", 1)],
            enabled=False,
        ),
        _recipe(
            "transport-belt",
            energy=0.5,
            category="crafting",
            ingredients=[_item("iron-plate", 1), _item("iron-gear-wheel", 1)],
            products=[_item("transport-belt", 2)],
            enabled=True,
        ),
        _recipe(
            "assembling-machine-1",
            energy=0.5,
            category="crafting",
            ingredients=[
                _item("iron-plate", 9),
                _item("iron-gear-wheel", 5),
                _item("electronic-circuit", 3),
            ],
            products=[_item("assembling-machine-1", 1)],
            enabled=False,
        ),
        _recipe(
            "stone-furnace",
            energy=0.5,
            category="crafting",
            ingredients=[_item("stone", 5)],
            products=[_item("stone-furnace", 1)],
            enabled=True,
        ),
        # No ``energy`` field at all. The catalog reports no time here, and
        # the planner must keep the absence an absence.
        _recipe(
            "timeless-widget",
            energy=None,
            category="crafting",
            ingredients=[_item("iron-plate", 1)],
            products=[_item("timeless-widget", 1)],
            enabled=True,
        ),
        # Disabled and unlocked by nothing: unavailable for good.
        _recipe(
            "orphan-widget",
            energy=1.0,
            category="crafting",
            ingredients=[_item("iron-plate", 1)],
            products=[_item("orphan-widget", 1)],
            enabled=False,
        ),
        # Needs a crafting category no machine in this payload reports.
        _recipe(
            "fluid-widget",
            energy=1.0,
            category="chemistry",
            ingredients=[_item("iron-plate", 1)],
            products=[_item("fluid-widget", 1)],
            enabled=True,
        ),
    ],
    "technologies": [
        {
            "name": "automation-science-pack",
            "enabled": True,
            "prerequisites": {},
            "unlocks": [],
            "researched": True,
        },
        {
            "name": "electronics",
            "enabled": True,
            "prerequisites": {},
            "unlocks": ["copper-cable", "electronic-circuit"],
            "researched": False,
        },
        {
            "name": "automation",
            "enabled": True,
            "prerequisites": ["automation-science-pack", "electronics"],
            "unlocks": ["assembling-machine-1"],
            "researched": False,
        },
    ],
    "machines": [
        {
            "name": "assembling-machine-1",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
            "crafting_speed": 0.5,
            "crafting_speed_status": "measured",
            "mining_speed_status": "absent",
        },
        {
            "name": "assembling-machine-2",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
            "crafting_speed": 0.75,
            "crafting_speed_status": "measured",
            "mining_speed_status": "absent",
        },
        {
            "name": "stone-furnace",
            "type": "furnace",
            "crafting_categories": ["smelting"],
            "crafting_speed": 1.0,
            "crafting_speed_status": "measured",
            "mining_speed_status": "absent",
        },
        {
            "name": "character",
            "type": "character",
            "crafting_categories": ["crafting"],
            "crafting_speed": 1.0,
            "crafting_speed_status": "measured",
            "mining_speed": 0.5,
            "mining_speed_status": "measured",
        },
    ],
    "belts": [],
}

#: Same recipes, but the crafting machine reports no usable speed.
UNMEASURED_SPEED_PAYLOAD: dict[str, object] = {
    **PAYLOAD,
    "machines": [
        {
            "name": "assembling-machine-1",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
            "crafting_speed_status": "probe_failed",
            "mining_speed_status": "absent",
        },
        {
            "name": "stone-furnace",
            "type": "furnace",
            "crafting_categories": ["smelting"],
            "crafting_speed": 1.0,
            "crafting_speed_status": "measured",
            "mining_speed_status": "absent",
        },
    ],
}

CYCLIC_PAYLOAD: dict[str, object] = {
    "connected": True,
    "recipes": [
        _recipe(
            "alpha",
            energy=1.0,
            category="crafting",
            ingredients=[_item("beta", 1)],
            products=[_item("alpha", 1)],
            enabled=True,
        ),
        _recipe(
            "beta",
            energy=1.0,
            category="crafting",
            ingredients=[_item("alpha", 1)],
            products=[_item("beta", 1)],
            enabled=True,
        ),
    ],
    "technologies": [],
    "machines": [
        {
            "name": "assembling-machine-1",
            "type": "assembling-machine",
            "crafting_categories": ["crafting"],
            "crafting_speed": 0.5,
            "crafting_speed_status": "measured",
            "mining_speed_status": "absent",
        }
    ],
    "belts": [],
}

#: Enough machines on hand that machine deficits never enter a plan.
MACHINE_STOCK = {"assembling-machine-1": 8, "stone-furnace": 8}

ALL_RESEARCHED = ("automation-science-pack", "electronics", "automation")


@pytest.fixture
def catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog(PAYLOAD)


@pytest.fixture
def unlocked_planner(catalog: RuntimeFactorioCatalog) -> DependencyPlanner:
    return DependencyPlanner(catalog, researched=ALL_RESEARCHED)


# ---------------------------------------------------------------------------
# What the reused expansion already does, and where it stops
# ---------------------------------------------------------------------------


def test_reused_planner_raises_on_a_cycle_so_the_wrapper_has_to_cut_it() -> None:
    """Why ``plan`` does not hand the catalog provider over untouched."""
    cyclic = RuntimeFactorioCatalog(CYCLIC_PAYLOAD)
    with pytest.raises(ValueError, match="recipe dependency cycle"):
        cyclic.planner().plan("alpha", 1.0)
    plan = DependencyPlanner(cyclic).plan(
        "alpha",
        1,
        available={"assembling-machine-1": 4},
    )
    assert BLOCKER_RECIPE_CYCLE in plan.blocker_kinds()


def test_reused_planner_omits_the_machine_that_runs_the_recipe(
    catalog: RuntimeFactorioCatalog,
) -> None:
    """The DAG holds ingredients only; the assembler is not one of them."""
    nodes = [node.item for node in catalog.planner().plan("transport-belt", 1.0).nodes]
    assert "assembling-machine-1" not in nodes
    plan = DependencyPlanner(catalog, researched=ALL_RESEARCHED).plan(
        "transport-belt",
        1,
        available={},
    )
    assert plan.step("assembling-machine-1") is not None


# ---------------------------------------------------------------------------
# Expansion to raw material, with quantities and order
# ---------------------------------------------------------------------------


def test_target_expands_to_raw_material_with_counts(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    assert plan.raw_requirements == {"copper-ore": 2.0, "iron-ore": 1.0}
    assert plan.step("copper-cable").crafts == 2
    assert plan.step("copper-plate").required_count == 2.0
    assert plan.step("electronic-circuit").crafts == 1
    assert plan.feasible


def test_steps_are_ordered_ingredient_before_consumer(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    order = [step.item for step in plan.steps]
    assert order.index("copper-plate") < order.index("copper-cable")
    assert order.index("copper-cable") < order.index("electronic-circuit")
    assert order.index("iron-plate") < order.index("electronic-circuit")
    assert order[-1] == "electronic-circuit"


def test_depth_is_the_longest_ingredient_path_from_raw(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    assert plan.step("iron-plate").depth == 1
    assert plan.step("copper-plate").depth == 1
    assert plan.step("copper-cable").depth == 2
    assert plan.step("electronic-circuit").depth == 3


def test_inventory_removes_branches_that_are_already_satisfied(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "electronic-circuit",
        1,
        available={**MACHINE_STOCK, "copper-cable": 3, "iron-plate": 1},
    )
    assert plan.step("copper-cable") is None
    assert plan.step("copper-plate") is None
    assert plan.step("iron-plate") is None
    assert plan.raw_requirements == {}
    assert [step.item for step in plan.steps] == ["electronic-circuit"]


def test_ceiling_surplus_is_returned_to_stock(
    unlocked_planner: DependencyPlanner,
) -> None:
    # One transport-belt craft yields 2, so asking for 3 costs 2 crafts and
    # leaves 1 belt over rather than charging a third craft.
    plan = unlocked_planner.plan("transport-belt", 3, available=MACHINE_STOCK)
    assert plan.step("transport-belt").crafts == 2
    assert plan.raw_requirements["iron-ore"] == 6.0


# ---------------------------------------------------------------------------
# Machines are items too
# ---------------------------------------------------------------------------


def test_missing_machine_becomes_a_step_with_its_own_ingredients(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("transport-belt", 1, available={})
    assert plan.step("transport-belt").machine == "assembling-machine-1"
    machine_step = plan.step("assembling-machine-1")
    assert machine_step is not None
    assert machine_step.is_machine_requirement
    # The machine is not a leaf: its own ingredients are expanded.
    assert plan.step("electronic-circuit") is not None
    assert plan.step("copper-cable") is not None
    assert plan.machine_requirements["assembling-machine-1"] >= 1
    assert plan.machine_requirements["stone-furnace"] >= 1


def test_machine_already_on_hand_is_not_crafted_again(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("transport-belt", 1, available=MACHINE_STOCK)
    assert plan.step("assembling-machine-1") is None
    assert plan.machine_requirements == {}


def test_one_machine_per_recipe_step_is_reserved_not_shared(
    unlocked_planner: DependencyPlanner,
) -> None:
    # transport-belt and iron-gear-wheel are two crafting steps: two machines.
    plan = unlocked_planner.plan(
        "transport-belt",
        1,
        available={"assembling-machine-1": 2, "stone-furnace": 4},
    )
    assert plan.machine_reservations["assembling-machine-1"] == 2
    assert plan.machine_requirements == {}
    assert plan.step("assembling-machine-1") is None


def test_one_machine_short_turns_into_a_machine_to_build(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "transport-belt",
        1,
        available={"assembling-machine-1": 1, "stone-furnace": 4},
    )
    assert plan.machine_requirements["assembling-machine-1"] >= 1
    assert plan.step("assembling-machine-1") is not None


def test_machine_ordering_puts_the_machine_before_what_it_builds(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "transport-belt",
        1,
        available={"stone-furnace": 8},
    )
    order = [step.item for step in plan.steps]
    assert order.index("assembling-machine-1") < order.index("transport-belt")


def test_machine_bootstrap_cycle_is_declared_not_recursed(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("transport-belt", 1, available={})
    assert BLOCKER_MACHINE_BOOTSTRAP in plan.blocker_kinds()
    bootstrap = next(
        blocker
        for blocker in plan.blockers
        if blocker.kind == BLOCKER_MACHINE_BOOTSTRAP
    )
    assert bootstrap.item == "assembling-machine-1"
    assert bootstrap.machine == "assembling-machine-1"
    assert not plan.feasible


def test_machine_preference_can_name_the_character(
    catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(
        catalog,
        researched=ALL_RESEARCHED,
        machine_preference=("character",),
        excluded_machine_types=frozenset(),
    )
    plan = planner.plan(
        "transport-belt",
        1,
        available={"character": 8, "stone-furnace": 8},
    )
    assert plan.step("transport-belt").machine == "character"
    # The furnace keeps its own category: preference only applies where the
    # preferred machine reports the category.
    assert plan.step("iron-plate").machine == "stone-furnace"
    assert BLOCKER_MACHINE_BOOTSTRAP not in plan.blocker_kinds()


def test_category_without_any_machine_is_declared(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("fluid-widget", 1, available=MACHINE_STOCK)
    assert BLOCKER_MACHINE_ABSENT in plan.blocker_kinds()
    assert plan.step("fluid-widget").machine is None
    assert not plan.feasible


# ---------------------------------------------------------------------------
# Technology
# ---------------------------------------------------------------------------


def test_locked_recipe_is_declared_instead_of_proposed_as_craftable(
    catalog: RuntimeFactorioCatalog,
) -> None:
    plan = DependencyPlanner(catalog).plan(
        "electronic-circuit",
        1,
        available=MACHINE_STOCK,
    )
    assert not plan.feasible
    assert BLOCKER_TECHNOLOGY_LOCKED in plan.blocker_kinds()
    step = plan.step("electronic-circuit")
    assert step.blocked_by_technologies == ("electronics",)
    assert step.craftable_now is False
    assert "electronics" in plan.missing_technologies


def test_researched_technology_unlocks_the_recipe(
    catalog: RuntimeFactorioCatalog,
) -> None:
    plan = DependencyPlanner(catalog, researched=("electronics",)).plan(
        "electronic-circuit",
        1,
        available=MACHINE_STOCK,
    )
    assert plan.missing_technologies == ()
    assert plan.step("electronic-circuit").craftable_now is True
    assert plan.feasible


def test_missing_technologies_list_unresearched_prerequisites_first(
    catalog: RuntimeFactorioCatalog,
) -> None:
    plan = DependencyPlanner(catalog).plan(
        "assembling-machine-1",
        1,
        # Not MACHINE_STOCK: eight assemblers on hand would satisfy the
        # target outright and leave nothing to plan.
        available={"assembling-machine-2": 8, "stone-furnace": 8},
    )
    step = plan.step("assembling-machine-1")
    assert step.blocked_by_technologies == ("electronics", "automation")
    # automation-science-pack is already researched, so it is not listed.
    assert "automation-science-pack" not in plan.missing_technologies


def test_researched_defaults_to_what_the_payload_reports(
    catalog: RuntimeFactorioCatalog,
) -> None:
    assert DependencyPlanner(catalog).researched == frozenset(
        {"automation-science-pack"}
    )


def test_recipe_disabled_without_any_unlock_is_its_own_refusal(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("orphan-widget", 1, available=MACHINE_STOCK)
    assert BLOCKER_RECIPE_DISABLED in plan.blocker_kinds()
    assert plan.step("orphan-widget").craftable_now is False
    assert not plan.feasible


# ---------------------------------------------------------------------------
# Cycles and recursion
# ---------------------------------------------------------------------------


def test_recipe_cycle_is_declared_not_raised() -> None:
    plan = DependencyPlanner(RuntimeFactorioCatalog(CYCLIC_PAYLOAD)).plan(
        "alpha",
        1,
        available={"assembling-machine-1": 4},
    )
    assert BLOCKER_RECIPE_CYCLE in plan.blocker_kinds()
    cycle = next(
        blocker
        for blocker in plan.blockers
        if blocker.kind == BLOCKER_RECIPE_CYCLE
    )
    assert cycle.chain == ("alpha", "beta", "alpha")
    assert not plan.feasible


def test_max_depth_stops_the_walk_and_says_so(
    catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(
        catalog,
        researched=ALL_RESEARCHED,
        max_depth=1,
    )
    plan = planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    assert BLOCKER_MAX_DEPTH in plan.blocker_kinds()
    assert not plan.feasible


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


def test_capacity_is_derived_from_measured_time_and_speed(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "electronic-circuit",
        1,
        rate_per_s=2.0,
        available=MACHINE_STOCK,
    )
    circuit = plan.capacity_for("electronic-circuit")
    assert circuit.status == CAPACITY_DERIVED
    assert circuit.machine == "assembling-machine-1"
    assert circuit.crafting_time_s == 0.5
    assert circuit.crafting_speed == 0.5
    # 2 crafts/s * 0.5 s / 0.5 speed = 2 machines.
    assert circuit.machines == 2
    plate = plan.capacity_for("iron-plate")
    assert plate.machine == "stone-furnace"
    # 2 plates/s * 3.2 s / 1.0 speed = 6.4 -> 7 furnaces.
    assert plate.machines == 7


def test_derived_capacity_sizes_the_machine_count_on_the_step(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "electronic-circuit",
        1,
        rate_per_s=2.0,
        available=MACHINE_STOCK,
    )
    step = plan.step("electronic-circuit")
    assert step.machine_count == 2
    assert step.machine_count_status == MACHINE_COUNT_DERIVED
    # 10 furnaces for copper plate and 7 for iron plate against 8 on hand:
    # the sized capacity is what turns furnaces into a thing to build.
    assert plan.machine_reservations["stone-furnace"] == 17
    assert plan.machine_requirements["stone-furnace"] == 9.0
    # 3 assemblers for cable, 2 for circuit, 1 for the furnace step above.
    assert plan.machine_reservations["assembling-machine-1"] == 6


def test_without_a_target_rate_the_machine_count_is_a_declared_minimum(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    step = plan.step("electronic-circuit")
    assert step.machine_count == 1
    assert step.machine_count_status == MACHINE_COUNT_MINIMUM
    assert plan.capacity == ()
    assert plan.raw_rate_per_s is None


def test_unmeasured_machine_speed_makes_capacity_indeterminate_not_zero() -> None:
    planner = DependencyPlanner(
        RuntimeFactorioCatalog(UNMEASURED_SPEED_PAYLOAD),
        researched=ALL_RESEARCHED,
    )
    plan = planner.plan(
        "electronic-circuit",
        1,
        rate_per_s=2.0,
        available=MACHINE_STOCK,
    )
    circuit = plan.capacity_for("electronic-circuit")
    assert circuit.status == CAPACITY_INDETERMINATE
    assert circuit.machines is None
    assert circuit.crafting_speed is None
    assert MISSING_CRAFTING_SPEED in circuit.missing
    # The step falls back to the structural minimum, never to a sized count.
    step = plan.step("electronic-circuit")
    assert step.machine_count == 1
    assert step.machine_count_status == MACHINE_COUNT_MINIMUM


def test_absent_recipe_time_is_absent_not_the_catalog_default(
    catalog: RuntimeFactorioCatalog,
    unlocked_planner: DependencyPlanner,
) -> None:
    # A row that carries no ``energy`` reaches the spec as no time at all,
    # qualified by its status, instead of as a substituted 0.5 s.
    spec = catalog.recipe_provider("timeless-widget")
    assert spec.crafting_time_s is None
    assert spec.crafting_time_status == PROBE_UNKNOWN
    plan = unlocked_planner.plan(
        "timeless-widget",
        1,
        rate_per_s=1.0,
        available=MACHINE_STOCK,
    )
    widget = plan.capacity_for("timeless-widget")
    assert widget.crafting_time_s is None
    assert widget.status == CAPACITY_INDETERMINATE
    assert widget.machines is None
    assert MISSING_CRAFTING_TIME in widget.missing


def test_capacity_reports_the_raw_rate_the_plant_has_to_sustain(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "electronic-circuit",
        1,
        rate_per_s=2.0,
        available=MACHINE_STOCK,
    )
    assert plan.raw_rate_per_s is not None
    assert math.isclose(plan.raw_rate_per_s["iron-ore"], 2.0)
    assert math.isclose(plan.raw_rate_per_s["copper-ore"], 3.0)


# ---------------------------------------------------------------------------
# Declared refusal
# ---------------------------------------------------------------------------


def test_unknown_target_refuses_instead_of_returning_an_empty_plan(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("mystery-item", 1, available=MACHINE_STOCK)
    assert plan.steps == ()
    assert not plan.feasible
    assert BLOCKER_RECIPE_ABSENT in plan.blocker_kinds()
    assert plan.unresolved_requirements == {"mystery-item": 1.0}


def test_raw_material_without_a_declared_source_is_refused(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan(
        "electronic-circuit",
        1,
        available=MACHINE_STOCK,
        raw_sources=("iron-ore",),
    )
    assert plan.raw_sources_declared is True
    assert BLOCKER_RAW_SOURCE_ABSENT in plan.blocker_kinds()
    missing = {
        blocker.item
        for blocker in plan.blockers
        if blocker.kind == BLOCKER_RAW_SOURCE_ABSENT
    }
    assert missing == {"copper-ore"}


def test_undeclared_raw_sources_never_read_as_available(
    unlocked_planner: DependencyPlanner,
) -> None:
    plan = unlocked_planner.plan("electronic-circuit", 1, available=MACHINE_STOCK)
    assert plan.raw_sources_declared is False
    assert BLOCKER_RAW_SOURCE_ABSENT not in plan.blocker_kinds()


def test_plan_serialises_with_the_refusal_visible(
    catalog: RuntimeFactorioCatalog,
) -> None:
    plan = DependencyPlanner(catalog).plan(
        "electronic-circuit",
        1,
        available=MACHINE_STOCK,
    )
    payload = plan.as_dict()
    assert payload["feasible"] is False
    assert payload["blockers"]
    assert json.dumps(payload)


def test_non_positive_target_count_is_rejected(
    unlocked_planner: DependencyPlanner,
) -> None:
    with pytest.raises(ValueError):
        unlocked_planner.plan("electronic-circuit", 0)


def test_non_positive_target_rate_is_rejected(
    unlocked_planner: DependencyPlanner,
) -> None:
    with pytest.raises(ValueError):
        unlocked_planner.plan("electronic-circuit", 1, rate_per_s=0.0)


# ---------------------------------------------------------------------------
# The real catalog on disk
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_catalog() -> RuntimeFactorioCatalog:
    if not KNOWLEDGE_GRAPH.exists():
        pytest.skip("runs/game_knowledge_graph.json is not present")
    try:
        payload = json.loads(KNOWLEDGE_GRAPH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # pragma: no cover - live file, partial write
        pytest.skip("runs/game_knowledge_graph.json was mid-write")
    return RuntimeFactorioCatalog(payload)


def _expected_craftable(
    catalog: RuntimeFactorioCatalog,
    planner: DependencyPlanner,
    item: str,
) -> bool:
    choice = catalog.recipe_choice(item)
    assert choice is not None
    return bool(choice.enabled) or any(
        technology in planner.researched
        for technology in catalog.unlock_technologies(item)
    )


def test_real_catalog_knows_far_more_than_the_hand_written_one(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    from factorio_ai_lab.planning.factorio_catalog import EARLY_GAME_RECIPES

    assert len(real_catalog.recipe_rows) > 200
    assert len(EARLY_GAME_RECIPES) < 20
    # Steel exists in the game and not in the hand written catalog.
    assert real_catalog.recipe_choice("steel-plate") is not None
    assert "steel-plate" not in EARLY_GAME_RECIPES


def test_real_catalog_electronic_circuit_reaches_raw_ore(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(real_catalog)
    plan = planner.plan(
        "electronic-circuit",
        1,
        available={"assembling-machine-1": 8, "stone-furnace": 8},
    )
    assert plan.step("electronic-circuit").depth == 3
    assert plan.raw_requirements == {"copper-ore": 2.0, "iron-ore": 1.0}
    assert plan.step("iron-plate").machine == "stone-furnace"
    assert plan.step("electronic-circuit").machine == "assembling-machine-1"


def test_real_catalog_assembling_machine_is_itself_a_dependency(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(real_catalog)
    plan = planner.plan("assembling-machine-1", 1, available={})
    assert plan.step("assembling-machine-1").depth == 4
    assert plan.step("electronic-circuit").depth == 3
    assert plan.step("iron-gear-wheel").depth == 2
    # The machine that crafts the machine is the machine.
    assert BLOCKER_MACHINE_BOOTSTRAP in plan.blocker_kinds()
    assert plan.machine_requirements.get("stone-furnace", 0) >= 1
    assert plan.raw_requirements["iron-ore"] == 22.0
    assert plan.raw_requirements["copper-ore"] == 5.0


def test_real_catalog_logistic_science_pack_is_five_deep(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(real_catalog)
    plan = planner.plan(
        "logistic-science-pack",
        1,
        available={"assembling-machine-1": 16, "stone-furnace": 16},
    )
    depths = {step.item: step.depth for step in plan.steps}
    assert depths["iron-plate"] == 1
    assert depths["iron-gear-wheel"] == 2
    assert depths["electronic-circuit"] == 3
    assert depths["inserter"] == 4
    assert depths["logistic-science-pack"] == 5
    assert set(plan.raw_requirements) == {"iron-ore", "copper-ore"}


def test_real_catalog_steel_plate_is_smelted_twice_over(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(real_catalog)
    plan = planner.plan("steel-plate", 1, available={"stone-furnace": 8})
    assert plan.step("steel-plate").depth == 2
    assert plan.step("steel-plate").machine == "stone-furnace"
    assert plan.raw_requirements == {"iron-ore": 5.0}


def test_real_catalog_capacity_uses_measured_assembler_speed(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(
        real_catalog,
        machine_preference=("assembling-machine-2",),
    )
    plan = planner.plan(
        "electronic-circuit",
        1,
        rate_per_s=1.0,
        available={"assembling-machine-2": 8, "stone-furnace": 8},
    )
    circuit = plan.capacity_for("electronic-circuit")
    assert circuit.machine == "assembling-machine-2"
    assert circuit.crafting_speed == 0.75
    assert circuit.status == CAPACITY_DERIVED
    assert circuit.machines == 1


def test_real_catalog_technology_gate_agrees_with_the_payload(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    """``enabled``/``researched`` move with the live run; the rule does not."""
    planner = DependencyPlanner(real_catalog)
    plan = planner.plan(
        "logistic-science-pack",
        1,
        available={"assembling-machine-1": 16, "stone-furnace": 16},
    )
    for step in plan.steps:
        expected = _expected_craftable(real_catalog, planner, step.item)
        assert step.craftable_now is expected
        if expected:
            assert step.blocked_by_technologies == ()
        else:
            assert step.blocked_by_technologies
            assert set(step.blocked_by_technologies).isdisjoint(planner.researched)


def test_real_catalog_names_the_technology_of_a_locked_recipe(
    real_catalog: RuntimeFactorioCatalog,
) -> None:
    planner = DependencyPlanner(real_catalog)
    locked = [
        row["name"]
        for row in real_catalog.recipe_rows
        if not row.get("enabled")
        and real_catalog.unlock_technologies(str(row.get("name")))
        and not _expected_craftable(real_catalog, planner, str(row.get("name")))
    ]
    if not locked:
        pytest.skip("every recipe in the live payload is currently unlocked")
    item = min(locked)
    plan = planner.plan(
        item,
        1,
        available={"assembling-machine-1": 32, "stone-furnace": 32},
    )
    step = plan.step(item)
    assert step is not None
    assert step.craftable_now is False
    assert step.blocked_by_technologies
    assert BLOCKER_TECHNOLOGY_LOCKED in plan.blocker_kinds()
    assert not plan.feasible
