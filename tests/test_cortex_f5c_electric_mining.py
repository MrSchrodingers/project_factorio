from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.electric_mining_option import (
    compose_electric_mining_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)

ROOT=Path(__file__).resolve().parents[1]


def electric_plan():
    provenance=ActionProvenance(
        requested_by="f5-c-deterministic-baseline",
        source_component="tests.test_cortex_f5c_electric_mining",
        code_revision="electric-test-sha",
        run_id="electric-test-run",
    )
    option=OptionRequest(
        option_id="f5c-electric-mining",
        kind=OptionKind.ESTABLISH_ELECTRIC_MINING,
        goal="prove causal powered electric extraction",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=1080*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-electric-mining-action",
        family=ActionFamily.CRAFT,
        intent="research and establish electric mining",
        provenance=provenance,
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting",
            "steam_power","copper_chain","automation_science",
            "powered_manufacturing",
        ),
        provides=("electric_mining",),
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
        "lab":(8.5,8.5),
        "assembler":(5.5,5.5),
        "electric_pole":(11.5,8.5),
        "electric_drill":(14.5,8.5),
        "electric_buffer":(14.5,6.5),
    }
    result=compose_electric_mining_option(
        option,
        action_request=action,
        positions=positions,
        bootstrap_tree={
            "name":"tree-01",
            "x":1.92578125,
            "y":-18.68359375,
            "direction":0,
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_electric_mining_option_is_inert_and_schema_aligned() -> None:
    plan=electric_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_ELECTRIC_MINING
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v12"
    assert plan.prepared.binding=="cortex.structural.electric_mining"
    assert plan.prepared.preflight["native_research_queue"] is True
    assert (
        plan.prepared.preflight["natural_resource_transaction"]
        =="exact_tree_restore_on_rejection"
    )
    assert {row.name for row in plan.termination_conditions}=={
        "electric_drill_powered",
        "ore_output_positive",
        "power_survives_load",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_electric_mining_is_causal_and_self_validating() -> None:
    plan=electric_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=1080)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "set_research('electric-mining-drill')" in code
    assert "get_research_progress('electric-mining-drill')" in code
    assert ".researched=" not in code
    assert "force.technologies" not in code
    assert "harvest_resource(" in code
    assert "quantity=4,radius=0.25" in code
    assert code.count("cortex_place_exact_entity(")==3
    assert "Position(x=11.5,y=8.5)" in code
    assert "Position(x=14.5,y=8.5)" in code
    assert "Position(x=14.5,y=6.5)" in code
    assert "cortex_electric_drop_position" in code
    assert "output chest is not aligned to drill output" in code
    assert "cortex_electric_output_growth" in code
    assert "cortex_electric_drill_powered" in code
    assert "cortex_power_survives_load" in code
    assert "cortex_powered_manufacturing_survives" in code
    assert "cortex_automation_science_survives" in code
    assert "cortex_processor_output" not in code
    assert "cortex_expected_product" not in code


def test_electric_mining_budget_covers_maximum_internal_windows() -> None:
    plan=electric_plan()

    refused=compile_structural_action(plan.prepared,settle_seconds=1078)
    assert refused.ready is False
    assert refused.refusal is not None
    assert "1079" in refused.refusal.detail

    accepted=compile_structural_action(plan.prepared,settle_seconds=1079)
    assert accepted.ready is True


def test_electric_runner_is_one_shot_with_natural_rollback() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_electric_mining.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "FactorioWorldLease" in source
    assert "use_checkpoint_for_action=False" in source
    assert '"world_reset":False' in source
    assert '"external_resource_injection":False' in source
    assert '"native_research_queue":True' in source
    assert '"direct_research_completion_mutation":False' in source
    assert "OptionKind.ESTABLISH_ELECTRIC_MINING" in source
    assert "bind_exact_place_tool(env)" in source
    assert "bind_safe_score_tool(env)" in source
    assert "bind_tick_accurate_sleep_tool(env)" in source
    assert "_ensure_factorio_unpaused(instance)" in source
    assert "_set_autosave_interval(control,0)" in source
    assert "_pause_factorio_for_quiesce(" in source
    assert "_quiesce_fle_storage(" in source
    assert "_restore_bootstrap_tree(" in source
    assert "exact_tree_restore_on_rejection" in source
    assert "option_execution_attempts" in source
    assert '"automatic_retry":False' in source
    checkpoint=source.index("checkpoint=GameState.from_instance(instance)")
    execute=source.index("execution=bridge.execute_a2(")
    assert checkpoint < execute
    assert "executor.restore_checkpoint(checkpoint)" in source


def test_electric_source_files_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/electric_mining_option.py",
        "src/factorio_ai_lab/cortex/electric_mining_structural_execute.py",
        "scripts/run_cortex_f5c_electric_mining.py",
    ):
        ast.parse((ROOT/name).read_text())
