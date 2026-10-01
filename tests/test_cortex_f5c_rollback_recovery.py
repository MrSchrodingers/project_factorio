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
        option_id="f5c-promoted-baseline-recovery",
        kind=OptionKind.RESTORE_PROMOTED_ENTITY,
        goal="restore rollback-damaged promoted baseline",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="tests.test_cortex_f5c_rollback_recovery",
            code_revision="recovery-test-sha",
            run_id="recovery-test-run",
        ),
        budget=OptionBudget(requested_ticks=180*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-promoted-baseline-recovery-action",
        family=ActionFamily.PLACEMENT,
        intent="restore promoted baseline from endogenous materials",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="tests.test_cortex_f5c_rollback_recovery",
            code_revision="recovery-test-sha",
            run_id="recovery-test-run",
        ),
        requires=("six_promoted_capabilities","pre_corruption_world_fingerprint"),
        provides=("restored_promoted_physical_baseline",),
    )
    result=compose_rollback_recovery_option(
        option,
        action_request=action,
        positions={
            "stone":(-46.5,-0.5),
            "wood":(0.12109375,-18.68359375),
            "coal_resource":(15.5,-0.5),
            "coal_extractor":(15.0,-4.0),
            "coal_buffer":(15.5,-2.5),
            "coal_quarantine":(15.5,-5.5),
            "iron_buffer":(15.5,71.5),
            "iron_furnace":(20.0,69.0),
            "copper_buffer":(-70.5,71.5),
            "copper_furnace":(-63.0,69.0),
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_recovery_option_is_inert_bounded_and_has_no_promotion_credit() -> None:
    plan=recovery_plan()
    assert plan.request.kind is OptionKind.RESTORE_PROMOTED_ENTITY
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v11"
    assert plan.prepared.preflight["promotion_credit"] is False
    assert plan.prepared.preflight["external_resource_injection"] is False
    assert plan.prepared.preflight["quarantine_recreated_empty"] is True
    assert {row.name for row in plan.termination_conditions}=={
        "promoted_baseline_restored",
        "coal_stock_recovered",
        "copper_smelting_restored",
        "science_buffer_intact",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_recovery_rebuilds_exact_promoted_baseline_endogenously() -> None:
    plan=recovery_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=180)
    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "cortex_mine_exact_resource(" in code
    assert "cortex_transfer_exact_item(" in code
    assert "cortex_recovery_iron_existing_transfer" in code
    assert "cortex_recovery_iron_smelt_transfer" in code
    assert "extract_item(" not in code
    assert "'stone',quantity=10,radius=3" in code
    assert "'coal',quantity=4,radius=3" in code
    assert "harvest_resource(" in code
    assert "quantity=4,radius=24" in code
    assert "craft_item(Prototype.BurnerMiningDrill,quantity=1)" in code
    assert "craft_item(Prototype.WoodenChest,quantity=2)" in code
    assert "craft_item(Prototype.StoneFurnace,quantity=2)" in code
    assert "cortex_recovery_reserved_furnaces" in code
    assert "Position(x=15.0,y=-4.0)" in code
    assert "Position(x=15.5,y=-2.5)" in code
    assert "Position(x=15.5,y=-5.5)" in code
    assert "Position(x=-63.0,y=69.0)" in code
    assert "baseline recovery quarantine must remain empty" in code
    assert "cortex_coal_stock_recovered" in code
    assert "cortex_copper_smelting_restored" in code
    assert "cortex_science_buffer_intact" in code
    assert code.index("craft_item(Prototype.StoneFurnace,quantity=2)") < code.index("craft_item(Prototype.BurnerMiningDrill,quantity=1)")
    assert "cortex_coal_extractor=insert_item(" in code
    assert "Prototype.Coal" in code


def test_promoted_baseline_recovery_runner_is_one_shot_no_reset_no_credit() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_reconcile_promoted_baseline.py").read_text()
    assert "F5BoundedAuthorityBridge" in source
    assert "bind_exact_item_transfer_tool" in source
    assert "FactorioWorldLease" in source
    assert "OptionKind.RESTORE_PROMOTED_ENTITY" in source
    assert '"promotion_credit":False' in source
    assert '"external_resource_injection":False' in source
    assert '"world_reset":False' in source
    assert "WOOD_POS=" in source
    assert "no live wood within accepted bootstrap radius" in source
    assert 'record["wood_harvest_position"]' in source
    assert "use_checkpoint_for_action=False" in source
    assert "capability_promoted" in source
    assert '"capability_promoted":None' in source
    assert "executor.reset(" not in source


def test_supervisor_routes_any_missing_promoted_entity_to_baseline_recovery() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()
    assert '"promoted entity absent from WORLD:" in payload["detail"]' in source
    assert "run_cortex_f5c_reconcile_promoted_baseline.py" in source
    assert 'last_frontier="promoted_baseline_recovery"' in source


def test_recovery_sources_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/rollback_recovery_option.py",
        "src/factorio_ai_lab/cortex/rollback_recovery_structural_execute.py",
        "scripts/run_cortex_f5c_reconcile_promoted_baseline.py",
        "scripts/run_cortex_supervisor.py",
    ):
        ast.parse((ROOT/name).read_text())
