from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from factorio_ai_lab.planning.production_dag import (
    PROBE_ABSENT,
    PROBE_FAILED,
    PROBE_MEASURED,
    PROBE_STATUSES,
    PROBE_UNKNOWN,
    ProductionDagPlanner,
    RecipeIngredient,
    RecipeProduct,
    RecipeSpec,
)

#: Ticks per second in Factorio. Belt speed is reported per tick, so every
#: per-second figure here is derived from this single constant.
TICKS_PER_SECOND = 60.0

#: Unit the runtime states belt speed in.
BELT_SPEED_UNIT = "tiles_per_tick"

# The probe statuses are defined next to ``RecipeSpec``, which carries one,
# and re-exported here because this module is where callers read them from:
# ``PROBE_MEASURED`` (the probe read the field and the runtime answered with a
# number), ``PROBE_ABSENT`` (the runtime answered that this entity has no such
# field), ``PROBE_FAILED`` (the read itself failed) and ``PROBE_UNKNOWN`` (no
# status travelled with the row, so a failed read and an absent field cannot
# be told apart).

__all__ = [
    "BELT_SPEED_UNIT",
    "PROBE_ABSENT",
    "PROBE_FAILED",
    "PROBE_MEASURED",
    "PROBE_STATUSES",
    "PROBE_UNKNOWN",
    "TICKS_PER_SECOND",
    "BeltSpeed",
    "FuelSpec",
    "MachineEnergy",
    "MachineSpeed",
    "RecipeProduct",
    "RuntimeFactorioCatalog",
    "RuntimeRecipeChoice",
]


@dataclass(frozen=True)
class RuntimeRecipeChoice:
    """The recipe this catalog plans ``product_name`` with.

    ``product_amount`` is what one execution yields of ``product_name``;
    :attr:`products` is everything that same execution yields, so a consumer
    can credit the byproducts instead of planning them again.
    """

    recipe_name: str
    product_name: str
    product_amount: float
    spec: RecipeSpec
    enabled: bool

    @property
    def products(self) -> tuple[RecipeProduct, ...]:
        return self.spec.products

    @property
    def byproducts(self) -> tuple[RecipeProduct, ...]:
        return self.spec.byproducts


@dataclass(frozen=True)
class MachineEnergy:
    """Energy-source facts for one machine prototype."""

    machine: str
    source_type: str | None
    source_type_status: str
    energy_usage_per_tick_j: float | None
    energy_usage_status: str
    fuel_categories: tuple[str, ...] = ()
    fuel_categories_status: str = PROBE_UNKNOWN

    @property
    def source_measured(self) -> bool:
        return self.source_type_status == PROBE_MEASURED

    @property
    def energy_usage_measured(self) -> bool:
        return self.energy_usage_status == PROBE_MEASURED

    @property
    def burner(self) -> bool:
        return self.source_measured and self.source_type == "burner"

    def as_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "source_type": self.source_type,
            "source_type_status": self.source_type_status,
            "energy_usage_per_tick_j": self.energy_usage_per_tick_j,
            "energy_usage_status": self.energy_usage_status,
            "fuel_categories": list(self.fuel_categories),
            "fuel_categories_status": self.fuel_categories_status,
        }


@dataclass(frozen=True)
class FuelSpec:
    """One runtime item that can act as fuel."""

    name: str
    fuel_value_j: float | None
    fuel_value_status: str
    fuel_categories: tuple[str, ...]
    fuel_categories_status: str

    @property
    def measured(self) -> bool:
        return (
            self.fuel_value_status == PROBE_MEASURED
            and self.fuel_value_j is not None
            and self.fuel_value_j > 0
        )

    def compatible_with(self, categories: Sequence[str]) -> bool:
        wanted = frozenset(str(value) for value in categories)
        return bool(wanted.intersection(self.fuel_categories))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fuel_value_j": self.fuel_value_j,
            "fuel_value_status": self.fuel_value_status,
            "fuel_categories": list(self.fuel_categories),
            "fuel_categories_status": self.fuel_categories_status,
        }


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
        if isinstance(raw_status, str) and raw_status in PROBE_STATUSES
        else None
    )
    if status is None:
        status = PROBE_MEASURED if value is not None else PROBE_UNKNOWN
    if status == PROBE_MEASURED and value is None:
        status = PROBE_UNKNOWN
    if status != PROBE_MEASURED:
        value = None
    return value, status


