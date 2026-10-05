from __future__ import annotations

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.f5d_maintenance_option import (
    compose_autonomous_refuel_option,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
)
from factorio_ai_lab.cortex.structural_execute import compile_structural_action


def _requests() -> tuple[OptionRequest, ActionRequest]:
    provenance=ActionProvenance(
        requested_by="f5d-test",
        source_component="test",
        code_revision="abc123",
        run_id="run-1",
    )
    option=OptionRequest(
        option_id="run-1:maintenance",
        kind=OptionKind.AUTONOMOUS_MAINTENANCE,
        goal="recover measured fuel starvation from endogenous coal",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=60*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="run-1:maintenance-action",
        family=ActionFamily.RESUPPLY,
        intent="insert_fuel_from_world_container",
        provenance=provenance,
    )
    return option,action


def test_f5d_maintenance_composition_is_inert_and_frozen() -> None:
    option,action=_requests()
    result=compose_autonomous_refuel_option(
        option,
        action_request=action,
        source={"entity_name":"wooden-chest","x":14.5,"y":6.5},
        targets=[
            {"entity_name":"burner-mining-drill","x":15.0,"y":-4.0},
            {"entity_name":"burner-mining-drill","x":-71.0,"y":70.0},
            {"entity_name":"burner-mining-drill","x":15.0,"y":70.0},
        ],
        dose=8,
        source_reserve=100,
    )
    assert result.ready is True
    assert result.plan is not None
    assert result.plan.world_mutation is False
    assert result.plan.prepared.purpose=="repair"
    assert result.plan.execute_authorized is False
    assert result.plan.action_request.provenance.parent_action_id==option.option_id
    assert result.plan.frozen_inputs["dose"]==8
    assert len(result.plan.frozen_inputs["targets"])==3


def test_f5d_maintenance_compile_is_exact_and_self_validating() -> None:
    option,action=_requests()
    result=compose_autonomous_refuel_option(
        option,
        action_request=action,
        source={"entity_name":"wooden-chest","x":14.5,"y":6.5},
        targets=[
            {"entity_name":"burner-mining-drill","x":15.0,"y":-4.0},
            {"entity_name":"burner-mining-drill","x":-71.0,"y":70.0},
            {"entity_name":"burner-mining-drill","x":15.0,"y":70.0},
        ],
        dose=8,
        source_reserve=100,
    )
    assert result.plan is not None
    compiled=compile_structural_action(result.plan.prepared,settle_seconds=60)
    assert compiled.ready is True
    assert compiled.compiled is not None
    code=compiled.compiled.code

    assert "Position(x=14.5,y=6.5)" in code
    assert "Position(x=15.0,y=-4.0)" in code
    assert "Position(x=-71.0,y=70.0)" in code
    assert "Position(x=15.0,y=70.0)" in code
    assert "cortex_refuel_required=24" in code
    assert "cortex_refuel_required+100" in code
    assert "cortex_autonomous_refuel_succeeded" in code
    assert "cortex_processor_output" not in code


def test_f5d_policy_cannot_compose_execute_authority() -> None:
    option,action=_requests()
    execute_option=OptionRequest(
        option_id=option.option_id,
        kind=option.kind,
        goal=option.goal,
        provenance=option.provenance,
        budget=option.budget,
        authority=ActionAuthority.EXECUTE,
    )
    result=compose_autonomous_refuel_option(
        execute_option,
        action_request=action,
        source={"entity_name":"wooden-chest","x":14.5,"y":6.5},
        targets=[{"entity_name":"burner-mining-drill","x":15.0,"y":-4.0}],
    )
    assert result.ready is False
    assert result.refusal is not None
    assert result.refusal.code=="f5d_maintenance_execute_forbidden"
