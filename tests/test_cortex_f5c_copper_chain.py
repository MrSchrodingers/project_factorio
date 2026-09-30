from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.copper_chain_option import (
    CopperChainOptionPlan,
    compose_copper_chain_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)

ROOT=Path(__file__).resolve().parents[1]


def _routes() -> dict[str,dict[str,object]]:
    return {
        name:{
            "validator":"observed_weighted_astar_v1",
            "path_waypoints":12,
            "path_length_tiles":11,
            "cost":11.2,
            "expanded_nodes":24,
        }
        for name in ("stone","wood","copper","furnace")
    }


def copper_plan() -> CopperChainOptionPlan:
    option=OptionRequest(
        option_id="f5c-copper-chain",
        kind=OptionKind.ESTABLISH_COPPER_CHAIN,
        goal="establish persistent copper chain",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_copper_chain",
            code_revision="copper-test-sha",
            run_id="copper-test-run",
        ),
        budget=OptionBudget(requested_ticks=220*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-copper-chain-action",
        family=ActionFamily.PLACEMENT,
        intent="establish persistent copper chain",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_copper_chain",
            code_revision="copper-test-sha",
            run_id="copper-test-run",
        ),
        requires=(
            "iron_extraction","coal_self_sufficiency",
            "iron_smelting","steam_power",
        ),
        provides=("copper_chain",),
    )
    result=compose_copper_chain_option(
        option,
        action_request=action,
        positions={
            "iron_extractor":(15.0,70.0),
            "iron_buffer":(15.5,71.5),
            "iron_furnace":(20.0,69.0),
            "coal_extractor":(15.0,-4.0),
            "coal_buffer":(15.5,-2.5),
            "boiler":(-4.0,3.5),
            "steam_engine":(-6.0,13.5),
            "copper_extractor":(-47.0,70.0),
            "copper_furnace":(-38.0,70.0),
            "stone":(-46.5,-0.5),
            "wood":(-5.0,-19.0),
        },
        route_validations=_routes(),
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_copper_chain_option_is_inert_schema_aligned_and_persistent() -> None:
    plan=copper_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_COPPER_CHAIN
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v8"
    assert plan.prepared.binding=="cortex.structural.copper_chain"
    assert plan.prepared.preflight["persistent_copper_chain"] is True
    assert plan.prepared.preflight["external_resource_injection"] is False
    names={row.name for row in plan.termination_conditions}
    assert {
        "copper_extraction_live",
        "copper_smelting_live",
        "copper_plate_output_positive",
        "copper_extractor_exists",
        "copper_furnace_exists",
    }.issubset(names)


def test_compiled_copper_chain_uses_endogenous_resources_and_survival() -> None:
    plan=copper_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=220)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "set_research(" not in code
    assert "reset(" not in code
    assert "harvest_resource(" in code
    assert "Prototype.CopperOre" in code
    assert "Prototype.CopperPlate" in code
    assert "Prototype.BurnerMiningDrill" in code
    assert "cortex_copper_extraction_live" in code
    assert "cortex_copper_smelting_live" in code
    assert "cortex_copper_plate_output_positive" in code
    assert "cortex_steam_power_survives" in code
    assert "cortex_iron_survival_growth" in code
    assert "cortex_coal_survival_growth" in code
    assert "cortex_smelting_survival_growth" in code


def test_copper_chain_guards_are_specific_not_generic_processor_guards() -> None:
    plan=copper_plan()
    names={row.name for row in execution_guard_conditions(plan.prepared)}

    assert names=={"copper_extractor_exists","copper_furnace_exists"}
    assert "processor_exists" not in names
    assert "processor_output" not in names


def test_copper_chain_runner_is_bounded_no_reset_and_supervisor_wired() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_copper_chain.py").read_text()
    supervisor=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "FactorioWorldLease" in source
    assert "bind_fast_reposition_tool" in source
    assert "use_checkpoint_for_action=False" in source
    assert '"world_reset":False' in source
    assert '"external_resource_injection":False' in source
    assert "executor.reset(" not in source
    assert 'OptionKind.ESTABLISH_COPPER_CHAIN' in source
    assert '"copper_chain":"run_cortex_f5c_copper_chain.py"' in supervisor


def test_copper_chain_source_files_parse() -> None:
    for name in (
        "src/factorio_ai_lab/cortex/copper_chain_option.py",
        "src/factorio_ai_lab/cortex/copper_chain_structural_execute.py",
        "scripts/run_cortex_f5c_copper_chain.py",
    ):
        ast.parse((ROOT/name).read_text())
