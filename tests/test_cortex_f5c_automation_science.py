from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.automation_science_option import (
    AutomationScienceOptionPlan,
    compose_automation_science_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)

ROOT=Path(__file__).resolve().parents[1]


def science_plan() -> AutomationScienceOptionPlan:
    option=OptionRequest(
        option_id="f5c-automation-science",
        kind=OptionKind.ESTABLISH_AUTOMATION_SCIENCE,
        goal="produce endogenous automation science",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_automation_science",
            code_revision="science-test-sha",
            run_id="science-test-run",
        ),
        budget=OptionBudget(requested_ticks=220*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-automation-science-action",
        family=ActionFamily.CRAFT,
        intent="craft persistent automation science from endogenous inputs",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_automation_science",
            code_revision="science-test-sha",
            run_id="science-test-run",
        ),
        requires=(
            "iron_extraction",
            "coal_self_sufficiency",
            "iron_smelting",
            "steam_power",
            "copper_chain",
        ),
        provides=("automation_science",),
    )
    result=compose_automation_science_option(
        option,
        action_request=action,
        positions={
            "iron_extractor":(15.0,70.0),
            "iron_buffer":(15.5,71.5),
            "iron_furnace":(20.0,69.0),
            "coal_extractor":(15.0,-4.0),
            "coal_buffer":(15.5,-2.5),
            "boiler":(-4.0,3.5),
            "steam_engine":(2.5,9.5),
            "copper_extractor":(-71.0,70.0),
            "copper_buffer":(-70.5,71.5),
            "copper_furnace":(-63.0,69.0),
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_automation_science_option_is_inert_schema_aligned_and_no_assembler() -> None:
    plan=science_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_AUTOMATION_SCIENCE
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v9"
    assert plan.prepared.binding=="cortex.structural.automation_science"
    assert plan.prepared.preflight["persistent_science_inventory"] is True
    assert plan.prepared.preflight["uses_powered_manufacturing"] is False
    names={row.name for row in plan.termination_conditions}
    assert names=={
        "automation_science_output_positive",
        "inputs_endogenous",
        "production_sustained",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_automation_science_uses_two_endogenous_batches_and_survival() -> None:
    plan=science_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=220)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "set_research(" not in code
    assert "AssemblingMachine" not in code
    assert "set_entity_recipe(" not in code
    assert "Prototype.IronGearWheel" in code
    assert code.count(
        "craft_item(Prototype.AutomationSciencePack,quantity=5)"
    )==2
    assert "cortex_science_batch1" in code
    assert "cortex_science_batch2" in code
    assert "cortex_science_buffer_count" in code
    assert "cortex_persistent_science_buffer" in code
    assert "insert_item(" in code
    assert "cortex_inputs_endogenous" in code
    assert "cortex_production_sustained" in code
    assert "cortex_copper_chain_survives" in code
    assert "cortex_steam_power_survives" in code
    assert "quantity=2" in code
    assert "sleep(45)" in code
    assert "cortex_iron_survival_growth" in code
    assert "cortex_coal_survival_growth" in code
    assert "cortex_copper_survival_growth" in code
    assert "cortex_copper_smelting_growth" in code


def test_automation_science_budget_covers_all_internal_causal_windows() -> None:
    plan=science_plan()

    compiled=compile_structural_action(plan.prepared,settle_seconds=163)
    assert compiled.ready is False
    assert compiled.refusal is not None
    assert "164" in compiled.refusal.detail

    compiled=compile_structural_action(plan.prepared,settle_seconds=220)
    assert compiled.ready is True


def test_automation_science_runner_is_bounded_no_reset_and_no_powered_manufacturing() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_automation_science.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "FactorioWorldLease" in source
    assert "use_checkpoint_for_action=False" in source
    assert '"world_reset":False' in source
    assert '"external_resource_injection":False' in source
    assert '"persistent_science_inventory":True' in source
    assert '"persistent_science_buffer"' in source
    assert '"continuity_gate":continuity_gate' in source
    assert '"uses_powered_manufacturing":False' in source
    assert "executor.reset(" not in source
    assert "set_research(" not in source
    assert "AssemblingMachine" not in source
    assert "OptionKind.ESTABLISH_AUTOMATION_SCIENCE" in source


def test_automation_science_source_files_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/automation_science_option.py",
        "src/factorio_ai_lab/cortex/automation_science_structural_execute.py",
        "scripts/run_cortex_f5c_automation_science.py",
    ):
        ast.parse((ROOT/name).read_text())
