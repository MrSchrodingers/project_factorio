from __future__ import annotations

from factorio_ai_lab.planning.production_dag import (
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeSpec,
    mapping_recipe_provider,
)

FACTORIO_DATA_VERSION = "2.0.73"
DEFAULT_RECIPE_TIME_S = 0.5

EARLY_GAME_RECIPES: dict[str, RecipeSpec] = {
    "iron-gear-wheel": RecipeSpec(
        item="iron-gear-wheel",
        output_count=1,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(RecipeIngredient("iron-plate", 2),),
    ),
    "copper-cable": RecipeSpec(
        item="copper-cable",
        output_count=2,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(RecipeIngredient("copper-plate", 1),),
    ),
    "electronic-circuit": RecipeSpec(
        item="electronic-circuit",
        output_count=1,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(
            RecipeIngredient("iron-plate", 1),
            RecipeIngredient("copper-cable", 3),
        ),
    ),
    "transport-belt": RecipeSpec(
        item="transport-belt",
        output_count=2,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(
            RecipeIngredient("iron-plate", 1),
            RecipeIngredient("iron-gear-wheel", 1),
        ),
    ),
    "inserter": RecipeSpec(
        item="inserter",
        output_count=1,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(
            RecipeIngredient("electronic-circuit", 1),
            RecipeIngredient("iron-gear-wheel", 1),
            RecipeIngredient("iron-plate", 1),
        ),
    ),
    "automation-science-pack": RecipeSpec(
        item="automation-science-pack",
        output_count=1,
        crafting_time_s=5.0,
        ingredients=(
            RecipeIngredient("copper-plate", 1),
            RecipeIngredient("iron-gear-wheel", 1),
        ),
    ),
    "assembling-machine-1": RecipeSpec(
        item="assembling-machine-1",
        output_count=1,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(
            RecipeIngredient("electronic-circuit", 3),
            RecipeIngredient("iron-gear-wheel", 5),
            RecipeIngredient("iron-plate", 9),
        ),
    ),
    "logistic-science-pack": RecipeSpec(
        item="logistic-science-pack",
        output_count=1,
        crafting_time_s=6.0,
        ingredients=(
            RecipeIngredient("inserter", 1),
            RecipeIngredient("transport-belt", 1),
        ),
    ),
    "lab": RecipeSpec(
        item="lab",
        output_count=1,
        crafting_time_s=2.0,
        ingredients=(
            RecipeIngredient("electronic-circuit", 10),
            RecipeIngredient("iron-gear-wheel", 10),
            RecipeIngredient("transport-belt", 4),
        ),
    ),
    "electric-mining-drill": RecipeSpec(
        item="electric-mining-drill",
        output_count=1,
        crafting_time_s=2.0,
        ingredients=(
            RecipeIngredient("electronic-circuit", 3),
            RecipeIngredient("iron-gear-wheel", 5),
            RecipeIngredient("iron-plate", 10),
        ),
    ),
    "small-electric-pole": RecipeSpec(
        item="small-electric-pole",
        output_count=2,
        crafting_time_s=DEFAULT_RECIPE_TIME_S,
        ingredients=(
            RecipeIngredient("wood", 1),
            RecipeIngredient("copper-cable", 2),
        ),
    ),
}

RAW_EARLY_GAME_ITEMS = frozenset(
    {
        "iron-plate",
        "copper-plate",
        "coal",
        "stone",
        "wood",
    }
)

EARLY_GAME_PRODUCTION_PLANNER = ProductionDagPlanner(
    mapping_recipe_provider(EARLY_GAME_RECIPES),
    raw_items=RAW_EARLY_GAME_ITEMS,
)
