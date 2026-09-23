from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: The crafting time was read off a source that answered with a number.
PROBE_MEASURED = "measured"
#: The source was read and it states this recipe has no such field.
PROBE_ABSENT = "absent"
#: The read itself failed, so nothing is known about the field.
PROBE_FAILED = "probe_failed"
#: No status travelled with the value, so a failed read and an absent field
#: cannot be told apart. Kept distinct from ``PROBE_ABSENT`` on purpose: a
#: source that cannot say is not a source that says "no".
PROBE_UNKNOWN = "unknown"

#: The statuses a measured figure may carry. Defined here, next to the spec
#: that carries one, and re-exported by ``runtime_catalog`` so the probe
#: vocabulary stays a single set of strings.
PROBE_STATUSES = frozenset(
    {PROBE_MEASURED, PROBE_ABSENT, PROBE_FAILED, PROBE_UNKNOWN}
)


@dataclass(frozen=True)
class RecipeIngredient:
    item: str
    count: float


@dataclass(frozen=True)
class RecipeProduct:
    """One output of a recipe, with what a single execution yields of it."""

    item: str
    count: float


@dataclass(frozen=True)
class RecipeSpec:
    """One recipe seen from one of its products.

    ``item``/``output_count`` name the product this spec plans for, while
    ``products`` lists everything the same execution yields. A spec built
    without ``products`` says nothing about byproducts rather than claiming
    there are none.

    ``crafting_time_s`` is ``None`` whenever the time was not measured, and
    ``crafting_time_status`` says which of the probe statuses applies. The two
    fields are kept consistent: a number only travels with ``measured``, and
    ``measured`` only travels with a number.
    """

    item: str
    output_count: float
    crafting_time_s: float | None
    ingredients: tuple[RecipeIngredient, ...]
    category: str = "crafting"
    products: tuple[RecipeProduct, ...] = ()
    crafting_time_status: str = PROBE_MEASURED

    def __post_init__(self) -> None:
        if self.output_count <= 0:
            raise ValueError("output_count must be positive")
        if self.crafting_time_status not in PROBE_STATUSES:
            raise ValueError(
                "crafting_time_status must be one of "
                f"{sorted(PROBE_STATUSES)}"
            )
        if self.crafting_time_s is None:
            if self.crafting_time_status == PROBE_MEASURED:
                raise ValueError(
                    "crafting_time_status 'measured' requires a crafting time"
                )
        else:
            if self.crafting_time_status != PROBE_MEASURED:
                raise ValueError(
                    "a crafting time may only travel with status 'measured'"
                )
            if self.crafting_time_s <= 0:
                raise ValueError("crafting_time_s must be positive")
        if any(ingredient.count <= 0 for ingredient in self.ingredients):
            raise ValueError("ingredient counts must be positive")
        if any(product.count <= 0 for product in self.products):
            raise ValueError("product counts must be positive")
        if self.products and not any(
            product.item == self.item
            and math.isclose(product.count, self.output_count, rel_tol=1e-9)
            for product in self.products
        ):
            raise ValueError(
                "products must contain this spec's own product at its "
                "output_count"
            )

    @property
    def crafting_time_measured(self) -> bool:
        return self.crafting_time_status == PROBE_MEASURED

    @property
    def byproducts(self) -> tuple[RecipeProduct, ...]:
        """Everything the same execution yields besides ``item``."""
        return tuple(
            product for product in self.products if product.item != self.item
        )


@dataclass(frozen=True)
class ProductionNode:
    item: str
    target_rate_per_s: float
    crafts_per_s: float
    recipe: RecipeSpec

    @property
    def minimum_machine_seconds_per_second(self) -> float | None:
        """Machine-seconds per second, or ``None`` when the time is unknown.

        An unmeasured crafting time cannot be turned into a machine count, so
        it answers with nothing instead of sizing on a substituted number.
        """
        if self.recipe.crafting_time_s is None:
            return None
        return self.crafts_per_s * self.recipe.crafting_time_s

    def minimum_machines(self, crafting_speed: float = 1.0) -> int | None:
        if crafting_speed <= 0:
            raise ValueError("crafting_speed must be positive")
        machine_seconds = self.minimum_machine_seconds_per_second
        if machine_seconds is None:
            return None
        return max(
            1,
            math.ceil(machine_seconds / crafting_speed - 1e-12),
        )

    @property
    def byproduct_rates_per_s(self) -> dict[str, float]:
        """Rate of every other product this node's crafts also yield.

        The planner sizes for ``item`` alone; these are the outputs the same
        crafts produce anyway, which a consumer must credit instead of
        planning them a second time.
        """
        return {
            product.item: self.crafts_per_s * product.count
            for product in self.recipe.byproducts
        }


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
                    "crafting_time_status": node.recipe.crafting_time_status,
                    "minimum_machines_at_speed_1": node.minimum_machines(),
                    "ingredients": [
                        {
                            "item": ingredient.item,
                            "count": ingredient.count,
                        }
                        for ingredient in node.recipe.ingredients
                    ],
                    "products": [
                        {
                            "item": product.item,
                            "count": product.count,
                        }
                        for product in node.recipe.products
                    ],
                    "byproducts_per_s": node.byproduct_rates_per_s,
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
