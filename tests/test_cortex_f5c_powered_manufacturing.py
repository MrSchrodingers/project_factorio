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
from factorio_ai_lab.cortex.powered_manufacturing_option import (
    PoweredManufacturingOptionPlan,
    compose_powered_manufacturing_option,
)
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)

ROOT=Path(__file__).resolve().parents[1]


def powered_plan() -> PoweredManufacturingOptionPlan:
    option=OptionRequest(
        option_id="f5c-powered-manufacturing",
        kind=OptionKind.ESTABLISH_POWERED_MANUFACTURING,
        goal="prove causal powered manufacturing",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_powered_manufacturing",
            code_revision="powered-test-sha",
            run_id="powered-test-run",
        ),
        budget=OptionBudget(requested_ticks=720*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-powered-manufacturing-action",
        family=ActionFamily.CRAFT,
        intent="research Automation and manufacture two products",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_powered_manufacturing",
            code_revision="powered-test-sha",
            run_id="powered-test-run",
        ),
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting",
            "steam_power","copper_chain","automation_science",
        ),
        provides=("powered_manufacturing",),
    )
    positions={
        "iron_extractor":(15.0,70.0),
        "iron_buffer":(15.5,71.5),
        "iron_furnace":(20.0,69.0),
        "coal_extractor":(15.0,-4.0),
        "coal_buffer":(15.5,-2.5),
        "boiler":(-4.0,3.5),
        "steam_engine":(2.5,9.5),
        "power_pole":(5.5,8.5),
        "copper_extractor":(-71.0,70.0),
        "copper_buffer":(-70.5,71.5),
        "copper_furnace":(-63.0,69.0),
    }
    result=compose_powered_manufacturing_option(
        option,action_request=action,positions=positions
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_powered_manufacturing_option_is_inert_and_schema_aligned() -> None:
    plan=powered_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_POWERED_MANUFACTURING
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v10"
    assert plan.prepared.binding=="cortex.structural.powered_manufacturing"
    assert plan.prepared.preflight["native_research_queue"] is True
    assert plan.prepared.preflight["direct_research_completion_mutation"] is False
    assert {row.name for row in plan.termination_conditions}=={
        "assembler_powered",
        "iron_gear_output_positive",
        "electronic_circuit_output_positive",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_powered_manufacturing_is_causal_and_dual_output() -> None:
    plan=powered_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=720)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "set_research(Technology.Automation)" in code
    assert "get_research_progress(Technology.Automation)" in code
    assert ".researched=" not in code
    assert "force.technologies" not in code
    assert "Prototype.Lab" in code
    assert "Prototype.AssemblingMachine1" in code
    assert "Prototype.IronGearWheel" in code
    assert "Prototype.ElectronicCircuit" in code
    assert "cortex_assembler_powered" in code
    assert "cortex_gear_output" in code
    assert "cortex_circuit_output" in code
    assert "cortex_coal_amplification_rounds" in code
    assert "sleep(30)" in code
    assert "sleep(200)" in code
    assert "sleep(115)" in code
    assert code.count("quantity=33")>=2
    assert code.count("sleep(115)")==2
    assert "cortex_science_replenished" in code
    assert "cortex_automation_science_survives" in code
    assert "cortex_steam_power_survives" in code


def test_powered_manufacturing_budget_covers_internal_windows() -> None:
    plan=powered_plan()

    compiled=compile_structural_action(plan.prepared,settle_seconds=716)
    assert compiled.ready is False
    assert compiled.refusal is not None
    assert "717" in compiled.refusal.detail

    compiled=compile_structural_action(plan.prepared,settle_seconds=720)
    assert compiled.ready is True


def test_powered_runner_is_one_shot_no_reset_and_native_research() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_powered_manufacturing.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "FactorioWorldLease" in source
    assert "use_checkpoint_for_action=False" in source
    assert '"world_reset":False' in source
    assert '"external_resource_injection":False' in source
    assert '"native_research_queue":True' in source
    assert '"direct_research_completion_mutation":False' in source
    assert "OptionKind.ESTABLISH_POWERED_MANUFACTURING" in source
    assert '"world_factory_fingerprint"' in source
    assert 'name=="character"' in source
    assert 'after_attach.get("entity_count")!=pf["world_entity_count"]' not in source
    assert "executor.reset(" not in source


def test_powered_source_files_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/powered_manufacturing_option.py",
        "src/factorio_ai_lab/cortex/powered_manufacturing_structural_execute.py",
        "scripts/run_cortex_f5c_powered_manufacturing.py",
    ):
        ast.parse((ROOT/name).read_text())
