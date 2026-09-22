from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecipeIngredient:
    item: str
    count: float


@dataclass(frozen=True)
class RecipeSpec:
    item: str
    output_count: float
    crafting_time_s: float
    ingredients: tuple[RecipeIngredient, ...]
    category: str = "crafting"

    def __post_init__(self) -> None:
        if self.output_count <= 0:
            raise ValueError("output_count must be positive")
        if self.crafting_time_s <= 0:
            raise ValueError("crafting_time_s must be positive")
        if any(ingredient.count <= 0 for ingredient in self.ingredients):
            raise ValueError("ingredient counts must be positive")


@dataclass(frozen=True)
class ProductionNode:
    item: str
    target_rate_per_s: float
    crafts_per_s: float
    recipe: RecipeSpec

    @property
    def minimum_machine_seconds_per_second(self) -> float:
        return self.crafts_per_s * self.recipe.crafting_time_s

    def minimum_machines(self, crafting_speed: float = 1.0) -> int:
        if crafting_speed <= 0:
            raise ValueError("crafting_speed must be positive")
        return max(
            1,
            math.ceil(
                self.minimum_machine_seconds_per_second / crafting_speed
                - 1e-12
            ),
        )


@dataclass(frozen=True)
class ProductionDag:
    target_item: str
    target_rate_per_s: float
    nodes: tuple[ProductionNode, ...]
    raw_requirements_per_s: Mapping[str, float]

    def node(self, item: str) -> ProductionNode | None:
        return next((node for node in self.nodes if node.item == item), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_item": self.target_item,
            "target_rate_per_s": self.target_rate_per_s,
            "nodes": [
                {
                    "item": node.item,
                    "target_rate_per_s": node.target_rate_per_s,
                    "crafts_per_s": node.crafts_per_s,
                    "crafting_time_s": node.recipe.crafting_time_s,
                    "minimum_machines_at_speed_1": node.minimum_machines(),
                    "ingredients": [
                        {
                            "item": ingredient.item,
                            "count": ingredient.count,
                        }
                        for ingredient in node.recipe.ingredients
                    ],
                }
                for node in self.nodes
            ],
            "raw_requirements_per_s": dict(
                sorted(self.raw_requirements_per_s.items())
            ),
        }


RecipeProvider = Callable[[str], RecipeSpec | None]


class ProductionDagPlanner:
    """Expand a Factorio target into a deterministic rate-balanced recipe DAG."""

    def __init__(
        self,
        recipe_provider: RecipeProvider,
        *,
        raw_items: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self.recipe_provider = recipe_provider
        self.raw_items = frozenset(raw_items)

    def plan(
        self,
        target_item: str,
        target_rate_per_s: float,
    ) -> ProductionDag:
        if target_rate_per_s <= 0:
            raise ValueError("target_rate_per_s must be positive")

        requested: defaultdict[str, float] = defaultdict(float)
        expanded: defaultdict[str, float] = defaultdict(float)
        raw: defaultdict[str, float] = defaultdict(float)
        recipes: dict[str, RecipeSpec] = {}
        dependencies: dict[str, set[str]] = defaultdict(set)
        stack: list[str] = []

        def expand(item: str, rate: float) -> None:
            requested[item] += rate
            if item in self.raw_items:
                raw[item] += rate
                return

            recipe = self.recipe_provider(item)
            if recipe is None:
                raw[item] += rate
                return
            recipes[item] = recipe

            if item in stack:
                cycle = " -> ".join([*stack, item])
                raise ValueError(f"recipe dependency cycle: {cycle}")

            incremental = requested[item] - expanded[item]
            if incremental <= 1e-12:
                return
            expanded[item] = requested[item]

            crafts_per_s = incremental / recipe.output_count
            stack.append(item)
            for ingredient in recipe.ingredients:
                dependencies[item].add(ingredient.item)
                expand(
                    ingredient.item,
                    crafts_per_s * ingredient.count,
                )
            stack.pop()

        expand(target_item, target_rate_per_s)

        # Shared intermediates may receive demand after their first expansion.
        # Iterate until all craftable demand has been propagated.
        changed = True
        while changed:
            changed = False
            for item, total_rate in list(requested.items()):
                recipe = recipes.get(item) or self.recipe_provider(item)
                if recipe is None or item in self.raw_items:
                    continue
                recipes[item] = recipe
                incremental = total_rate - expanded[item]
                if incremental <= 1e-12:
                    continue
                expanded[item] = total_rate
                crafts_per_s = incremental / recipe.output_count
                for ingredient in recipe.ingredients:
                    dependencies[item].add(ingredient.item)
                    before = requested[ingredient.item]
                    requested[ingredient.item] += crafts_per_s * ingredient.count
                    if requested[ingredient.item] > before + 1e-12:
                        changed = True

        visited: set[str] = set()
        order: list[str] = []

        def visit(item: str) -> None:
            if item in visited or item not in recipes:
                return
            visited.add(item)
            for dependency in sorted(dependencies.get(item, ())):
                visit(dependency)
            order.append(item)

        visit(target_item)

        nodes = tuple(
            ProductionNode(
                item=item,
                target_rate_per_s=requested[item],
                crafts_per_s=requested[item] / recipes[item].output_count,
                recipe=recipes[item],
            )
            for item in order
        )
        return ProductionDag(
            target_item=target_item,
            target_rate_per_s=target_rate_per_s,
            nodes=nodes,
            raw_requirements_per_s=dict(raw),
        )


def mapping_recipe_provider(
    recipes: Mapping[str, RecipeSpec],
) -> RecipeProvider:
    return recipes.get
