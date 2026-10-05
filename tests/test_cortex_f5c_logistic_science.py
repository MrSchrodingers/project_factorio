from __future__ import annotations

import ast

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.logistic_science_option import (
    DEFAULT_OPTION_SECONDS,
    compose_logistic_science_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
    execution_guard_conditions,
)


def logistic_plan():
    provenance=ActionProvenance(
        requested_by="f5-c-deterministic-baseline",
        source_component="tests.test_cortex_f5c_logistic_science",
        code_revision="logistic-test-sha",
        run_id="logistic-test-run",
    )
    option=OptionRequest(
        option_id="f5c-logistic-science",
        kind=OptionKind.ESTABLISH_LOGISTIC_SCIENCE,
        goal="prove endogenous sustainable logistic science",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=DEFAULT_OPTION_SECONDS*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-logistic-science-action",
        family=ActionFamily.CRAFT,
        intent="research and produce sustainable logistic science",
        provenance=provenance,
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting",
            "steam_power","copper_chain","automation_science",
            "powered_manufacturing","electric_mining",
        ),
        provides=("logistic_science",),
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
    result=compose_logistic_science_option(
        option,
        action_request=action,
        positions=positions,
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_logistic_science_option_is_inert_and_schema_aligned() -> None:
    plan=logistic_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_LOGISTIC_SCIENCE
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v13"
    assert plan.prepared.binding=="cortex.structural.logistic_science"
    assert plan.prepared.preflight["native_research_queue"] is True
    assert plan.prepared.preflight["external_resource_injection"] is False
    assert {row.name for row in plan.termination_conditions}=={
        "logistic_science_output_positive",
        "inputs_endogenous",
        "all_promoted_capabilities_alive",
        "sustainability_soak_passed",
    }
    assert execution_guard_conditions(plan.prepared)==()


def test_compiled_logistic_science_is_causal_and_self_validating() -> None:
    plan=logistic_plan()
    compiled=compile_structural_action(
        plan.prepared,
        settle_seconds=DEFAULT_OPTION_SECONDS,
    )

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)

    assert "set_research('logistic-science-pack')" in code
    assert "get_research_progress(" in code
    assert ".researched=" not in code
    assert "force.technologies" not in code
    assert "cortex_logistic_iron_plate_ready" in code
    assert "cortex_logistic_copper_plate_ready" in code
    assert "cortex_electric_coal_after_accumulation" in code
    assert "cortex_all_promoted_capabilities_alive" in code
    assert "cortex_logistic_science_output_positive" in code
    assert "cortex_inputs_endogenous=True" in code
    assert "cortex_sustainability_soak_passed" in code
    assert "cortex_logistic_first_growth" in code
    assert "cortex_logistic_output_second" in code
    assert code.count(
        "Prototype.TransportBelt,cortex_assembler,quantity=1"
    )==2
    assert code.count(
        "Prototype.Inserter,cortex_assembler,quantity=1"
    )==2
    first_extract=code.index(
        "quantity=cortex_logistic_output_first"
    )
    second_belt=code.rindex(
        "Prototype.TransportBelt,cortex_assembler,quantity=1"
    )
    assert first_extract < second_belt
    assert "cortex_existing_circuit_output" in code
    assert "cortex_existing_gear_output" in code
    assert code.count("sleep(14)")>=2
    assert "cortex_processor_output" not in code
    assert "cortex_expected_product" not in code


def test_logistic_science_budget_covers_internal_causal_windows() -> None:
    plan=logistic_plan()

    refused=compile_structural_action(plan.prepared,settle_seconds=2279)
    assert refused.ready is False
    assert refused.refusal is not None
    assert "2280" in refused.refusal.detail

    accepted=compile_structural_action(plan.prepared,settle_seconds=2280)
    assert accepted.ready is True


def test_logistic_science_requires_eight_promoted_capabilities() -> None:
    plan=logistic_plan()
    assert plan.action_request.requires==(
        "iron_extraction","coal_self_sufficiency","iron_smelting",
        "steam_power","copper_chain","automation_science",
        "powered_manufacturing","electric_mining",
    )


def test_logistic_runner_eval_timeout_covers_option_budget() -> None:
    source=(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5c_logistic_science.py"
    ).read_text()
    assert "minimum_seconds=max(2700,option_seconds+300)" in source


def test_logistic_preflight_uses_electrical_topology_not_instant_energy() -> None:
    source=(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5c_logistic_science.py"
    ).read_text()
    assert 'for key in ("lab","assembler","electric_pole","electric_drill")' in source
    assert "promoted electric drill is not electrically live" not in source
    assert 'observed["electric_drill"].get("energy")' not in source

def test_logistic_runner_measure_reads_v13_namespace_aliases() -> None:
    import importlib.util
    from types import SimpleNamespace

    path=(
        __import__("pathlib").Path(__file__).resolve().parents[1]
        /"scripts"/"run_cortex_f5c_logistic_science.py"
    )
    spec=importlib.util.spec_from_file_location("f5c_logistic_runner_test",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    namespace=SimpleNamespace(
        cortex_logistic_research_completed=True,
        cortex_logistic_research_remaining_count=0,
        cortex_logistic_science_batch_ready=77,
        cortex_logistic_iron_plate_ready=190,
        cortex_logistic_copper_plate_ready=95,
        cortex_logistic_output_second=1,
    )
    measured=module._measure(namespace,None)

    assert measured["research_completed"] is True
    assert measured["research_remaining_count"]==0
    assert measured["science_batch_ready"]==77
    assert measured["iron_plate_ready"]==190
    assert measured["copper_plate_ready"]==95
    assert measured["logistic_second_output"]==1

