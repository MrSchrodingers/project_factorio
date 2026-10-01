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
        budget=OptionBudget(requested_ticks=240*60),
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
            "iron_resource":(15.5,70.5),
            "coal_extractor":(15.0,-4.0),
            "coal_buffer":(15.5,-2.5),
            "coal_quarantine":(15.5,-5.5),
            "iron_extractor":(15.0,70.0),
            "iron_buffer":(15.5,71.5),
            "iron_furnace":(20.0,69.0),
            "boiler":(-4.0,3.5),
            "copper_extractor":(-71.0,70.0),
            "copper_buffer":(-70.5,71.5),
            "copper_furnace":(-63.0,69.0),
        },
        missing_components=(
            "iron_extractor","iron_buffer","iron_furnace",
            "coal_extractor","coal_buffer","coal_quarantine",
            "copper_furnace",
        ),
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
        "iron_extraction_restored",
        "iron_smelting_restored",
        "coal_stock_recovered",
        "copper_smelting_restored",
        "science_buffer_intact",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_recovery_rebuilds_exact_promoted_baseline_endogenously() -> None:
    plan=recovery_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=240)
    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "cortex_mine_exact_resource(" in code
    assert "'wood',quantity=6,radius=3" in code
    assert "cortex_transfer_exact_item(" in code
    assert "cortex_inspect_exact_item(" in code
    for legacy_probe in (
        "inspect_inventory(cortex_iron_furnace)",
        "inspect_inventory(cortex_coal_buffer)",
        "inspect_inventory(cortex_iron_buffer)",
        "inspect_inventory(cortex_coal_quarantine)",
        "inspect_inventory(cortex_copper_furnace)",
        "inspect_inventory(cortex_copper_buffer)",
    ):
        assert legacy_probe not in code
    assert "cortex_deposit_exact_item(" in code
    assert "cortex_craft_exact_item(" in code
    assert "cortex_place_exact_entity(" in code
    assert "cortex_recovery_iron_ore_harvested" in code
    assert "cortex_recovery_iron_stock_final" in code
    assert "extract_item(" not in code
    assert "craft_item(" not in code
    assert "place_entity(" not in code
    assert "'stone',quantity=10,radius=3" in code
    assert "'stone',quantity=5,radius=3" in code
    assert "'coal',quantity=10,radius=3" in code
    assert "'coal',quantity=19,radius=3" in code
    assert "cortex_recovery_seed_coal_harvested" in code
    assert code.index("cortex_recovery_seed_coal_harvested") < code.index("initial_items={'coal':2}")
    assert "'iron-ore',quantity=18,radius=3" in code
    assert "cortex_craft_exact_item('burner-mining-drill',quantity=2)" in code
    assert "cortex_craft_exact_item('wooden-chest',quantity=3)" in code
    assert "cortex_craft_exact_item('stone-furnace',quantity=2)" in code
    assert code.count("cortex_craft_exact_item('stone-furnace',quantity=1)") >= 2
    assert "Position(x=20.0,y=69.0)" in code
    assert "cortex_recovery_drill_furnaces_before_drills" in code
    assert "Position(x=15.0,y=-4.0)" in code
    assert "Position(x=15.5,y=-2.5)" in code
    assert "Position(x=15.5,y=-5.5)" in code
    assert "Position(x=15.0,y=70.0)" in code
    assert "Position(x=15.5,y=71.5)" in code
    assert "Position(x=-63.0,y=69.0)" in code
    assert "baseline recovery quarantine must remain empty" in code
    assert "cortex_iron_extraction_restored" in code
    assert "cortex_iron_smelting_restored" in code
    assert "cortex_recovery_endogenous_coal_buffered" in code
    assert "cortex_recovery_endogenous_coal_remainder" not in code
    assert "quantity=5,radius=3" in code
    assert "cortex_recovery_iron_buffer_seeded" in code
    assert "initial_items={'coal':2}" in code
    assert "initial_items={'coal':1}" in code
    assert "initial_items={'iron-ore':5}" in code
    coal_extractor_place=code.index("Position(x=15.0,y=-4.0),'burner-mining-drill'")
    iron_extractor_place=code.index("Position(x=15.0,y=70.0),'burner-mining-drill'")
    coal_buffer_place=code.index("Position(x=15.5,y=-2.5),'wooden-chest'")
    iron_buffer_place=code.index("Position(x=15.5,y=71.5),'wooden-chest'")
    quarantine_place=code.index("Position(x=15.5,y=-5.5),'wooden-chest'")
    assert code.index("cortex_recovery_seed_coal_harvested=") < iron_buffer_place
    assert code.index("cortex_recovery_iron_buffer_seed_harvested=") < iron_buffer_place
    assert iron_buffer_place < quarantine_place < coal_buffer_place
    assert coal_buffer_place < iron_extractor_place < coal_extractor_place
    assert "initial_items={'coal':16}" in code
    assert "initial_items={'iron-ore':5}" in code
    assert "initial_items={'coal':1}" in code
    assert "initial_items={'coal':2}" in code
    assert "cortex_recovery_coal_seed=cortex_deposit_exact_item" not in code
    assert "cortex_recovery_iron_seed=cortex_deposit_exact_item" not in code
    assert "sleep(1)" in code
    assert "cortex_coal_stock_recovered" in code
    assert "cortex_copper_smelting_restored" in code
    assert code.index("cortex_recovery_copper_plate_count=") < code.index("cortex_recovery_seed_coal_harvested=")
    assert "cortex_science_buffer_intact" in code
    assert "cortex_processor_output" not in code
    assert code.index("cortex_craft_exact_item('stone-furnace',quantity=2)") < code.index("cortex_craft_exact_item('burner-mining-drill',quantity=2)")
    assert code.index("cortex_craft_exact_item('burner-mining-drill',quantity=2)") < code.rindex("cortex_craft_exact_item('stone-furnace',quantity=1)")
    assert "Prototype.Coal" in code


def test_promoted_baseline_recovery_runner_is_one_shot_no_reset_no_credit() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_reconcile_promoted_baseline.py").read_text()
    assert "F5BoundedAuthorityBridge" in source
    assert "bind_exact_item_transfer_tool" in source
    assert "bind_exact_item_inspect_tool" in source
    assert "bind_exact_item_deposit_tool" in source
    assert "bind_exact_craft_tool" in source
    assert "bind_exact_place_tool" in source
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
