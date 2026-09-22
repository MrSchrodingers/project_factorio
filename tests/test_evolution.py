import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factorio_ai_lab.experiments import evolution_loop as evolution_loop_module
from factorio_ai_lab.experiments.evolution_loop import (
    _apply_deterministic_open_play_repairs,
    _normalized_open_play_configuration,
    _repair_open_play_strategy,
)
from factorio_ai_lab.learning.evolution import apply_advice, challenger_genome


class EvolutionGenomeTests(unittest.TestCase):
    def test_first_attempt_is_control(self) -> None:
        genome = challenger_genome(attempt=1)
        self.assertEqual(genome.routing_turn_penalty, 0.25)
        self.assertEqual(genome.placement_exploration, 2.0)
        self.assertEqual(genome.coal_safety_stock, 4)
        self.assertEqual(genome.buffer_target, 12)

    def test_mutation_is_deterministic_and_structural(self) -> None:
        first = challenger_genome(attempt=2, seed=123)
        second = challenger_genome(attempt=2, seed=123)
        control = challenger_genome(attempt=1, seed=123)
        self.assertEqual(first, second)
        self.assertNotEqual(first, control)
        changed = sum(
            first.to_dict()[key] != control.to_dict()[key]
            for key in first.to_dict()
        )
        self.assertGreaterEqual(changed, 3)

    def test_mutates_around_champion_and_stays_in_bounds(self) -> None:
        genome = challenger_genome(
            attempt=7,
            seed=321,
            champion_configuration={
                "routing_turn_penalty": 0.1,
                "placement_exploration": 1.0,
                "placement_radius_scale": 1.2,
                "coal_safety_stock": 8,
                "buffer_target": 24,
            },
        )
        self.assertGreaterEqual(genome.routing_turn_penalty, 0.02)
        self.assertLessEqual(genome.routing_turn_penalty, 2.0)
        self.assertGreaterEqual(genome.coal_safety_stock, 2)
        self.assertLessEqual(genome.coal_safety_stock, 12)
        self.assertGreaterEqual(genome.buffer_target, 6)
        self.assertLessEqual(genome.buffer_target, 40)

    def test_advice_is_bounded_and_changes_whitelisted_parameters(self) -> None:
        base = challenger_genome(attempt=1)
        adjusted = apply_advice(
            base,
            (
                ("coal_safety_stock", "increase"),
                ("buffer_target", "increase"),
                ("routing_turn_penalty", "decrease"),
            ),
        )
        self.assertEqual(adjusted.coal_safety_stock, 5)
        self.assertEqual(adjusted.buffer_target, 14)
        self.assertLess(adjusted.routing_turn_penalty, base.routing_turn_penalty)

    def test_to_dict_contains_engineering_policy(self) -> None:
        payload = challenger_genome(attempt=1).to_dict()
        for key in (
            "placement_radius_scale",
            "coal_safety_stock",
            "coal_producer_refuel",
            "coal_copper_mining_budget",
            "coal_copper_smelting_budget",
            "coal_survival_budget",
            "buffer_target",
            "rebuild_gain_threshold",
        ):
            self.assertIn(key, payload)


