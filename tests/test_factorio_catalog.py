import unittest

from factorio_ai_lab.planning.factorio_catalog import (
    EARLY_GAME_PRODUCTION_PLANNER,
    FACTORIO_DATA_VERSION,
)


class FactorioCatalogTests(unittest.TestCase):
    def test_logistic_science_expands_to_raw_plate_rates(self) -> None:
        dag = EARLY_GAME_PRODUCTION_PLANNER.plan(
            "logistic-science-pack",
            0.1,
        )
        self.assertEqual(FACTORIO_DATA_VERSION, "2.0.73")
        self.assertAlmostEqual(
            dag.raw_requirements_per_s["iron-plate"],
            0.55,
        )
        self.assertAlmostEqual(
            dag.raw_requirements_per_s["copper-plate"],
            0.15,
        )
        self.assertIsNotNone(dag.node("electronic-circuit"))
        self.assertIsNotNone(dag.node("transport-belt"))
        self.assertIsNotNone(dag.node("inserter"))

    def test_small_electric_pole_expands_to_wood_and_copper(self) -> None:
        plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
            "small-electric-pole",
            2.0,
        )

        self.assertAlmostEqual(
            plan.raw_requirements_per_s["wood"],
            1.0,
        )
        self.assertAlmostEqual(
            plan.raw_requirements_per_s["copper-plate"],
            1.0,
        )

    def test_assembling_machine_one_expands_to_plate_requirements(self) -> None:
        plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
            "assembling-machine-1",
            1.0,
        )

        self.assertAlmostEqual(
            plan.raw_requirements_per_s["iron-plate"],
            22.0,
        )
        self.assertAlmostEqual(
            plan.raw_requirements_per_s["copper-plate"],
            4.5,
        )

    def test_electric_mining_drill_expands_to_plate_requirements(self) -> None:
        plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
            "electric-mining-drill",
            1.0,
        )

        self.assertAlmostEqual(
            plan.raw_requirements_per_s["iron-plate"],
            23.0,
        )
        self.assertAlmostEqual(
            plan.raw_requirements_per_s["copper-plate"],
            4.5,
        )

    def test_lab_expands_to_raw_plate_requirements(self) -> None:
        plan = EARLY_GAME_PRODUCTION_PLANNER.plan("lab", 1.0)

        self.assertEqual(plan.target_item, "lab")
        self.assertAlmostEqual(
            plan.raw_requirements_per_s["iron-plate"],
            36.0,
        )
        self.assertAlmostEqual(
            plan.raw_requirements_per_s["copper-plate"],
            15.0,
        )

    def test_logistic_science_machine_chain_has_six_craftables(self) -> None:
        dag = EARLY_GAME_PRODUCTION_PLANNER.plan(
            "logistic-science-pack",
            0.1,
        )
        self.assertEqual(len(dag.nodes), 6)


if __name__ == "__main__":
    unittest.main()
