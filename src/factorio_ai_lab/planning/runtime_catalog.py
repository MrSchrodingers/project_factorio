from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.planning.production_dag import (
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeSpec,
)


@dataclass(frozen=True)
class RuntimeRecipeChoice:
    recipe_name: str
    product_name: str
    product_amount: float
    spec: RecipeSpec
    enabled: bool


def _rows(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = payload.get(key, [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


class RuntimeFactorioCatalog:
    """Recipe/technology graph sourced from the live Factorio prototype tables.

    This is deterministic game knowledge, not learned evidence. Learned policy
    artifacts may optimize over this catalog but cannot overwrite its facts.
    """

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.recipe_rows = _rows(payload, "recipes")
        self.technology_rows = _rows(payload, "technologies")
        self.machine_rows = _rows(payload, "machines")
        self._recipes_by_product: dict[str, list[RuntimeRecipeChoice]] = (
            defaultdict(list)
        )
        self._unlock_by_recipe: dict[str, list[str]] = defaultdict(list)
        self._build_indexes()

    def _build_indexes(self) -> None:
        for row in self.recipe_rows:
            name = str(row.get("name") or "")
            if not name:
                continue
            ingredients_raw = row.get("ingredients", [])
            ingredients = tuple(
                RecipeIngredient(
                    str(item.get("name")),
                    float(item.get("amount", 0.0)),
                )
                for item in ingredients_raw
                if isinstance(item, Mapping)
                and item.get("name")
                and isinstance(item.get("amount"), (int, float))
                and float(item.get("amount", 0.0)) > 0
            )
            products_raw = row.get("products", [])
            for product in products_raw:
                if not isinstance(product, Mapping):
                    continue
                product_name = str(product.get("name") or "")
                amount = product.get("amount")
                if (
                    not product_name
                    or not isinstance(amount, (int, float))
                    or float(amount) <= 0
                ):
                    continue
                spec = RecipeSpec(
                    item=product_name,
                    output_count=float(amount),
                    crafting_time_s=max(0.001, float(row.get("energy", 0.5) or 0.5)),
                    ingredients=ingredients,
                    category=(
                        str((row.get("categories") or ["crafting"])[0])
                        if isinstance(row.get("categories"), list)
                        and row.get("categories")
                        else "crafting"
                    ),
                )
                self._recipes_by_product[product_name].append(
                    RuntimeRecipeChoice(
                        recipe_name=name,
                        product_name=product_name,
                        product_amount=float(amount),
                        spec=spec,
                        enabled=bool(row.get("enabled")),
                    )
                )

        for row in self.technology_rows:
            technology = str(row.get("name") or "")
            if not technology:
                continue
            for recipe_name in row.get("unlocks", []) or []:
                self._unlock_by_recipe[str(recipe_name)].append(technology)

    def recipe_choice(self, item: str) -> RuntimeRecipeChoice | None:
        choices = self._recipes_by_product.get(item, [])
        if not choices:
            return None
        # Prefer the canonical same-name recipe, then currently enabled
        # alternatives, then recipes with fewer ingredient types/byproducts.
        return min(
            choices,
            key=lambda choice: (
                choice.recipe_name != item,
                not choice.enabled,
                len(choice.spec.ingredients),
                choice.recipe_name,
            ),
        )

    def recipe_provider(self, item: str) -> RecipeSpec | None:
        choice = self.recipe_choice(item)
        return choice.spec if choice is not None else None

    def planner(self) -> ProductionDagPlanner:
        return ProductionDagPlanner(self.recipe_provider)

    def unlock_technologies(self, item: str) -> tuple[str, ...]:
        choice = self.recipe_choice(item)
        if choice is None:
            return ()
        return tuple(sorted(self._unlock_by_recipe.get(choice.recipe_name, [])))

    def dependency_subgraph(
        self,
        target_item: str,
        *,
        max_depth: int = 12,
    ) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        visiting: set[str] = set()

        def visit(item: str, depth: int) -> None:
            if depth > max_depth or item in visiting:
                return
            choice = self.recipe_choice(item)
            if choice is None:
                nodes.setdefault(
                    item,
                    {
                        "id": item,
                        "kind": "raw_or_unresolved",
                        "recipe": None,
                        "enabled": None,
                        "unlock_technologies": [],
                    },
                )
                return
            nodes[item] = {
                "id": item,
                "kind": "recipe",
                "recipe": choice.recipe_name,
                "enabled": choice.enabled,
                "category": choice.spec.category,
                "crafting_time_s": choice.spec.crafting_time_s,
                "unlock_technologies": list(
                    self._unlock_by_recipe.get(choice.recipe_name, [])
                ),
            }
            visiting.add(item)
            for ingredient in choice.spec.ingredients:
                edges.append(
                    {
                        "source": ingredient.item,
                        "target": item,
                        "relation": "ingredient",
                    }
                )
                visit(ingredient.item, depth + 1)
            visiting.remove(item)

        visit(target_item, 0)
        return {
            "target": target_item,
            "nodes": list(nodes.values()),
            "edges": edges,
        }

    def summary(self) -> dict[str, Any]:
        researched = sum(
            bool(row.get("researched")) for row in self.technology_rows
        )
        enabled_recipes = sum(bool(row.get("enabled")) for row in self.recipe_rows)
        return {
            "connected": bool(self.payload.get("connected", False)),
            "factorio_version": self.payload.get("factorio_version"),
            "recipe_count": len(self.recipe_rows),
            "technology_count": len(self.technology_rows),
            "machine_count": len(self.machine_rows),
            "enabled_recipe_count": enabled_recipes,
            "researched_technology_count": researched,
            "product_count": len(self._recipes_by_product),
        }