def _probe_status(
    row: Mapping[str, Any],
    status_key: str,
    *,
    measured_if: bool = False,
) -> str:
    raw = row.get(status_key)
    if isinstance(raw, str) and raw in PROBE_STATUSES:
        return raw
    return PROBE_MEASURED if measured_if else PROBE_UNKNOWN


def _machine_energy_from_row(row: Mapping[str, Any]) -> MachineEnergy | None:
    name = str(row.get("name") or "")
    if not name:
        return None
    source_raw = row.get("energy_source_type")
    source_type = str(source_raw) if isinstance(source_raw, str) and source_raw else None
    source_status = _probe_status(
        row,
        "energy_source_status",
        measured_if=source_type is not None,
    )
    if source_status != PROBE_MEASURED:
        source_type = None

    usage, usage_status = _probe_number(
        row,
        "energy_usage_per_tick_j",
        "energy_usage_status",
    )

    raw_categories = row.get("fuel_categories")
    categories = (
        tuple(sorted(str(value) for value in raw_categories if isinstance(value, str)))
        if isinstance(raw_categories, Sequence)
        and not isinstance(raw_categories, (str, bytes))
        else ()
    )
    categories_status = _probe_status(
        row,
        "fuel_categories_status",
        measured_if=bool(categories),
    )
    if categories_status != PROBE_MEASURED:
        categories = ()

    return MachineEnergy(
        machine=name,
        source_type=source_type,
        source_type_status=source_status,
        energy_usage_per_tick_j=usage,
        energy_usage_status=usage_status,
        fuel_categories=categories,
        fuel_categories_status=categories_status,
    )


def _fuel_spec_from_row(row: Mapping[str, Any]) -> FuelSpec | None:
    name = str(row.get("name") or "")
    if not name:
        return None
    value, value_status = _probe_number(
        row,
        "fuel_value_j",
        "fuel_value_status",
    )
    raw_categories = row.get("fuel_categories")
    categories = (
        tuple(sorted(str(item) for item in raw_categories if isinstance(item, str)))
        if isinstance(raw_categories, Sequence)
        and not isinstance(raw_categories, (str, bytes))
        else ()
    )
    categories_status = _probe_status(
        row,
        "fuel_categories_status",
        measured_if=bool(categories),
    )
    if categories_status != PROBE_MEASURED:
        categories = ()
    return FuelSpec(
        name=name,
        fuel_value_j=value,
        fuel_value_status=value_status,
        fuel_categories=categories,
        fuel_categories_status=categories_status,
    )


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


def _recipe_time(row: Mapping[str, Any]) -> tuple[float | None, str]:
    """Crafting time of a recipe row, with the status that qualifies it.

    The runtime states it as ``energy``, in seconds. A row that carries no
    usable ``energy`` yields no time at all: substituting a plausible default
    here is how an absent field used to reach every machine count downstream
    as if it had been measured. A zero or negative figure is not a crafting
    time either, so it reads as unknown rather than as a measurement.
    """
    value, status = _probe_number(row, "energy", "energy_status")
    if value is not None and value <= 0.0:
        return None, PROBE_UNKNOWN
    return value, status


