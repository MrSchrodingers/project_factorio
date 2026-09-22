import unittest

from factorio_ai_lab.planning.production_dag import (
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeSpec,
    mapping_recipe_provider,
)


class ProductionDagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recipes = {
            "copper-cable": RecipeSpec(
                item="copper-cable",
                output_count=2,
                crafting_time_s=0.5,
                ingredients=(RecipeIngredient("copper-plate", 1),),
            ),
            "electronic-circuit": RecipeSpec(
                item="electronic-circuit",
                output_count=1,
                crafting_time_s=0.5,
                ingredients=(
                    RecipeIngredient("iron-plate", 1),
                    RecipeIngredient("copper-cable", 3),
                ),
            ),
            "transport-belt": RecipeSpec(
                item="transport-belt",
                output_count=2,
                crafting_time_s=0.5,
                ingredients=(
                    RecipeIngredient("iron-plate", 1),
                    RecipeIngredient("iron-gear-wheel", 1),
                ),
            ),
            "inserter": RecipeSpec(
                item="inserter",
                output_count=1,
                crafting_time_s=0.5,
                ingredients=(
                    RecipeIngredient("electronic-circuit", 1),
                    RecipeIngredient("iron-gear-wheel", 1),
                    RecipeIngredient("iron-plate", 1),
                ),
            ),
            "iron-gear-wheel": RecipeSpec(
                item="iron-gear-wheel",
                output_count=1,
                crafting_time_s=0.5,
                ingredients=(RecipeIngredient("iron-plate", 2),),
            ),
            "logistic-science-pack": RecipeSpec(
                item="logistic-science-pack",
                output_count=1,
                crafting_time_s=6.0,
                ingredients=(
                    RecipeIngredient("transport-belt", 1),
                    RecipeIngredient("inserter", 1),
                ),
            ),
        }
        self.planner = ProductionDagPlanner(
            mapping_recipe_provider(self.recipes),
            raw_items={"iron-plate", "copper-plate"},
        )

    def test_green_science_expands_into_shared_intermediates(self) -> None:
        dag = self.planner.plan("logistic-science-pack", 1.0)
        self.assertEqual(dag.target_rate_per_s, 1.0)
        self.assertAlmostEqual(dag.raw_requirements_per_s["copper-plate"], 1.5)
        self.assertAlmostEqual(dag.raw_requirements_per_s["iron-plate"], 5.5)
        self.assertEqual(dag.node("logistic-science-pack").minimum_machines(), 6)

    def test_dependencies_are_topological(self) -> None:
        dag = self.planner.plan("logistic-science-pack", 0.5)
        names = [node.item for node in dag.nodes]
        self.assertLess(names.index("copper-cable"), names.index("electronic-circuit"))
        self.assertLess(names.index("electronic-circuit"), names.index("inserter"))
        self.assertEqual(names[-1], "logistic-science-pack")

    def test_unknown_recipe_becomes_raw_requirement(self) -> None:
        dag = self.planner.plan("mystery-item", 2.0)
        self.assertFalse(dag.nodes)
        self.assertEqual(dag.raw_requirements_per_s["mystery-item"], 2.0)


if __name__ == "__main__":
    unittest.main()
