import unittest

from factorio_ai_lab.planning.production_dag import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    PROBE_UNKNOWN,
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeProduct,
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


class RecipeTimeStatusTests(unittest.TestCase):
    """A crafting time travels with the status that qualifies it."""

    def test_a_time_defaults_to_measured(self) -> None:
        spec = RecipeSpec(
            item="widget",
            output_count=1,
            crafting_time_s=0.5,
            ingredients=(),
        )
        self.assertEqual(spec.crafting_time_status, PROBE_MEASURED)
        self.assertTrue(spec.crafting_time_measured)

    def test_an_absent_time_is_allowed_when_the_status_says_so(self) -> None:
        spec = RecipeSpec(
            item="widget",
            output_count=1,
            crafting_time_s=None,
            ingredients=(),
            crafting_time_status=PROBE_UNKNOWN,
        )
        self.assertIsNone(spec.crafting_time_s)
        self.assertFalse(spec.crafting_time_measured)

    def test_an_absent_time_cannot_claim_to_be_measured(self) -> None:
        with self.assertRaises(ValueError):
            RecipeSpec(
                item="widget",
                output_count=1,
                crafting_time_s=None,
                ingredients=(),
            )

    def test_a_time_cannot_travel_with_an_unmeasured_status(self) -> None:
        with self.assertRaises(ValueError):
            RecipeSpec(
                item="widget",
                output_count=1,
                crafting_time_s=0.5,
                ingredients=(),
                crafting_time_status=PROBE_ABSENT,
            )

    def test_an_unknown_status_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RecipeSpec(
                item="widget",
                output_count=1,
                crafting_time_s=None,
                ingredients=(),
                crafting_time_status="probably",
            )

    def test_machine_count_is_unknown_when_the_time_is(self) -> None:
        planner = ProductionDagPlanner(
            mapping_recipe_provider(
                {
                    "widget": RecipeSpec(
                        item="widget",
                        output_count=1,
                        crafting_time_s=None,
                        ingredients=(RecipeIngredient("iron-plate", 1),),
                        crafting_time_status=PROBE_UNKNOWN,
                    )
                }
            ),
            raw_items={"iron-plate"},
        )
        node = planner.plan("widget", 2.0).node("widget")
        self.assertIsNone(node.minimum_machine_seconds_per_second)
        self.assertIsNone(node.minimum_machines())
        self.assertIsNone(node.minimum_machines(crafting_speed=0.5))

    def test_serialised_node_declares_the_time_status(self) -> None:
        planner = ProductionDagPlanner(
            mapping_recipe_provider(
                {
                    "widget": RecipeSpec(
                        item="widget",
                        output_count=1,
                        crafting_time_s=None,
                        ingredients=(RecipeIngredient("iron-plate", 1),),
                        crafting_time_status=PROBE_UNKNOWN,
                    )
                }
            ),
            raw_items={"iron-plate"},
        )
        node = planner.plan("widget", 2.0).to_dict()["nodes"][0]
        self.assertIsNone(node["crafting_time_s"])
        self.assertEqual(node["crafting_time_status"], PROBE_UNKNOWN)
        self.assertIsNone(node["minimum_machines_at_speed_1"])


class MultiProductRecipeTests(unittest.TestCase):
    """A recipe states every product one execution yields."""

    def _spec(self) -> RecipeSpec:
        return RecipeSpec(
            item="petroleum-gas",
            output_count=55,
            crafting_time_s=5.0,
            ingredients=(
                RecipeIngredient("water", 50),
                RecipeIngredient("crude-oil", 100),
            ),
            category="oil-processing",
            products=(
                RecipeProduct("heavy-oil", 25),
                RecipeProduct("light-oil", 45),
                RecipeProduct("petroleum-gas", 55),
            ),
        )

    def test_byproducts_exclude_the_product_the_spec_is_for(self) -> None:
        self.assertEqual(
            [(product.item, product.count) for product in self._spec().byproducts],
            [("heavy-oil", 25.0), ("light-oil", 45.0)],
        )

    def test_products_must_contain_the_product_the_spec_is_for(self) -> None:
        with self.assertRaises(ValueError):
            RecipeSpec(
                item="petroleum-gas",
                output_count=55,
                crafting_time_s=5.0,
                ingredients=(),
                products=(RecipeProduct("heavy-oil", 25),),
            )

    def test_product_counts_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            RecipeSpec(
                item="petroleum-gas",
                output_count=55,
                crafting_time_s=5.0,
                ingredients=(),
                products=(
                    RecipeProduct("petroleum-gas", 55),
                    RecipeProduct("heavy-oil", 0),
                ),
            )

    def test_byproduct_rates_follow_the_craft_rate(self) -> None:
        planner = ProductionDagPlanner(
            mapping_recipe_provider({"petroleum-gas": self._spec()}),
            raw_items={"water", "crude-oil"},
        )
        node = planner.plan("petroleum-gas", 110.0).node("petroleum-gas")
        self.assertAlmostEqual(node.crafts_per_s, 2.0)
        self.assertEqual(
            node.byproduct_rates_per_s,
            {"heavy-oil": 50.0, "light-oil": 90.0},
        )

    def test_serialised_node_carries_products_and_byproduct_rates(self) -> None:
        planner = ProductionDagPlanner(
            mapping_recipe_provider({"petroleum-gas": self._spec()}),
            raw_items={"water", "crude-oil"},
        )
        node = planner.plan("petroleum-gas", 55.0).to_dict()["nodes"][0]
        self.assertEqual(
            node["products"],
            [
                {"item": "heavy-oil", "count": 25.0},
                {"item": "light-oil", "count": 45.0},
                {"item": "petroleum-gas", "count": 55.0},
            ],
        )
        self.assertEqual(
            node["byproducts_per_s"],
            {"heavy-oil": 25.0, "light-oil": 45.0},
        )


if __name__ == "__main__":
    unittest.main()