def _recipe_products(row: Mapping[str, Any]) -> tuple[RecipeProduct, ...]:
    """Every product one execution of this recipe yields.

    Oil processing is the case that makes this mandatory: one execution of
    ``advanced-oil-processing`` yields heavy oil, light oil and petroleum gas
    together, and a plan that sees only the product it asked for counts the
    other two a second time.
    """
    products_raw = row.get("products", [])
    if not isinstance(products_raw, Sequence) or isinstance(
        products_raw, (str, bytes)
    ):
        return ()
    products: list[RecipeProduct] = []
    for product in products_raw:
        if not isinstance(product, Mapping):
            continue
        name = str(product.get("name") or "")
        amount = product.get("amount")
        if (
            not name
            or not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or float(amount) <= 0
        ):
            continue
        products.append(RecipeProduct(name, float(amount)))
    return tuple(products)


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
        self.fuel_rows = _rows(payload, "fuels")
        self._recipes_by_product: dict[str, list[RuntimeRecipeChoice]] = (
            defaultdict(list)
        )
        self._unlock_by_recipe: dict[str, list[str]] = defaultdict(list)
        self._machine_speeds: dict[str, MachineSpeed] = {}
        self._machine_energy: dict[str, MachineEnergy] = {}
        self._belt_speeds: dict[str, BeltSpeed] = {}
        self._fuels: dict[str, FuelSpec] = {}
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
            products = _recipe_products(row)
            crafting_time_s, crafting_time_status = _recipe_time(row)
            category = (
                str((row.get("categories") or ["crafting"])[0])
                if isinstance(row.get("categories"), list)
                and row.get("categories")
                else "crafting"
            )
            for product in products:
                spec = RecipeSpec(
                    item=product.item,
                    output_count=product.count,
                    crafting_time_s=crafting_time_s,
                    ingredients=ingredients,
                    category=category,
                    products=products,
                    crafting_time_status=crafting_time_status,
                )
                self._recipes_by_product[product.item].append(
                    RuntimeRecipeChoice(
                        recipe_name=name,
                        product_name=product.item,
                        product_amount=product.count,
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
            machine_energy = _machine_energy_from_row(row)
            if machine_energy is not None:
                self._machine_energy[machine_energy.machine] = machine_energy

        for row in self.belt_rows:
            belt_speed = _belt_speed_from_row(row)
            if belt_speed is not None:
                self._belt_speeds[belt_speed.name] = belt_speed

        for row in self.fuel_rows:
            fuel = _fuel_spec_from_row(row)
            if fuel is not None:
                self._fuels[fuel.name] = fuel

    def recipe_choices(self, item: str) -> tuple[RuntimeRecipeChoice, ...]:
        """Every recipe that yields ``item``, in payload order.

        :meth:`recipe_choice` answers with one of these. The full list is what
        lets a caller see the alternatives, including the multi-product ones
        whose other outputs it would otherwise plan twice.
        """
        return tuple(self._recipes_by_product.get(item, []))

    def recipe_choice(self, item: str) -> RuntimeRecipeChoice | None:
        choices = self._recipes_by_product.get(item, [])
        if not choices:
            return None
        # Prefer the canonical same-name recipe, then currently enabled
        # alternatives, then recipes with fewer ingredient types, then the
        # first by name. Byproducts do not enter the order: the chosen recipe
        # states them on :attr:`RuntimeRecipeChoice.products` instead.
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

    def machine_energy(self, machine: str) -> MachineEnergy:
        known = self._machine_energy.get(machine)
        if known is not None:
            return known
        return MachineEnergy(
            machine=machine,
            source_type=None,
            source_type_status=PROBE_UNKNOWN,
            energy_usage_per_tick_j=None,
            energy_usage_status=PROBE_UNKNOWN,
            fuel_categories=(),
            fuel_categories_status=PROBE_UNKNOWN,
        )

    def machine_energies(self) -> tuple[MachineEnergy, ...]:
        return tuple(self._machine_energy.values())

    def fuel(self, name: str) -> FuelSpec | None:
        return self._fuels.get(name)

    def fuels(self) -> tuple[FuelSpec, ...]:
        return tuple(self._fuels.values())

    def compatible_fuels(self, categories: Sequence[str]) -> tuple[FuelSpec, ...]:
        return tuple(
            sorted(
                (
                    fuel
                    for fuel in self._fuels.values()
                    if fuel.measured and fuel.compatible_with(categories)
                ),
                key=lambda fuel: (
                    -(fuel.fuel_value_j or 0.0),
                    fuel.name,
                ),
            )
        )

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
                "crafting_time_status": choice.spec.crafting_time_status,
                "products": [
                    {"item": product.item, "count": product.count}
                    for product in choice.products
                ],
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
            "fuel_count": len(self.fuel_rows),
            "measured_crafting_speed_count": sum(
                speed.crafting_speed_measured
                for speed in self._machine_speeds.values()
            ),
            "measured_crafting_time_count": sum(
                _recipe_time(row)[1] == PROBE_MEASURED
                for row in self.recipe_rows
            ),
            "belt_speeds": [
                belt.as_dict() for belt in self._belt_speeds.values()
            ],
            "enabled_recipe_count": enabled_recipes,
            "researched_technology_count": researched,
            "product_count": len(self._recipes_by_product),
        }
