from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from factorio_ai_lab.planning.production_dag import (
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeSpec,
)

#: Ticks per second in Factorio. Belt speed is reported per tick, so every
#: per-second figure here is derived from this single constant.
TICKS_PER_SECOND = 60.0

#: Unit the runtime states belt speed in.
BELT_SPEED_UNIT = "tiles_per_tick"

#: The probe read the field and the runtime answered with a number.
PROBE_MEASURED = "measured"
#: The probe ran and the runtime answered that this entity has no such field.
PROBE_ABSENT = "absent"
#: The read itself failed, so nothing is known about the field.
PROBE_FAILED = "probe_failed"
#: No status travelled with the row, so a failed read and an absent field
#: cannot be told apart. Kept distinct from ``PROBE_ABSENT`` on purpose: a
#: payload that cannot say is not a payload that says "no".
PROBE_UNKNOWN = "unknown"

_PROBE_STATUSES = frozenset(
    {PROBE_MEASURED, PROBE_ABSENT, PROBE_FAILED, PROBE_UNKNOWN}
)


@dataclass(frozen=True)
class RuntimeRecipeChoice:
    recipe_name: str
    product_name: str
    product_amount: float
    spec: RecipeSpec
    enabled: bool


@dataclass(frozen=True)
class MachineSpeed:
    """Speeds one machine prototype reports, with how each was obtained."""

    machine: str
    crafting_speed: float | None
    crafting_speed_status: str
    mining_speed: float | None
    mining_speed_status: str

    @property
    def crafting_speed_measured(self) -> bool:
        return self.crafting_speed_status == PROBE_MEASURED

    @property
    def mining_speed_measured(self) -> bool:
        return self.mining_speed_status == PROBE_MEASURED

    def as_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "crafting_speed": self.crafting_speed,
            "crafting_speed_status": self.crafting_speed_status,
            "mining_speed": self.mining_speed,
            "mining_speed_status": self.mining_speed_status,
        }


@dataclass(frozen=True)
class BeltSpeed:
    """Throughput geometry of one belt prototype, in the runtime's own unit."""

    name: str
    entity_type: str | None
    tiles_per_tick: float | None
    belt_speed_status: str
    max_underground_distance: int | None
    max_underground_distance_status: str
    unit: str = BELT_SPEED_UNIT

    @property
    def belt_speed_measured(self) -> bool:
        return self.belt_speed_status == PROBE_MEASURED

    @property
    def tiles_per_second(self) -> float | None:
        """Belt speed in tiles per second, derived from the per-tick figure."""
        if self.tiles_per_tick is None:
            return None
        return self.tiles_per_tick * TICKS_PER_SECOND

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "tiles_per_tick": self.tiles_per_tick,
            "tiles_per_second": self.tiles_per_second,
            "belt_speed_unit": self.unit,
            "belt_speed_status": self.belt_speed_status,
            "max_underground_distance": self.max_underground_distance,
            "max_underground_distance_status": (
                self.max_underground_distance_status
            ),
        }


def _probe_number(
    row: Mapping[str, Any],
    value_key: str,
    status_key: str,
) -> tuple[float | None, str]:
    """Read one probed number together with the status that qualifies it.

    An absent field and a failed read both arrive as no value at all, which
    is the conflation that let a removed accessor pass for "this machine has
    no speed". Only the status separates them, so a value without a status
    that vouches for it is never returned.
    """
    raw = row.get(value_key)
    value: float | None = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        candidate = float(raw)
        if isfinite(candidate):
            value = candidate
    raw_status = row.get(status_key)
    status = (
        raw_status
        if isinstance(raw_status, str) and raw_status in _PROBE_STATUSES
        else None
    )
    if status is None:
        status = PROBE_MEASURED if value is not None else PROBE_UNKNOWN
    if status == PROBE_MEASURED and value is None:
        status = PROBE_UNKNOWN
    if status != PROBE_MEASURED:
        value = None
    return value, status


def _machine_speed_from_row(row: Mapping[str, Any]) -> MachineSpeed | None:
    name = str(row.get("name") or "")
    if not name:
        return None
    crafting, crafting_status = _probe_number(
        row,
        "crafting_speed",
        "crafting_speed_status",
    )
    mining, mining_status = _probe_number(
        row,
        "mining_speed",
        "mining_speed_status",
    )
    return MachineSpeed(
        machine=name,
        crafting_speed=crafting,
        crafting_speed_status=crafting_status,
        mining_speed=mining,
        mining_speed_status=mining_status,
    )


def _belt_speed_from_row(row: Mapping[str, Any]) -> BeltSpeed | None:
    name = str(row.get("name") or "")
    if not name:
        return None
    speed, speed_status = _probe_number(row, "belt_speed", "belt_speed_status")
    distance, distance_status = _probe_number(
        row,
        "max_underground_distance",
        "max_underground_distance_status",
    )
    unit = row.get("belt_speed_unit")
    entity_type = row.get("type")
    return BeltSpeed(
        name=name,
        entity_type=str(entity_type) if entity_type else None,
        tiles_per_tick=speed,
        belt_speed_status=speed_status,
        max_underground_distance=(
            int(distance) if distance is not None else None
        ),
        max_underground_distance_status=distance_status,
        unit=str(unit) if isinstance(unit, str) and unit else BELT_SPEED_UNIT,
    )


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
        self.belt_rows = _rows(payload, "belts")
        self._recipes_by_product: dict[str, list[RuntimeRecipeChoice]] = (
            defaultdict(list)
        )
        self._unlock_by_recipe: dict[str, list[str]] = defaultdict(list)
        self._machine_speeds: dict[str, MachineSpeed] = {}
        self._belt_speeds: dict[str, BeltSpeed] = {}
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

        for row in self.machine_rows:
            machine_speed = _machine_speed_from_row(row)
            if machine_speed is not None:
                self._machine_speeds[machine_speed.machine] = machine_speed

        for row in self.belt_rows:
            belt_speed = _belt_speed_from_row(row)
            if belt_speed is not None:
                self._belt_speeds[belt_speed.name] = belt_speed

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

    def machine_speed(self, machine: str) -> MachineSpeed:
        """Speeds for ``machine``, unknown when no row carries that name.

        A machine the runtime did not report reads as ``PROBE_UNKNOWN`` with
        no values, so a fact that was never measured cannot pass for a slow
        machine or for a machine that crafts nothing.
        """
        known = self._machine_speeds.get(machine)
        if known is not None:
            return known
        return MachineSpeed(
            machine=machine,
            crafting_speed=None,
            crafting_speed_status=PROBE_UNKNOWN,
            mining_speed=None,
            mining_speed_status=PROBE_UNKNOWN,
        )

    def machine_speeds(self) -> tuple[MachineSpeed, ...]:
        return tuple(self._machine_speeds.values())

    def belt_speed(self, name: str) -> BeltSpeed | None:
        return self._belt_speeds.get(name)

    def belt_speeds(self) -> tuple[BeltSpeed, ...]:
        return tuple(self._belt_speeds.values())

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
            "belt_count": len(self.belt_rows),
            "measured_crafting_speed_count": sum(
                speed.crafting_speed_measured
                for speed in self._machine_speeds.values()
            ),
            "belt_speeds": [
                belt.as_dict() for belt in self._belt_speeds.values()
            ],
            "enabled_recipe_count": enabled_recipes,
            "researched_technology_count": researched,
            "product_count": len(self._recipes_by_product),
        }
