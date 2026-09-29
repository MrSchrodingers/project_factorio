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
from factorio_ai_lab.cortex.steam_power_option import (
    SteamPowerOptionPlan,
    compose_steam_power_option,
)
from factorio_ai_lab.cortex.structural_execute import compile_structural_action

ROOT=Path(__file__).resolve().parents[1]


def _routes() -> dict[str,dict[str,object]]:
    return {
        name:{
            "validator":"observed_weighted_astar_v1",
            "path_waypoints":10,
            "path_length_tiles":9,
            "cost":9.1,
            "expanded_nodes":20,
        }
        for name in ("stone","copper","wood","water")
    }


def steam_power_plan() -> SteamPowerOptionPlan:
    option=OptionRequest(
        option_id="f5c-steam-power",
        kind=OptionKind.ESTABLISH_STEAM_POWER,
        goal="establish endogenous steam power",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_steam_power",
            code_revision="steam-power-test-sha",
            run_id="steam-power-test-run",
        ),
        budget=OptionBudget(requested_ticks=300*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-steam-power-action",
        family=ActionFamily.PLACEMENT,
        intent="establish endogenous steam power",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="tests.test_cortex_f5c_steam_power",
            code_revision="steam-power-test-sha",
            run_id="steam-power-test-run",
        ),
        requires=(
            "iron_extraction",
            "coal_self_sufficiency",
            "iron_smelting",
        ),
        provides=("steam_power",),
    )
    result=compose_steam_power_option(
        option,
        action_request=action,
        incumbent_iron_extractor_position=(15.0,70.0),
        incumbent_iron_buffer_position=(15.5,71.5),
        incumbent_iron_furnace_position=(20.0,69.0),
        incumbent_coal_extractor_position=(15.0,-4.0),
        incumbent_coal_buffer_position=(15.5,-2.5),
        stone_position=(-46.5,-0.5),
        copper_position=(-46.5,70.5),
        wood_position=(-1.68,-18.68),
        water_position=(-11.0,0.0),
        route_validations=_routes(),
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_steam_power_option_is_inert_schema_aligned_and_no_copper_promotion() -> None:
    plan=steam_power_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_STEAM_POWER
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v7"
    assert plan.prepared.binding=="cortex.structural.steam_power"
    assert plan.prepared.preflight["external_resource_injection"] is False
    assert plan.prepared.preflight["persistent_copper_chain"] is False
    assert plan.prepared.preflight["native_research_triggers"]=={
        "steam-power":{"type":"craft-item","item":"iron-plate","count":50},
        "electronics":{"type":"craft-item","item":"copper-plate","count":10},
    }
    names={row.name for row in plan.termination_conditions}
    assert {
        "water_source_valid",
        "endogenous_fuel_reachable",
        "steam_generated",
        "electrical_production_positive",
        "electric_consumer_supplied",
        "iron_extraction_survives",
        "coal_self_sufficiency_survives",
        "iron_smelting_survives",
        "steam_engine_exists",
        "electric_consumer_energy",
    }.issubset(names)
    assert "processor_exists" not in names
    assert "processor_output" not in names


def test_compiled_steam_power_uses_native_triggers_real_load_and_survival() -> None:
    plan=steam_power_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=300)

    assert compiled.ready
    assert compiled.compiled is not None
    code=compiled.compiled.code
    ast.parse(code)
    assert "set_research(" not in code
    assert ".researched" not in code
    assert "quantity=56" in code
    assert "quantity=16" in code
    assert "quantity=5" in code
    assert "dry_run=True" in code
    assert "cortex_water_pipe_required=int(" in code
    assert "cortex_steam_pipe_required=int(" in code
    assert "cortex_pipe_required_total=" in code
    assert "cortex_pipe_iron_shortfall=max(" in code
    assert "cortex_pipe_topup_ore=extract_item(" in code
    assert "cortex_pipe_topup_coal_available=inspect_inventory()" in code
    assert "quantity=cortex_pipe_to_craft" in code
    assert "crafted pipe inventory below dry-run requirement" in code
    assert "cortex_power_tap_count=0" in code
    assert "cortex_consumer_pole=place_entity_next_to(" in code
    assert "if cortex_consumer.electrical_id is None:" in code
    assert "cortex_engine_pole=place_entity_next_to(" in code
    assert "cortex_power_network=connect_entities(" not in code
    assert "cortex_iron_reserve_recovery_refuel=0" in code
    assert "cortex_iron_reserve_after_recovery" in code
    assert "endogenous iron reserve recovery failed" in code
    assert "cortex_recovery_coal_available" not in code
    assert "endogenous coal reserve below iron reserve recovery" not in code
    recovery=code[code.index("cortex_iron_reserve_recovery_refuel=0"):]
    recovery=recovery.split("cortex_survival_iron_draw=extract_item(",1)[0]
    assert "insert_item(\n        Prototype.Coal" not in recovery
    assert "cortex_coal_reserve_target=13" in code
    assert "cortex_coal_reserve_recovery_refuel=0" in code
    assert "endogenous coal operating reserve not met" in code
    assert "endogenous coal reserve below survival draw" in code
    assert "cortex_survival_coal_draw=3" in code
    assert "cortex_trigger_iron_plates < 50" in code
    assert "cortex_trigger_copper_plates < 10" in code
    assert "pickup_entity(cortex_copper_furnace)" in code
    assert "craft_item(Prototype.OffshorePump,quantity=1)" in code
    assert "craft_item(Prototype.Boiler,quantity=1)" in code
    assert "craft_item(Prototype.SteamEngine,quantity=1)" in code
    assert "connect_entities(" in code
    assert "Prototype.SmallElectricPole" in code
    assert "cortex_consumer_energy" in code
    assert "cortex_steam_amount" in code
    assert "cortex_water_amount" in code
    assert "cortex_iron_survival_growth" in code
    assert "cortex_coal_survival_growth" in code
    assert "cortex_smelting_survival_growth" in code
    assert "move_to(" not in code
    assert "cortex_fast_reposition(" in code