class EvolutionLoopRepairTests(unittest.TestCase):
    def test_open_play_configuration_fills_resource_defaults(self) -> None:
        normalized = _normalized_open_play_configuration(
            {
                "routing_turn_penalty": 0.4,
                "coal_safety_stock": 3,
            }
        )

        self.assertEqual(normalized["open_play_iron_target"], 300)
        self.assertEqual(normalized["open_play_copper_target"], 160)
        self.assertEqual(normalized["open_play_coal_target"], 100)

    def test_same_counterexample_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            strategy_path = Path(temporary) / "open_play_strategy.json"
            strategy_path.write_text(
                json.dumps(
                    {
                        "experimental_champion": "champion-1",
                        "counterexample_run": "open-play-1",
                        "configuration": {
                            "open_play_iron_target": 345,
                            "open_play_copper_target": 179,
                        },
                    }
                )
            )
            with patch.object(
                evolution_loop_module,
                "OPEN_PLAY_STRATEGY",
                strategy_path,
            ):
                repaired = _repair_open_play_strategy(
                    champion={"run_id": "champion-1"},
                    previous={
                        "open_play_iron_target": 345,
                        "open_play_copper_target": 179,
                    },
                    current={
                        "run_id": "open-play-1",
                        "stage": "Lab bootstrap",
                        "metrics": {},
                    },
                )

        self.assertEqual(repaired["open_play_iron_target"], 345)
        self.assertEqual(repaired["open_play_copper_target"], 179)

    def test_logistic_science_power_starvation_increases_coal_budget(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Logistic-science unlock",
                "metrics": {
                    "open_play_scaled_red_science": 74,
                    "logistic_science_unlock_diagnostics": {
                        "required_red": 75,
                        "scaled_red": 74,
                    },
                    "logistic_science_production_rounds": [
                        {
                            "round": 5,
                            "output": 70,
                            "boiler_status": "EntityStatus.NO_FUEL",
                            "engine_status": "EntityStatus.WORKING",
                            "assembler_status": "EntityStatus.WORKING",
                        },
                        {
                            "round": 6,
                            "output": 74,
                            "boiler_status": "EntityStatus.NO_FUEL",
                            "engine_status": "EntityStatus.NOT_CONNECTED",
                            "assembler_status": "EntityStatus.NO_POWER",
                        },
                    ],
                },
            },
            configuration={
                "open_play_coal_target": 100,
                "open_play_iron_target": 345,
                "open_play_copper_target": 179,
            },
        )

        self.assertEqual(repaired["open_play_coal_target"], 160)
        self.assertTrue(
            any(
                row.get("reason") == "logistic_science_power_budget"
                and row.get("increment") == 60
                for row in repairs
            )
        )

    def test_bootstrap_retry_preserves_requested_resource_target(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Raw bootstrap",
                "metrics": {
                    "open_play_bootstrap": {
                        "requested": {
                            "wood": 50,
                            "stone": 50,
                            "coal": 180,
                            "iron": 703,
                            "copper": 263,
                        },
                        "deficits": {
                            "wood": 0,
                            "stone": 0,
                            "coal": 0,
                            "iron": 162,
                            "copper": 0,
                        },
                    }
                },
            },
            configuration={
                "open_play_wood_target": 50,
                "open_play_stone_target": 50,
                "open_play_coal_target": 180,
                "open_play_iron_target": 703,
                "open_play_copper_target": 263,
                "open_play_wood_radius": 24,
            },
        )

        self.assertEqual(repaired["open_play_iron_target"], 703)
        self.assertTrue(
            any(
                row.get("reason") == "bootstrap_collection_retry"
                and row.get("resource") == "iron"
                and row.get("increment") == 0
                for row in repairs
            )
        )

    def test_electric_backbone_routing_failure_cycles_layout_variant(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_transition": {
                        "phase": "physical_backbone",
                        "researched": True,
                        "accepted": False,
                        "error_occurred": True,
                        "result": (
                            "TypeError: 'Inserter' object is not subscriptable"
                        ),
                    }
                },
            },
            configuration={
                "autonomy_layout_variant": 0,
                "open_play_coal_target": 140,
                "open_play_iron_target": 524,
                "open_play_copper_target": 221,
            },
        )

        self.assertEqual(repaired["autonomy_layout_variant"], 1)
        self.assertTrue(
            any(
                row.get("reason")
                == "electric_backbone_layout_counterexample"
                and row.get("previous_variant") == 0
                and row.get("next_variant") == 1
                for row in repairs
            )
        )

    def test_electric_transition_power_starvation_increases_coal_budget(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_research_preparation": {
                        "coal_loaded": 0,
                        "reclaimed_coal": 0,
                        "boiler_status": "EntityStatus.NO_FUEL",
                        "engine_status": "EntityStatus.WORKING",
                    },
                    "electric_mining_transition": {
                        "phase": "research",
                        "required_red": 25,
                        "available_red": 28,
                        "researched": False,
                    },
                    "electric_mining_research_rounds": [
                        {
                            "round": 1,
                            "accepted": False,
                            "lab_status": "EntityStatus.NO_POWER",
                            "boiler_status": "EntityStatus.NO_FUEL",
                            "engine_status": "EntityStatus.WORKING",
                        }
                    ],
                },
            },
            configuration={
                "open_play_coal_target": 100,
                "open_play_iron_target": 345,
                "open_play_copper_target": 179,
                "open_play_wood_target": 50,
            },
        )

        self.assertEqual(repaired["open_play_coal_target"], 140)
        self.assertTrue(
            any(
                row.get("reason") == "electric_research_power_budget"
                and row.get("increment") == 40
                for row in repairs
            )
        )

    def test_electric_transition_power_starvation_survives_later_phase_failure(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_research_preparation": {
                        "coal_loaded": 5,
                        "lab_energy": 1066.0,
                        "boiler_status": "EntityStatus.WORKING",
                    },
                    "electric_mining_research_rounds": [
                        {
                            "round": 1,
                            "boiler_status": "EntityStatus.NO_FUEL",
                            "engine_status": "EntityStatus.WORKING",
                        }
                    ],
                    "electric_mining_transition": {
                        "phase": "physical_backbone",
                        "researched": True,
                    },
                },
            },
            configuration={
                "open_play_coal_target": 140,
                "open_play_iron_target": 524,
                "open_play_copper_target": 221,
                "open_play_wood_target": 50,
            },
        )

        self.assertEqual(repaired["open_play_coal_target"], 180)
        self.assertTrue(
            any(
                row.get("reason") == "electric_research_power_budget"
                and row.get("increment") == 40
                for row in repairs
            )
        )

    def test_electric_transition_repair_parses_route_deficit_result(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_transition": {
                        "phase": "physical_backbone",
                        "result": (
                            "Exception: route buffer deficits "
                            "iron=303.0/551;copper=40.0/55;wood=20.0/48"
                        ),
                    }
                },
            },
            configuration={
                "open_play_coal_target": 180,
                "open_play_iron_target": 703,
                "open_play_copper_target": 263,
                "open_play_wood_target": 50,
            },
        )

        self.assertEqual(repaired["open_play_iron_target"], 1000)
        self.assertGreaterEqual(repaired["open_play_copper_target"], 282)
        self.assertGreaterEqual(repaired["open_play_wood_target"], 85)
        self.assertTrue(
            any(
                row.get("reason") == "electric_transition_material_budget"
                and row.get("raw_item") == "iron-plate"
                for row in repairs
            )
        )

    def test_electric_transition_repair_accounts_for_structural_budget(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_transition": {
                        "belt_required": 40,
                        "pole_required": 20,
                        "route_iron": 10,
                        "route_iron_target": 90,
                        "route_copper": 5,
                        "route_copper_target": 30,
                        "route_wood": 4,
                        "route_wood_target": 20,
                    }
                },
            },
            configuration={
                "open_play_iron_target": 345,
                "open_play_copper_target": 179,
                "open_play_wood_target": 50,
            },
        )

        self.assertGreater(repaired["open_play_iron_target"], 345)
        self.assertGreater(repaired["open_play_copper_target"], 179)
        self.assertGreater(repaired["open_play_wood_target"], 50)
        self.assertTrue(
            any(
                row.get("reason") == "electric_transition_material_budget"
                for row in repairs
            )
        )

    def test_route_buffer_shortfall_reduces_capex_before_more_iron(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Electric mining transition",
                "metrics": {
                    "electric_mining_transition": {
                        "phase": "route_buffer",
                        "route_stop_reason": "production_plateau",
                        "route_iron": 546,
                        "route_iron_target": 602,
                        "route_copper": 293,
                        "route_copper_target": 74,
                        "route_wood": 117,
                        "route_wood_target": 74,
                        "belt_required": 387,
                        "pole_required": 121,
                    }
                },
            },
            configuration={
                "open_play_iron_target": 1000,
                "open_play_copper_target": 389,
                "open_play_wood_target": 128,
                "autonomy_belt_margin": 6,
                "autonomy_route_detour_margin": 8,
            },
        )

        self.assertEqual(repaired["open_play_iron_target"], 1000)
        self.assertEqual(repaired["open_play_copper_target"], 389)
        self.assertEqual(repaired["open_play_wood_target"], 128)
        self.assertEqual(repaired["autonomy_belt_margin"], 4)
        self.assertEqual(repaired["autonomy_route_detour_margin"], 4)
        self.assertTrue(
            any(
                row.get("reason") == "route_capex_counterexample"
                for row in repairs
            )
        )
        self.assertFalse(
            any(
                row.get("reason") == "electric_transition_material_budget"
                and row.get("raw_item") == "iron-plate"
                for row in repairs
            )
        )

    def test_autonomy_counterexample_mutates_structural_strategy(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Autonomy soak",
                "events": [
                    {
                        "failed_gates": [
                            "fuel_distribution",
                            "smelting_distribution",
                        ]
                    }
                ],
                "metrics": {},
            },
            configuration={
                "autonomy_layout_variant": 0,
                "autonomy_commissioning_coal": 14,
                "autonomy_belt_margin": 6,
                "autonomy_pole_margin": 10,
            },
        )

        self.assertEqual(repaired["autonomy_layout_variant"], 1)
        self.assertEqual(repaired["autonomy_commissioning_coal"], 18)
        self.assertEqual(repaired["autonomy_belt_margin"], 8)
        self.assertEqual(repaired["autonomy_pole_margin"], 14)
        self.assertTrue(
            any(
                row.get("reason") == "structural_autonomy_counterexample"
                for row in repairs
            )
        )

    def test_lab_failure_increases_raw_material_targets(self) -> None:
        repaired, repairs = _apply_deterministic_open_play_repairs(
            current={
                "stage": "Lab bootstrap",
                "metrics": {
                    "lab_automation_diagnostics": {
                        "result": "missing ingredients - iron-gear-wheel x2",
                    }
                },
            },
            configuration={
                "open_play_iron_target": 300,
                "open_play_copper_target": 160,
                "open_play_wood_target": 50,
                "open_play_stone_target": 50,
                "open_play_coal_target": 100,
                "open_play_wood_radius": 24,
            },
        )

        self.assertEqual(repaired["open_play_iron_target"], 345)
        self.assertEqual(repaired["open_play_copper_target"], 179)
        self.assertTrue(
            any(
                row.get("reason") == "stage_material_budget"
                and row.get("target_item") == "lab"
                for row in repairs
            )
        )


if __name__ == "__main__":
    unittest.main()
