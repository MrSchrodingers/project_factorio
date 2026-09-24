from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from factorio_ai_lab.cortex.actions import (
    ActionCondition,
    ActionProvenance,
    ConditionOperator,
    ConditionState,
)
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.structural import (
    REFUSAL_BUFFER_CONTENTS_UNOBSERVED,
    REFUSAL_BUFFER_MATERIAL_AMBIGUOUS,
    REFUSAL_MINING_MATERIAL_AMBIGUOUS,
    REFUSAL_NO_DIRECT_PROCESSING_RECIPE,
    STATUS_PARTIAL,
    STATUS_READY,
    plan_processing_for_buffered_output,
)
from factorio_ai_lab.cortex.structural_prepare import (
    CONTRACT_VERSION,
    PURPOSE_INFRASTRUCTURE,
    REFUSAL_BRANCH_PRECONDITION,
    prepare_structural_branch,
)
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.planning.placement import ResourceSurvey
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog


def entity(name, x, y, *, unit, contents_marker=True, contents=None, direction=0):
    row = {
        "name": name,
        "position": {"x": x, "y": y},
        "unit_number": unit,
        "direction": direction,
        "status": "working",
    }
    if contents_marker:
        row["contents"] = [] if contents is None else contents
    return row


def catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog({
        "recipes": [
            {
                "name": "iron-plate",
                "energy": 3.2,
                "categories": ["smelting"],
                "ingredients": [{"name": "iron-ore", "amount": 1}],
                "products": [{"name": "iron-plate", "amount": 1}],
                "enabled": True,
            },
            {
                "name": "copper-plate",
                "energy": 3.2,
                "categories": ["smelting"],
                "ingredients": [{"name": "copper-ore", "amount": 1}],
                "products": [{"name": "copper-plate", "amount": 1}],
                "enabled": True,
            },
            {
                "name": "stone-furnace",
                "energy": 0.5,
                "categories": ["crafting"],
                "ingredients": [{"name": "stone", "amount": 5}],
                "products": [{"name": "stone-furnace", "amount": 1}],
                "enabled": True,
            },
            {
                "name": "grenade",
                "energy": 8,
                "categories": ["crafting"],
                "ingredients": [
                    {"name": "coal", "amount": 10},
                    {"name": "iron-plate", "amount": 5},
                ],
                "products": [{"name": "grenade", "amount": 1}],
                "enabled": True,
            },
        ],
        "technologies": [],
        "machines": [
            {
                "name": "character",
                "type": "character",
                "crafting_categories": ["crafting"],
                "crafting_speed": 1,
                "crafting_speed_status": "measured",
            },
            {
                "name": "stone-furnace",
                "type": "furnace",
                "crafting_categories": ["smelting"],
                "crafting_speed": 1,
                "crafting_speed_status": "measured",
            },
        ],
    })


def request(*targets: str):
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=targets,
        arguments={"producers": list(targets)},
    )
    return request_from_repair_action(
        repair,
        action_id="f1-counterexample",
        provenance=ActionProvenance(
            requested_by="repair-loop",
            source_component="test",
            code_revision="sha",
            run_id="run",
        ),
    )


def test_plans_direct_processing_cell_from_observed_buffer_material() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 20}],
        ),
    ]
    graph = build_factory_graph(world)
    original = deepcopy(world)

    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=graph,
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5, "transport-belt": 20, "inserter": 4},
    )

    assert world == original
    assert result.status == STATUS_READY
    assert result.ready is True
    assert len(result.branches) == 1
    branch = result.branches[0]
    assert branch.material == "iron-ore"
    assert branch.product == "iron-plate"
    assert branch.recipe.recipe_name == "iron-plate"
    assert branch.processor == "stone-furnace"
    assert branch.delivery.builds
    assert branch.placement.builds
    assert branch.request.provenance.parent_action_id == "f1-counterexample"
    assert branch.request.postconditions[0].state.value == "unknown"


def test_groups_multiple_materials_into_independent_branches() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 20}],
        ),
        entity("burner-mining-drill", 20, 0, unit=3, direction=8),
        entity(
            "wooden-chest",
            20,
            2,
            unit=4,
            contents=[{"name": "copper-ore", "count": 15}],
        ),
    ]
    graph = build_factory_graph(world)

    result = plan_processing_for_buffered_output(
        request("u1", "u3"),
        graph=graph,
        world_entities=world,
        catalog=catalog(),
        available={"stone": 10, "transport-belt": 40, "inserter": 8},
    )

    assert result.status == STATUS_READY
    assert {branch.material for branch in result.branches} == {
        "iron-ore",
        "copper-ore",
    }
    assert {branch.product for branch in result.branches} == {
        "iron-plate",
        "copper-plate",
    }


def test_unobserved_buffer_contents_is_refused_not_assumed_empty() -> None:
    world = [
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity("wooden-chest", 0, 2, unit=2, contents_marker=False),
    ]
    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
    )

    assert result.branches == ()
    assert {refusal.code for refusal in result.refusals} == {
        REFUSAL_BUFFER_CONTENTS_UNOBSERVED
    }


