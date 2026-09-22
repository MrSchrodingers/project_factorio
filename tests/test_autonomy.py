import inspect
import unittest
from pathlib import Path

from factorio_ai_lab.experiments.open_play_runner import (
    OPEN_PLAY_CURRICULUM,
    OPEN_PLAY_STAGE_INDEX,
    STAGE_AUTONOMY_SOAK,
    STAGE_ELECTRIC_MINING_TRANSITION,
    STAGE_GREEN_SCIENCE_INDUSTRY,
    STAGE_LOGISTIC_SCIENCE_UNLOCK,
    STAGE_LOGISTICS_RESEARCH,
    _autonomy_layout_directions,
    _bootstrap_resource_deficits,
    _closed_loop_validation_passed,
    _green_science_industry,
    _opposite_direction_name,
    _research_logistics,
    _unlock_logistic_science,
)
from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy


def entity(name, x, y, status="working"):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "status": status,
    }


class AutonomyEvaluationTests(unittest.TestCase):
    def test_bootstrap_deficits_are_measured_against_requested_targets(self) -> None:
        deficits = _bootstrap_resource_deficits(
            measured={
                "wood": 50,
                "stone": 50,
                "coal": 180,
                "iron": 541,
                "copper": 263,
            },
            requested={
                "wood": 50,
                "stone": 50,
                "coal": 180,
                "iron": 703,
                "copper": 263,
            },
        )

        self.assertEqual(deficits["iron"], 162.0)
        self.assertEqual(deficits["coal"], 0.0)
        self.assertEqual(deficits["copper"], 0.0)

    def test_validated_champion_requires_closed_loop(self) -> None:
        self.assertFalse(
            _closed_loop_validation_passed(True, {})
        )
        self.assertFalse(
            _closed_loop_validation_passed(
                True,
                {"autonomy": {"closed_loop": False}},
            )
        )
        self.assertFalse(
            _closed_loop_validation_passed(
                False,
                {"autonomy": {"closed_loop": True}},
            )
        )
        self.assertTrue(
            _closed_loop_validation_passed(
                True,
                {"autonomy": {"closed_loop": True}},
            )
        )

    def test_electric_transition_precedes_science_scaleup(self) -> None:
        names = [stage["name"] for stage in OPEN_PLAY_CURRICULUM]

        self.assertLess(
            names.index("Electric mining transition"),
            names.index("Logistic-science unlock"),
        )
        self.assertLess(
            names.index("Electric mining transition"),
            names.index("Green-science industry"),
        )
        self.assertEqual(names[-1], "Autonomy soak")

    def test_named_stage_indices_follow_curriculum_order(self) -> None:
        self.assertEqual(
            STAGE_ELECTRIC_MINING_TRANSITION,
            OPEN_PLAY_STAGE_INDEX["Electric mining transition"],
        )
        self.assertEqual(
            STAGE_LOGISTIC_SCIENCE_UNLOCK,
            OPEN_PLAY_STAGE_INDEX["Logistic-science unlock"],
        )
        self.assertEqual(
            STAGE_GREEN_SCIENCE_INDUSTRY,
            OPEN_PLAY_STAGE_INDEX["Green-science industry"],
        )
        self.assertEqual(
            STAGE_LOGISTICS_RESEARCH,
            OPEN_PLAY_STAGE_INDEX["Logistics research"],
        )
        self.assertEqual(
            STAGE_AUTONOMY_SOAK,
            len(OPEN_PLAY_CURRICULUM) - 1,
        )
        self.assertEqual(
            sorted(OPEN_PLAY_STAGE_INDEX.values()),
            list(range(len(OPEN_PLAY_CURRICULUM))),
        )

    def test_input_inserters_use_opposite_api_rotation_for_belt_feed(self) -> None:
        self.assertEqual(_opposite_direction_name("UP"), "DOWN")
        self.assertEqual(_opposite_direction_name("RIGHT"), "LEFT")
        self.assertEqual(_opposite_direction_name("DOWN"), "UP")
        self.assertEqual(_opposite_direction_name("LEFT"), "RIGHT")
        with self.assertRaises(ValueError):
            _opposite_direction_name("DIAGONAL")

        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        for inserter_name, rotate_token in (
            ("boiler_inserter", "__BOILER_ROTATE_DIRECTION__"),
            ("iron_ore_inserter", "__IRON_ORE_ROTATE_DIRECTION__"),
            ("iron_fuel_inserter", "__IRON_FUEL_ROTATE_DIRECTION__"),
            ("copper_ore_inserter", "__COPPER_ORE_ROTATE_DIRECTION__"),
            ("copper_fuel_inserter", "__COPPER_FUEL_ROTATE_DIRECTION__"),
        ):
            self.assertIn(
                (
                    f"{inserter_name}=rotate_entity(\n"
                    f"    {inserter_name},\n"
                    f"    Direction.{rotate_token},\n"
                    ")"
                ),
                source,
            )

    def test_route_deficit_telemetry_captures_all_material_classes(self) -> None:
        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        self.assertIn("route_deficits = []", source)
        self.assertIn(
            'f"iron={route_iron:.0f}/{route_iron_target:.0f}"',
            source,
        )
        self.assertIn(
            'f"copper={route_copper:.0f}/{route_copper_target:.0f}"',
            source,
        )
        self.assertIn(
            'f"wood={route_wood:.0f}/{route_wood_target:.0f}"',
            source,
        )
        self.assertIn('"route_deficits": route_deficits', source)
        self.assertIn("pole_requirement_estimates=[]", source)

    def test_electric_backbone_is_split_into_bounded_phases(self) -> None:
        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        self.assertIn('"electric_mining_layout_plan"', source)
        self.assertIn('"electric_mining_route_batches"', source)
        self.assertIn('"electric_mining_construction_materials"', source)
        self.assertIn('"electric_mining_power_batches"', source)
        self.assertIn('"electric_mining_belt_routes"', source)
        self.assertIn('"electric_mining_backbone_soak"', source)
        self.assertIn("route_batch_size = 4", source)
        self.assertIn("route_buffer_max_rounds", source)

    def test_physical_production_baseline_is_after_manual_wip_cleanup(self) -> None:
        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        cleanup = source.index("# Phase C — remove all manually loaded WIP")
        baseline = source.index(
            'iron_before = _production_counter(namespace, "iron-plate")',
            cleanup,
        )
        soak = source.index('soak_step = executor.execute(', baseline)
        self.assertLess(cleanup, baseline)
        self.assertLess(baseline, soak)
        self.assertIn('"output_distribution": output_distribution', source)

    def test_route_buffer_uses_conservative_horizon_and_plateau_stop(self) -> None:
        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        self.assertIn("iron_deficit/10.0", source)
        self.assertIn("copper_deficit/8.0", source)
        self.assertIn("route_buffer_max_rounds=min(\n    64,", source)
        self.assertIn("route_no_progress_batches = 0", source)
        self.assertIn("route_no_progress_batches >= 2", source)
        self.assertIn('"route_stop_reason"', source)
        self.assertIn('"production_plateau"', source)

    def test_route_buffer_reclaims_coal_from_idle_furnaces(self) -> None:
        source = Path(
            __import__(
                "factorio_ai_lab.experiments.open_play_runner",
                fromlist=["__file__"],
            ).__file__
        ).read_text()

        self.assertIn("route_batch_reclaimed_coal=0", source)
        self.assertIn("parked_iron=parked_inventory[Prototype.IronOre]", source)
        self.assertIn("parked_copper=parked_inventory[Prototype.CopperOre]", source)
        self.assertIn("and parked_iron<=0", source)
        self.assertIn("and parked_copper<=0", source)
        self.assertIn("route_batch_reclaimed_coal+=reclaimed", source)

    def test_post_transition_stages_do_not_use_retired_furnaces(self) -> None:
        for function in (
            _unlock_logistic_science,
            _green_science_industry,
            _research_logistics,
        ):
            source = inspect.getsource(function)
            self.assertNotIn("iron_furnace_2", source)
            self.assertNotIn("iron_furnace_3", source)
            self.assertNotIn("copper_furnace_2", source)

    def test_post_transition_science_uses_autonomous_output_buffers(self) -> None:
        for function in (
            _unlock_logistic_science,
            _green_science_industry,
            _research_logistics,
        ):
            source = inspect.getsource(function)
            self.assertIn("iron_output_chest", source)
            self.assertIn("copper_output_chest", source)

        unlock_source = inspect.getsource(_unlock_logistic_science)
        self.assertIn("scale_material_rounds", unlock_source)
        self.assertIn("for scale_material_round in range(1,7)", unlock_source)

        green_source = inspect.getsource(_green_science_industry)
        self.assertIn("green_science_material_preflight", green_source)
        self.assertIn("EARLY_GAME_PRODUCTION_PLANNER.plan", green_source)
        self.assertIn("for green_material_round in range(1,9)", green_source)

        logistics_source = inspect.getsource(_research_logistics)
        self.assertIn("logistics_plate_rounds", logistics_source)
        self.assertIn("for logistics_plate_round in range(1,6)", logistics_source)

    def test_layout_variants_are_structurally_distinct(self) -> None:
        variants = {
            _autonomy_layout_directions(index)
            for index in range(4)
        }

        self.assertEqual(len(variants), 4)
        self.assertEqual(
            _autonomy_layout_directions(4),
            _autonomy_layout_directions(0),
        )

    def test_empty_world_has_zero_autonomy_score(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[],
            interventions={},
            production_rates_per_s={},
            soak_runtime_s=0,
        )

        self.assertEqual(report.level, "none")
        self.assertEqual(report.score, 0.0)
        self.assertFalse(report.topology["healthy_fuel"])
        self.assertFalse(report.topology["healthy_power"])
        self.assertFalse(report.topology["zero_manual_logistics"])

    def test_not_connected_counts_as_power_failure(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[
                entity("steam-engine", 0, 0, "not_connected"),
                entity("lab", 2, 0, "no_power"),
                entity("offshore-pump", -2, 0),
                entity("boiler", -1, 0),
            ],
            interventions={},
            production_rates_per_s={},
            soak_runtime_s=120,
        )

        self.assertGreaterEqual(report.no_power_entities, 2)
        self.assertFalse(report.topology["healthy_power"])

    def test_manual_factory_is_not_closed_loop(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[
                entity("burner-mining-drill", 0, 0, "no_fuel"),
                entity("stone-furnace", 6, 0, "no_ingredients"),
                entity("offshore-pump", 0, 8),
                entity("boiler", 4, 8, "no_fuel"),
                entity("steam-engine", 8, 8, "not_plugged_in_electric_network"),
            ],
            interventions={"manual_transfer_calls": 4},
            production_rates_per_s={"iron-plate": 0.0},
            soak_runtime_s=120,
        )

        self.assertFalse(report.closed_loop)
        self.assertEqual(report.level, "semi_autonomous")
        self.assertGreater(report.no_fuel_entities, 0)
        self.assertGreater(report.manual_logistics_calls, 0)

    def test_stale_flow_spike_does_not_create_live_chain(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[
                entity("burner-mining-drill", 0, 0, "no_fuel"),
                entity("wooden-chest", 0, 2),
                entity("offshore-pump", 8, 0),
                entity("boiler", 10, 0),
                entity("steam-engine", 13, 0, "not_plugged_in_electric_network"),
            ],
            interventions={"manual_transfer_calls": 10},
            production_rates_per_s={"coal": 0.4},
            soak_runtime_s=0,
        )

        self.assertFalse(report.topology["coal_chain_live"])
        self.assertFalse(report.topology["producing_material"])
        self.assertFalse(report.closed_loop)

    def test_smelting_without_output_buffer_is_not_closed_loop(self) -> None:
        rows = [
            entity("electric-mining-drill", 0, 0),
            entity("transport-belt", 2, 0),
            entity("inserter", 4, 0),
            entity("boiler", 5, 0),
            entity("offshore-pump", 5, -4),
            entity("steam-engine", 8, 0),
            entity("small-electric-pole", 7, 2),
            entity("lab", 9, 2),
            entity("stone-furnace", 3, 6),
            entity("transport-belt", 1, 6),
            entity("inserter", 2, 6),
            entity("inserter", 3, 5),
        ]
        report = evaluate_factory_autonomy(
            entities=rows,
            interventions={},
            production_rates_per_s={
                "iron-plate": 1.0,
                "copper-plate": 1.0,
                "coal": 1.0,
            },
            soak_runtime_s=120,
        )

        self.assertFalse(report.closed_loop)
        self.assertFalse(report.topology["smelting_output_distribution"])

    def test_physical_closed_loop_requires_zero_manual_logistics(self) -> None:
        rows = [
            entity("electric-mining-drill", 0, 0),
            entity("transport-belt", 2, 0),
            entity("transport-belt", 3, 0),
            entity("inserter", 4, 0),
            entity("boiler", 5, 0),
            entity("offshore-pump", 5, -4),
            entity("steam-engine", 8, 0),
            entity("small-electric-pole", 7, 2),
            entity("lab", 9, 2),
            # Iron smelting cell: belt + two input inserters + output inserter/chest.
            entity("stone-furnace", 3, 6),
            entity("transport-belt", 1, 6),
            entity("inserter", 2, 6),
            entity("inserter", 3, 5),
            entity("inserter", 3, 7),
            entity("wooden-chest", 3, 8),
            # Copper smelting cell.
            entity("stone-furnace", 12, 6),
            entity("transport-belt", 10, 6),
            entity("inserter", 11, 6),
            entity("inserter", 12, 5),
            entity("inserter", 12, 7),
            entity("wooden-chest", 12, 8),
        ]
        report = evaluate_factory_autonomy(
            entities=rows,
            interventions={},
            production_rates_per_s={
                "iron-plate": 1.0,
                "copper-plate": 1.0,
                "coal": 1.0,
            },
            soak_runtime_s=120,
        )

        self.assertTrue(report.closed_loop)
        self.assertEqual(report.level, "closed_loop")
        self.assertEqual(report.score, 1.0)


if __name__ == "__main__":
    unittest.main()
