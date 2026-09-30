from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.rollback_recovery_option import (
    RollbackRecoveryOptionPlan,
    compose_rollback_recovery_option,
)
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)

ROOT=Path(__file__).resolve().parents[1]


def recovery_plan() -> RollbackRecoveryOptionPlan:
    option=OptionRequest(
        option_id="f5c-copper-furnace-recovery",
        kind=OptionKind.RESTORE_PROMOTED_ENTITY,
        goal="restore promoted copper furnace",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="tests.test_cortex_f5c_rollback_recovery",
            code_revision="recovery-test-sha",
            run_id="recovery-test-run",
        ),
        budget=OptionBudget(requested_ticks=20*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-copper-furnace-recovery-action",
        family=ActionFamily.PLACEMENT,
        intent="restore promoted copper furnace",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="tests.test_cortex_f5c_rollback_recovery",
            code_revision="recovery-test-sha",
            run_id="recovery-test-run",
        ),
        requires=("promoted_copper_chain_evidence","live_endogenous_stone"),
        provides=("restored_promoted_copper_furnace",),
    )
    result=compose_rollback_recovery_option(
        option,
        action_request=action,
        positions={
            "stone":(-46.5,-0.5),
            "copper_furnace":(-63.0,69.0),
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_recovery_option_is_inert_bounded_and_has_no_generic_output_guard() -> None:
    plan=recovery_plan()
    assert plan.request.kind is OptionKind.RESTORE_PROMOTED_ENTITY
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v11"
    assert plan.prepared.preflight["promotion_credit"] is False
    assert plan.prepared.preflight["external_resource_injection"] is False
    assert {row.name for row in plan.termination_conditions}=={
        "promoted_entity_restored"
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_recovery_uses_endogenous_stone_and_exact_promoted_position() -> None:
    plan=recovery_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=20)
    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "cortex_mine_exact_resource(" in code
    assert "harvest_resource(" not in code
    assert "cortex_recovery_stone_inventory_growth" in code
    assert "rollback recovery native stone mining incomplete" in code
    assert "quantity=5" in code
    assert "radius=1.5" in code
    assert "Position(x=-46.5,y=-0.5)" in code
    assert "craft_item(Prototype.StoneFurnace,quantity=1)" in code
    assert "Position(x=-63.0,y=69.0)" in code
    assert "cortex_promoted_entity_restored" in code
    assert "insert_item(" not in code


def test_copper_furnace_recovery_runner_is_one_shot_and_no_reset() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_repair_copper_furnace.py").read_text()
    assert "F5BoundedAuthorityBridge" in source
    assert "FactorioWorldLease" in source
    assert "OptionKind.RESTORE_PROMOTED_ENTITY" in source
    assert '"promotion_credit":False' in source
    assert '"external_resource_injection":False' in source
    assert '"world_reset":False' in source
    assert "executor.reset(" not in source
    assert "set_research(" not in source
    assert "STONE_POS=" in source
    assert "bind_exact_resource_mining_tool" in source
    assert '"fle_transactional_resource_mining_tool"' in source
    assert 'positions["stone"]=resolved_stone' in source
    assert '"stone_anchor"' in source
    assert '"stone_harvest_position"' in source


def test_supervisor_routes_missing_copper_furnace_to_technical_recovery() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()
    assert "promoted entity absent from WORLD: copper_furnace" in source
    assert "run_cortex_f5c_repair_copper_furnace.py" in source
    assert 'last_frontier="copper_furnace_recovery"' in source


def test_recovery_sources_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/rollback_recovery_option.py",
        "src/factorio_ai_lab/cortex/rollback_recovery_structural_execute.py",
        "scripts/run_cortex_f5c_repair_copper_furnace.py",
        "scripts/run_cortex_supervisor.py",
    ):
        ast.parse((ROOT/name).read_text())
