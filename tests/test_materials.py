from __future__ import annotations

from factorio_ai_lab.planning.materials import MaterialLedger


def test_material_ledger_accounts_for_wip_and_reservations() -> None:
    ledger = MaterialLedger.from_inventory(
        {"iron-plate": 40},
        work_in_progress={"iron-plate": 20},
        incoming={"iron-plate": 10},
        reserved={"iron-plate": 15},
        safety_stock={"iron-plate": 5},
    )
    plan = ledger.plan({"iron-plate": 55})
    row = plan.allocations[0]
    assert row.position.gross_available == 70
    assert row.position.net_available == 50
    assert row.allocated == 50
    assert row.shortage == 5
    assert not plan.feasible


def test_material_ledger_prevents_double_counting_reserved_stock() -> None:
    ledger = MaterialLedger.from_inventory(
        {"iron-plate": 100, "copper-plate": 30},
        reserved={"iron-plate": 70},
    )
    plan = ledger.plan(
        {"iron-plate": 40, "copper-plate": 20}
    )
    assert plan.shortage_by_item() == {"iron-plate": 10}
    assert plan.total_shortage == 10


def test_material_plan_reports_surplus() -> None:
    ledger = MaterialLedger.from_inventory({"wood": 50})
    plan = ledger.plan({"wood": 12})
    assert plan.feasible
    assert plan.allocations[0].projected_surplus == 38
