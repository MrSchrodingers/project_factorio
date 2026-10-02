from __future__ import annotations

import ast

from factorio_ai_lab.cortex.actions import (
    ActionCondition,
    ActionFamily,
    ConditionOperator,
    ConditionState,
)
from factorio_ai_lab.cortex.options import OptionKind
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.cortex.structural_prepare import (
    ROLLBACK_RECOVERY_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)


def reserve_prepared() -> PreparedStructuralAction:
    conditions=tuple(
        ActionCondition(
            name=name,
            operator=ConditionOperator.EQUALS,
            state=ConditionState.UNKNOWN,
            expected=True,
            hard=True,
        )
        for name in (
            "reserve_iron_ready",
            "reserve_coal_ready",
            "reserve_endogenous_only",
            "reserve_capabilities_survive",
        )
    )
    return PreparedStructuralAction(
        action_id="reserve-recovery:action",
        family=ActionFamily.RESUPPLY,
        intent="restore promoted reserves",
        binding="cortex.structural.rollback_recovery",
        purpose="infrastructure",
        contract_version=ROLLBACK_RECOVERY_CONTRACT_VERSION,
        operations=(
            StructuralOperation(
                op="recover_promoted_reserves",
                parameters={
                    "positions":{
                        "iron_extractor":{"x":15.0,"y":70.0},
                        "iron_buffer":{"x":15.5,"y":71.5},
                        "coal_extractor":{"x":15.0,"y":-4.0},
                        "coal_buffer":{"x":15.5,"y":-2.5},
                    },
                    "iron_target":56,
                    "coal_floor":16,
                    "iron_refuel_coal":3,
                    "coal_refuel_coal":1,
                    "recovery_window_seconds":60,
                },
            ),
            StructuralOperation(
                op="verify_postconditions",
                parameters={
                    "conditions":[condition.to_dict() for condition in conditions],
                },
            ),
        ),
        measurement_keys=tuple(condition.name for condition in conditions),
        preflight={
            "promotion_credit":False,
            "external_resource_injection":False,
        },
    )


def test_reserve_recovery_kind_is_typed_separately() -> None:
    assert OptionKind.RESTORE_PROMOTED_RESERVES.value=="restore_promoted_reserves"


def test_reserve_recovery_compiles_endogenous_only_without_entity_creation() -> None:
    prepared=reserve_prepared()
    result=compile_structural_action(prepared,settle_seconds=90)
    assert result.ready
    assert result.compiled is not None
    code=result.compiled.code
    ast.parse(code)
    assert "cortex_reserve_iron_after>=56" in code
    assert "cortex_reserve_coal_after>=16" in code
    assert "quantity=4" in code
    assert "quantity=3" in code
    assert "quantity=1" in code
    assert "sleep(60)" in code
    assert "endogenous coal stock cannot fund reserve recovery" in code
    assert "place_entity(" not in code
    assert "craft_item(" not in code
    assert "cortex_place_exact_entity(" not in code
    assert "cortex_craft_exact_item(" not in code
    assert "cortex_mine_exact_resource(" not in code
    assert "set_inventory" not in code
    assert "cortex_processor_output" not in code


def test_reserve_recovery_refuses_budget_shorter_than_causal_window() -> None:
    result=compile_structural_action(reserve_prepared(),settle_seconds=59)
    assert not result.ready
    assert result.refusal is not None
    assert "causal windows (60s)" in result.refusal.detail


def test_reserve_recovery_v11_does_not_change_legacy_recovery_operation() -> None:
    prepared=reserve_prepared()
    operations=[operation.op for operation in prepared.operations]
    assert operations==["recover_promoted_reserves","verify_postconditions"]
    assert prepared.contract_version=="cortex_structural_ops_v11"
