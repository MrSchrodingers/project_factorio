from __future__ import annotations

from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.f5d_material_link import plan_existing_processing_link
from factorio_ai_lab.cortex.f5d_material_link_option import (
    compose_material_link_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.learning.repair_loop import (
    INTENT_REROUTE_PRODUCER,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog


def _recipe(name, ingredients, products, *, category="crafting"):
    return {
        "name":name,
        "energy":0.5 if category=="crafting" else 3.2,
        "categories":[category],
        "ingredients":[{"name":n,"amount":a} for n,a in ingredients],
        "products":[{"name":n,"amount":a} for n,a in products],
        "enabled":True,
    }


def _catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog({
        "recipes":[
            _recipe("iron-plate",[("iron-ore",1)],[("iron-plate",1)],category="smelting"),
            _recipe("copper-plate",[("copper-ore",1)],[("copper-plate",1)],category="smelting"),
            _recipe("stone-furnace",[("stone",5)],[("stone-furnace",1)]),
            _recipe("iron-gear-wheel",[("iron-plate",2)],[("iron-gear-wheel",1)]),
            _recipe("copper-cable",[("copper-plate",1)],[("copper-cable",2)]),
            _recipe(
                "electronic-circuit",
                [("iron-plate",1),("copper-cable",3)],
                [("electronic-circuit",1)],
            ),
            _recipe(
                "inserter",
                [("iron-plate",1),("iron-gear-wheel",1),("electronic-circuit",1)],
                [("inserter",1)],
            ),
            _recipe(
                "transport-belt",
                [("iron-plate",1),("iron-gear-wheel",1)],
                [("transport-belt",2)],
            ),
        ],
        "technologies":[],
        "machines":[
            {
                "name":"character",
                "type":"character",
                "crafting_categories":["crafting"],
                "crafting_speed":1,
                "crafting_speed_status":"measured",
            },
            {
                "name":"stone-furnace",
                "type":"furnace",
                "crafting_categories":["smelting"],
                "crafting_speed":1,
                "crafting_speed_status":"measured",
            },
        ],
    })


def _world(*, iron_ore=30, iron_plates=8, copper_plates=8):
    return {
        "tick":1,
        "entity_count":6,
        "entities":[
            {
                "name":"character",
                "type":"character",
                "unit_number":100,
                "position":{"x":0.0,"y":0.0},
                "status":"working",
                "direction":0,
            },
            {
                "name":"burner-mining-drill",
                "type":"mining-drill",
                "unit_number":569,
                "position":{"x":15.0,"y":70.0},
                "status":"working",
                "direction":8,
            },
            {
                "name":"wooden-chest",
                "type":"container",
                "unit_number":570,
                "position":{"x":15.5,"y":71.5},
                "status":"normal",
                "direction":0,
                "contents":[{"name":"iron-ore","count":iron_ore}],
            },
            {
                "name":"stone-furnace",
                "type":"furnace",
                "unit_number":566,
                "position":{"x":20.0,"y":69.0},
                "status":"no_ingredients",
                "direction":0,
                "fuel":[{"name":"coal","count":10}],
                "fuel_remaining":1000.0,
                "craft_output":[{"name":"iron-plate","count":iron_plates}],
            },
            {
                "name":"stone-furnace",
                "type":"furnace",
                "unit_number":565,
                "position":{"x":-63.0,"y":69.0},
                "status":"no_ingredients",
                "direction":0,
                "fuel":[{"name":"coal","count":6}],
                "fuel_remaining":1000.0,
                "craft_output":[{"name":"copper-plate","count":copper_plates}],
            },
            {
                "name":"wooden-chest",
                "type":"container",
                "unit_number":567,
                "position":{"x":-70.5,"y":71.5},
                "status":"normal",
                "direction":0,
                "contents":[{"name":"copper-ore","count":18}],
            },
        ],
        "production":{"produced":{},"consumed":{}},
    }


def _action() -> RepairAction:
    return RepairAction(
        tool="rebuild",
        intent=INTENT_REROUTE_PRODUCER,
        prediction=Prediction("producers_reaching_processor","increase"),
        provides=("material",),
        targets=("u569",),
        arguments={"producers":["u569"]},
    )


def _requests() -> tuple[OptionRequest,ActionRequest]:
    provenance=ActionProvenance(
        requested_by="f5d-test",
        source_component="test",
        code_revision="abc123",
        run_id="run-link",
    )
    option=OptionRequest(
        option_id="run-link:maintenance",
        kind=OptionKind.AUTONOMOUS_MAINTENANCE,
        goal="create persistent endogenous material flow",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=3600),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="run-link:maintenance-action",
        family=ActionFamily.REBUILD,
        intent=INTENT_REROUTE_PRODUCER,
        provenance=provenance,
        targets=("u569",),
    )
    return option,action


def test_f5d_plans_existing_processor_instead_of_new_furnace() -> None:
    plan=plan_existing_processing_link(
        _world(),
        action=_action(),
        catalog=_catalog(),
    )
    assert plan is not None
    assert plan.source_buffer["node_id"]=="u570"
    assert plan.target_processor["node_id"]=="u566"
    assert plan.delivery.mode=="belt"
    assert plan.delivery.belt_count==3
    assert plan.construction_items=={"inserter":2,"transport-belt":3}
    assert plan.plate_requirements=={"iron-plate":14,"copper-plate":3}
    assert plan.bootstrap["iron_ore_to_smelt"]==6
    assert plan.bootstrap["copper_plate_needed"]==3


def test_f5d_material_link_refuses_non_endogenous_bootstrap() -> None:
    plan=plan_existing_processing_link(
        _world(iron_ore=5,iron_plates=0),
        action=_action(),
        catalog=_catalog(),
    )
    assert plan is None


def test_f5d_material_link_option_is_inert_and_external_a2_only() -> None:
    link=plan_existing_processing_link(_world(),action=_action(),catalog=_catalog())
    assert link is not None
    option,action=_requests()
    result=compose_material_link_option(option,action_request=action,link=link)

    assert result.ready is True
    assert result.plan is not None
    assert result.plan.prepared.contract_version=="cortex_structural_ops_v15"
    assert result.plan.prepared.purpose=="infrastructure"
    assert result.plan.action_request.provenance.parent_action_id==option.option_id
    assert result.plan.prepared.preflight["persistent_logistics_created"] is True

    execute_option=OptionRequest(
        option_id=option.option_id,
        kind=option.kind,
        goal=option.goal,
        provenance=option.provenance,
        budget=option.budget,
        authority=ActionAuthority.EXECUTE,
    )
    refused=compose_material_link_option(
        execute_option,
        action_request=action,
        link=link,
    )
    assert refused.ready is False
    assert refused.refusal is not None
    assert refused.refusal.code=="f5d_material_link_execute_forbidden"


def test_f5d_material_link_compiler_freezes_route_and_proves_post_bootstrap_flow() -> None:
    link=plan_existing_processing_link(_world(),action=_action(),catalog=_catalog())
    assert link is not None
    option,action=_requests()
    result=compose_material_link_option(option,action_request=action,link=link)
    assert result.plan is not None

    compiled=compile_structural_action(result.plan.prepared,settle_seconds=60)
    assert compiled.ready is True
    assert compiled.compiled is not None
    code=compiled.compiled.code

    assert "Position(x=15.5,y=71.5)" in code
    assert "Position(x=20.0,y=69.0)" in code
    assert "Position(x=16.5,y=71.5)" in code
    assert "Position(x=17.5,y=71.5)" in code
    assert "Position(x=17.5,y=70.5)" in code
    assert "Position(x=17.5,y=69.5)" in code
    assert "Position(x=18.5,y=69.5)" in code
    assert "quantity=6" in code
    assert "quantity=14" in code
    assert "quantity=3" in code
    assert "cortex_link_source_preflow" in code
    assert "cortex_link_target_preflow" in code
    assert (
        "cortex_material_link_source_after<cortex_link_source_preflow"
        in code
    )
    assert (
        "cortex_material_link_output_after>cortex_link_target_preflow"
        in code
    )
    assert "autonomous_material_link_succeeded" in code


def test_f5d_runner_filters_ucb_through_hard_feasibility(tmp_path) -> None:
    import importlib.util

    from factorio_ai_lab.cortex.f5d_autonomy import PersistentUCBPolicy, decide

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5d_autonomy.py"
    )
    spec=importlib.util.spec_from_file_location("f5d_link_runner_test",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    snapshot=_world()
    decision=decide(snapshot,PersistentUCBPolicy(tmp_path/"policy.json"))
    selected,context,feasibility=module._resolve_live_candidate(
        snapshot,
        decision,
        _catalog().payload,
    )

    assert selected is not None
    assert selected.action.key=="rebuild:reroute_producer_logistics"
    assert context is not None and context["kind"]=="material_link"
    assert context["link"].delivery.belt_count==3
    row=next(
        item for item in feasibility
        if item["action_key"]=="rebuild:reroute_producer_logistics"
    )
    assert row["feasible"] is True


def test_f5d_runner_preserves_selected_action_family() -> None:
    import importlib.util
    from types import SimpleNamespace

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5d_autonomy.py"
    )
    spec=importlib.util.spec_from_file_location("f5d_link_request_test",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    selected=SimpleNamespace(action=_action())
    _,request=module._option_requests(
        run_id="run-link",
        commit="abc123",
        selected=selected,
    )
    assert request.family is ActionFamily.REBUILD
    assert request.intent==INTENT_REROUTE_PRODUCER