def test_steam_power_budget_must_cover_internal_causal_windows() -> None:
    plan=steam_power_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=264)

    assert compiled.ready is False
    assert compiled.refusal is not None
    assert "265" in compiled.refusal.detail


def test_steam_power_runner_is_bounded_and_uses_canonical_character() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_steam_power.py").read_text()

    assert "F5BoundedAuthorityBridge" in source
    assert "executor.reset(" not in source
    assert "use_checkpoint_for_action=False" in source
    assert '"automatic_retry":False' in source
    assert '"persistent_copper_chain":False' in source
    assert "bind_fast_reposition_tool" in source
    assert "_canonical_character(snapshot)" in source
    assert "namespace.player_location" not in source
    assert "DASHBOARD_BUILD_INFO" in source
    assert "RUNTIME_BUILD_INFO" in source


def test_supervisor_has_bounded_steam_power_runner() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()
    assert '"steam_power":"run_cortex_f5c_steam_power.py"' in source


def test_steam_power_runner_budget_covers_infrastructure_topup() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_steam_power.py").read_text()

    assert "DEFAULT_OPTION_SECONDS=360" in source
    for name in (
        "infrastructure_iron_after_trigger",
        "water_pipe_required",
        "steam_pipe_required",
        "pipe_required_total",
        "pipe_available_before",
        "pipe_to_craft",
        "pipe_iron_shortfall",
        "pipe_topup_ore",
        "pipe_topup_coal",
        "pipe_topup_plates",
        "pipe_topup_window",
        "pipe_inventory_ready",
        "power_tap_count",
        "consumer_electrical_id",
        "iron_reserve_recovery_refuel",
        "iron_reserve_after_recovery",
        "coal_reserve_before",
        "coal_reserve_target",
        "coal_reserve_shortfall",
        "coal_reserve_draw",
        "coal_reserve_recovery_refuel",
        "coal_reserve_recovery_window",
        "coal_reserve_ready",
    ):
        assert f'"{name}"' in source
