from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.iron_smelting_option import (
    IronSmeltingOptionPlan,
    compose_iron_smelting_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.planning.placement import OUTCOME_BUILD, PlacementPlan

ROOT=Path(__file__).resolve().parents[1]


def iron_smelting_plan() -> IronSmeltingOptionPlan:
    option=OptionRequest(
        option_id="f5c-iron-smelting",
        kind=OptionKind.ESTABLISH_IRON_SMELTING,
        goal="establish endogenous iron smelting",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_iron_smelting",
            code_revision="iron-smelting-test-sha",
            run_id="iron-smelting-test-run",
        ),
        budget=OptionBudget(requested_ticks=120*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-iron-smelting-action",
        family=ActionFamily.PLACEMENT,
        intent="establish persistent endogenous iron smelting",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_iron_smelting",
            code_revision="iron-smelting-test-sha",
            run_id="iron-smelting-test-run",
        ),
        requires=("iron_extraction","coal_self_sufficiency"),
        provides=("iron_smelting",),
    )
    result=compose_iron_smelting_option(
        option,
        action_request=action,
        furnace_placement=PlacementPlan(
            outcome=OUTCOME_BUILD,
            position=(20.0,72.0),
            reason="test",
            scanned=1,
            resource_tiles=0,
            resource_cost=0.0,
            resource_names=(),
            resource_unsurveyed=0,
        ),
        stone_route={
            "resource":"stone",
            "quantity":5,
            "position":{"x":-46.5,"y":-0.5},
            "validated_path_waypoints":51,
        },
        incumbent_iron_extractor_position=(15.0,70.0),
        incumbent_iron_buffer_position=(15.5,71.5),
        incumbent_coal_extractor_position=(15.0,-4.0),
        incumbent_coal_buffer_position=(15.5,-2.5),
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_iron_smelting_option_is_inert_and_schema_aligned() -> None:
    plan=iron_smelting_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_IRON_SMELTING
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v6"
    assert plan.prepared.binding=="cortex.structural.iron_smelting"
    assert plan.prepared.preflight["external_resource_injection"] is False
    names={row.name for row in plan.termination_conditions}
    assert {
        "iron_ore_input_live",
        "furnace_operational",
        "iron_plate_output_positive",
        "iron_extraction_survives",
        "coal_self_sufficiency_survives",
    }.issubset(names)


def test_compiled_iron_smelting_uses_endogenous_buffers_and_survival_window() -> None:
    plan=iron_smelting_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=120)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "nearest(" not in code
    assert "move_to(" not in code
    assert "cortex_fast_reposition(" in code
    assert "cortex_iron_drawn=extract_item(" in code
    assert "cortex_coal_drawn=extract_item(" in code
    assert "cortex_iron_furnace=place_entity(" in code
    assert "pickup_entity(cortex_iron_furnace)" not in code
    assert "cortex_iron_plate_output_positive" in code
    assert "cortex_iron_survival_growth" in code
    assert "cortex_coal_survival_growth" in code
    assert "sleep(20)" in code


def test_iron_smelting_refuses_unvalidated_stone_route() -> None:
    plan=iron_smelting_plan()
    option=plan.request
    action=ActionRequest(
        action_id="refusal-action",
        family=ActionFamily.PLACEMENT,
        intent="refusal",
        provenance=ActionProvenance(
            requested_by="test",
            source_component="test",
            code_revision=option.provenance.code_revision,
            run_id=option.provenance.run_id,
        ),
    )
    result=compose_iron_smelting_option(
        option,
        action_request=action,
        furnace_placement=PlacementPlan(
            outcome=OUTCOME_BUILD,
            position=(20.0,72.0),
        ),
        stone_route={"resource":"stone","quantity":5},
        incumbent_iron_extractor_position=(15.0,70.0),
        incumbent_iron_buffer_position=(15.5,71.5),
        incumbent_coal_extractor_position=(15.0,-4.0),
        incumbent_coal_buffer_position=(15.5,-2.5),
    )

    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code=="f5c_iron_smelting_stone_route_required"


def test_iron_smelting_runner_remains_bounded_and_no_reset() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_iron_smelting.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "executor.reset(" not in source
    assert "use_checkpoint_for_action=False" in source
    assert '"automatic_retry":False' in source
    assert "bind_fast_reposition_tool" in source
    assert "external_resource_injection" in source


def test_phase_state_requires_full_smelting_evidence_before_promotion() -> None:
    source=(ROOT/"scripts"/"cortex_phase_state.py").read_text()

    assert '"cortex_f5c_iron_smelting_v1"' in source
    assert 'payload.get("capability_promoted")=="iron_smelting"' in source
    assert 'gate.get("iron_ore_input_live") is True' in source
    assert 'gate.get("furnace_operational") is True' in source
    assert 'gate.get("iron_plate_output_positive") is True' in source
    assert 'survival.get("iron_extraction_survives") is True' in source
    assert 'survival.get("coal_self_sufficiency_survives") is True' in source
    assert (
        '["iron_extraction","coal_self_sufficiency","iron_smelting"]'
        in source
    )


def test_smelting_planner_is_bounded_and_persists_pregrant_progress() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_iron_smelting.py").read_text()

    assert "STONE_FALLBACK_CANDIDATE_LIMIT=8" in source
    assert "preferred_stone=_preferred_stone_from_coal_artifact" in source
    assert '"planning_stage":"starting"' in source
    assert 'record["planning_stage"]="stone_route_validation"' in source
    assert 'record["planning_stage"]="stone_route_validated"' in source
    assert source.index("_write(artifact,record)")<source.index(
        "with FactorioWorldLease"
    )
