from __future__ import annotations

import pytest

from factorio_ai_lab.domain.state import GridPoint
from scripts.run_cortex_structural_canary import (
    CONFIRMATORY_SEEDS,
    DEFAULT_SEED,
    available_inventory,
    craft_output_count,
    resource_survey_from_overview,
    validate_canary_seed,
)


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
