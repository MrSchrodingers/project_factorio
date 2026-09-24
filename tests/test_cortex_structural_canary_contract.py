from __future__ import annotations

from pathlib import Path

import pytest

from factorio_ai_lab.domain.state import GridPoint
from scripts.run_cortex_structural_canary import (
    CONFIRMATORY_SEEDS,
    DEFAULT_BOOTSTRAP_SETTLE_SECONDS,
    DEFAULT_SEED,
    available_inventory,
    complete_canary_delivery_dependency,
    craft_output_count,
    delivery_power_capability,
    resource_survey_from_overview,
    validate_canary_seed,
)

ROOT = Path(__file__).resolve().parents[1]

def test_default_canary_seed_is_outside_confirmatory_holdout() -> None:
    assert DEFAULT_SEED not in CONFIRMATORY_SEEDS
    validate_canary_seed(DEFAULT_SEED)


@pytest.mark.parametrize("seed", sorted(CONFIRMATORY_SEEDS))
def test_confirmatory_seeds_are_hard_refused_by_canary(seed: int) -> None:
    with pytest.raises(ValueError, match="reserved for confirmatory evaluation"):
        validate_canary_seed(seed)

def test_available_inventory_reads_only_character_on_hand_items() -> None:
    rows = [
        {
            "name": "character",
            "inventory": {
                "stone-furnace": 10,
                "inserter": 50,
                "coal": 480,
            },
        },
        {
            "name": "wooden-chest",
            "inventories": {"chest": {"iron-ore": 32}},
        },
        {
            "name": "burner-mining-drill",
            "inventories": {"fuel": {"coal": 15}},
        },
    ]

    available = available_inventory(rows)

    assert available["character"] == 1.0
    assert available["stone-furnace"] == 10.0
    assert available["inserter"] == 50.0
    assert available["coal"] == 480.0
    assert "wooden-chest" not in available
    assert "iron-ore" not in available


def test_resource_survey_uses_canonical_overview_points_and_bounds() -> None:
    survey = resource_survey_from_overview({
        "connected": True,
        "center": {"x": 10.0, "y": 20.0},
        "radius": 30.0,
        "points": [
            {"name": "iron-ore", "x": 1.5, "y": 2.5},
            {"name": "coal", "x": 5.5, "y": 6.5},
        ],
    })

    assert survey.tiles[GridPoint(1, 2)] == "iron-ore"
    assert survey.tiles[GridPoint(5, 6)] == "coal"
    assert survey.surveyed == (-20.0, -10.0, 40.0, 50.0)


def test_craft_output_count_reads_observed_processor_output() -> None:
    row = {
        "craft_output": [
            {"name": "iron-plate", "count": 3},
            {"name": "stone", "count": 2},
            {"name": "iron-plate", "count": 4},
        ]
    }

    assert craft_output_count(row, "iron-plate") == 7.0
    assert craft_output_count(row, "copper-plate") == 0.0
    assert craft_output_count(None, "iron-plate") == 0.0

def test_bootstrap_deadline_has_margin_beyond_previous_boundary() -> None:
    # F2-E2 produced exactly one ore at the old fixed 5 s boundary.
    # F2-F3 uses bounded polling with explicit headroom instead.
    assert DEFAULT_BOOTSTRAP_SETTLE_SECONDS == 12

def test_delivery_power_zero_edges_plus_fixture_contract_derives_unavailable() -> None:
    capability = delivery_power_capability(
        {"power_edge_count": 0},
        fixture_power_operation=False,
    )

    assert capability["available"] is False
    assert capability["status"] == "derived_unavailable"
    assert capability["evidence"]["value"] == 0
    assert capability["evidence"]["fixture_power_operation"] is False


def test_delivery_power_existing_network_does_not_prove_actuator_power() -> None:
    capability = delivery_power_capability(
        {"power_edge_count": 3},
        fixture_power_operation=False,
    )

    assert capability["available"] is None
    assert capability["status"] == "network_exists_actuator_position_unmeasured"
    assert capability["evidence"]["value"] == 3


def test_delivery_power_missing_metric_stays_unmeasured() -> None:
    capability = delivery_power_capability(
        {},
        fixture_power_operation=False,
    )

    assert capability["available"] is None
    assert capability["status"] == "missing"
    assert capability["evidence"]["value"] is None

def test_zero_edges_without_fixture_contract_remains_unmeasured() -> None:
    capability = delivery_power_capability(
        {"power_edge_count": 0},
        fixture_power_operation=None,
    )

    assert capability["available"] is None
    assert capability["status"] == "power_capability_unmeasured"
    assert capability["evidence"]["fixture_power_operation"] is None

def test_runner_delivery_glue_passes_fail_closed_power_to_planner(monkeypatch) -> None:
    calls = {}
    sentinel = object()

    def fake_complete(prepared, **kwargs):
        calls["prepared"] = prepared
        calls.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "scripts.run_cortex_structural_canary.complete_delivery_actuator_dependency",
        fake_complete,
    )

    prepared = object()
    catalog = object()
    power, result = complete_canary_delivery_dependency(
        prepared,
        graph_metrics={"power_edge_count": 0},
        catalog=catalog,
        inventory={"burner-inserter": 50.0, "coal": 480.0},
        horizon_s=10.0,
    )

    assert power["available"] is False
    assert power["status"] == "derived_unavailable"
    assert calls["prepared"] is prepared
    assert calls["catalog"] is catalog
    assert calls["electric_power_available"] is False
    assert calls["horizon_s"] == 10.0
    assert result is sentinel

def test_cortex_canary_does_not_import_curriculum_runner() -> None:
    source = (ROOT / "scripts" / "run_cortex_structural_canary.py").read_text(
        encoding="utf-8"
    )
    assert "factorio_ai_lab.experiments.curriculum_runner" not in source
    assert (
        "factorio_ai_lab.instrumentation.runtime import "
        "runtime_entity_footprints"
    ) in source