def test_ambiguous_buffer_material_is_refused() -> None:
    world = [
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[
                {"name": "iron-ore", "count": 10},
                {"name": "copper-ore", "count": 1},
            ],
        ),
    ]
    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
    )

    assert result.branches == ()
    assert result.refusals[0].code == REFUSAL_BUFFER_MATERIAL_AMBIGUOUS


def test_coal_does_not_become_arbitrary_multi_input_recipe() -> None:
    world = [
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "coal", "count": 20}],
        ),
    ]
    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
    )

    assert result.branches == ()
    assert result.refusals[0].code == REFUSAL_NO_DIRECT_PROCESSING_RECIPE


def test_partial_plan_keeps_ready_ore_branch_and_refuses_ambiguous_coal_goal() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 20}],
        ),
        entity("burner-mining-drill", 20, 0, unit=3, direction=8),
        entity(
            "wooden-chest",
            20,
            2,
            unit=4,
            contents=[{"name": "coal", "count": 20}],
        ),
    ]
    result = plan_processing_for_buffered_output(
        request("u1", "u3"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5},
    )

    assert result.status == STATUS_PARTIAL
    assert [branch.material for branch in result.branches] == ["iron-ore"]
    assert REFUSAL_NO_DIRECT_PROCESSING_RECIPE in {
        refusal.code for refusal in result.refusals
    }

def resource_survey(*names: str) -> ResourceSurvey:
    rows = []
    positions = (
        (-0.5, -0.5),
        (0.5, 0.5),
        (-0.5, 0.5),
        (0.5, -0.5),
    )
    for name, (x, y) in zip(names, positions, strict=False):
        rows.append({
            "name": name,
            "type": "resource",
            "position": {"x": x, "y": y},
        })
    return ResourceSurvey.from_entities(rows, surveyed=(-10, -10, 10, 10))


def test_resource_survey_disambiguates_contaminated_buffer_causally() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[
                {"name": "iron-ore", "count": 10},
                {"name": "coal", "count": 3},
            ],
        ),
    ]

    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5},
        resources=resource_survey("iron-ore"),
    )

    assert result.status == STATUS_READY
    assert len(result.branches) == 1
    assert result.branches[0].material == "iron-ore"
    assert result.sources[0].identity_basis == "mining_resource"
    assert result.sources[0].evidence.source == "resource_survey"
    assert result.sources[0].buffer_evidence is not None


def test_resource_survey_resolves_identity_even_when_buffer_is_empty() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity("wooden-chest", 0, 2, unit=2, contents=[]),
    ]

    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5},
        resources=resource_survey("iron-ore"),
    )

    assert result.status == STATUS_READY
    assert result.sources[0].material == "iron-ore"
    assert result.sources[0].observed_count is None
    assert result.sources[0].identity_basis == "mining_resource"


def test_mixed_resource_footprint_is_refused_without_majority_heuristic() -> None:
    world = [
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 10}],
        ),
    ]

    result = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        resources=resource_survey("iron-ore", "copper-ore"),
    )

    assert result.branches == ()
    assert result.refusals[0].code == REFUSAL_MINING_MATERIAL_AMBIGUOUS


def test_prepares_structural_branch_as_inert_versioned_operation_contract() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[
                {"name": "iron-ore", "count": 10},
                {"name": "coal", "count": 3},
            ],
        ),
    ]
    plan = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5},
        resources=resource_survey("iron-ore"),
    )
    branch = plan.branches[0]

    prepared = prepare_structural_branch(branch)

    assert prepared.ready is True
    assert prepared.prepared is not None
    action = prepared.prepared
    assert action.contract_version == CONTRACT_VERSION
    assert action.purpose == PURPOSE_INFRASTRUCTURE
    assert action.preflight["world_mutation"] is False
    assert action.preflight["material"] == "iron-ore"
    assert action.preflight["product"] == "iron-plate"
    assert {
        "producers_reaching_processor",
        "physical_processing_coverage",
        "processor_output",
    }.issubset(action.measurement_keys)
    assert [operation.op for operation in action.operations] == [
        "ensure_item",
        "place_processor",
        "configure_processing",
        "connect_delivery",
        "verify_postconditions",
    ]


def test_prepared_structural_action_refuses_unsatisfied_hard_precondition() -> None:
    world = [
        entity("character", -5, -5, unit=99),
        entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 10}],
        ),
    ]
    plan = plan_processing_for_buffered_output(
        request("u1"),
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=catalog(),
        available={"stone": 5},
    )
    branch = plan.branches[0]
    blocked = replace(
        branch,
        preconditions=branch.preconditions
        + (
            ActionCondition(
                name="power_capacity_measured",
                operator=ConditionOperator.EXISTS,
                state=ConditionState.UNKNOWN,
                hard=True,
            ),
        ),
    )

    prepared = prepare_structural_branch(blocked)

    assert prepared.ready is False
    assert prepared.refusal is not None
    assert prepared.refusal.code == REFUSAL_BRANCH_PRECONDITION
