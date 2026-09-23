"""Community production patterns as a separable, auditable seed.

The Factorio community publishes layouts that are already good: a smelting
column fed by one drill, an assembly cell with its furnace bank sized to the
recipe. Handing those to the learner is a head start; handing them as opaque
blueprints destroys the evidence of what the learner itself added. So every
block produced here is tagged with the pattern it came from and the exact
parameters used (:class:`PatternProvenance`), which is what lets a later
comparison separate "this came from the seed" from "this was discovered".

Three rules give the module its shape.

*Ratios are derived, never written down.* How many furnaces one drill feeds,
how many furnaces one assembler eats, how many machines a belt saturates: all
of it comes out of :class:`~factorio_ai_lab.planning.runtime_catalog.
RuntimeFactorioCatalog` figures that carry a ``measured`` status, combined
with the recipe times the same catalog indexes. A figure that arrives under
any other status is not a slower machine and not a zero: the pattern declares
the ratio indeterminate, names what was missing, and refuses to emit a layout.
Refusing is the point. A seed that guesses a number is a seed that teaches the
learner a fiction it cannot tell from a measurement.

*Layouts are parameterised, never fixed.* Spacing, orientation, belt tails and
machine budgets are inputs, so a mutation operator has something to turn. A
pattern with a single shape cannot be improved on, only copied.

*Cost and saturation are declared.* Each block states the input rate that
saturates it and the output rate it sustains, both derived, so a caller can
hold the plant against what the game actually reports instead of trusting the
drawing.

Scope, stated rather than implied:

* no splitter and no underground belt. Both need the ``logistics``
  technology, and the open-play history shows it researched in 0 of 57 runs,
  so a main bus pattern would describe a factory that cannot be built;
* only square machines. Rotating a non-square footprint by a quarter turn
  swaps its tile extent, and every candidate machine here (2x2 furnaces, 2x2
  and 3x3 drills, 3x3 assemblers) is square, so the pattern refuses the case
  it would otherwise place wrongly;
* one ingredient per assembly cell. A two-ingredient recipe needs two furnace
  banks arriving on different sides of the machine, which is a different
  pattern, not a parameter of this one.

The module is pure: mappings in, dataclasses out, no RCON and no I/O. Turning
a :class:`ProductionBlock` into ``place_entity`` calls belongs to whoever owns
a runner.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.footprints import entity_footprint, entity_tiles
from factorio_ai_lab.planning.rebuild import Placement
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_MEASURED,
    PROBE_UNKNOWN,
    TICKS_PER_SECOND,
    RuntimeFactorioCatalog,
)

#: Lanes on a transport belt. The runtime reports ``belt_speed`` and nothing
#: else, so this and :data:`BELT_ITEM_SPACING_TILES` are engine geometry taken
#: as given. Every rate that uses them is stamped
#: :data:`RATE_ENGINE_CONSTANT` instead of :data:`RATE_MEASURED` and the
#: assumption is listed on the block, so a caller can see which figures are
#: purely measured and which lean on a constant.
BELT_LANES = 2
#: Tiles one item occupies along a belt lane.
BELT_ITEM_SPACING_TILES = 0.25

#: Pattern identifiers, carried on every block and every refusal.
PATTERN_SMELTING_LINE = "smelting_line"
PATTERN_ASSEMBLY_CELL = "assembly_cell"

#: Rate computed from figures that all carried ``measured``.
RATE_MEASURED = "derived_from_measured"
#: Rate computed from measured figures plus an engine constant above.
RATE_ENGINE_CONSTANT = "derived_with_engine_constant"
#: At least one input was missing or arrived under a status that does not
#: vouch for it, so the rate has no value. Distinct from zero on purpose.
RATE_INDETERMINATE = "indeterminate"

#: Why a pattern declined to emit a layout.
REFUSAL_RATE_INDETERMINATE = "rate_indeterminate"
REFUSAL_NO_RECIPE = "recipe_absent"
REFUSAL_INGREDIENT_MISMATCH = "recipe_ingredient_mismatch"
REFUSAL_MULTI_INGREDIENT = "multi_ingredient_recipe_unsupported"
REFUSAL_CATEGORY_MISMATCH = "machine_category_mismatch"
REFUSAL_NON_SQUARE_MACHINE = "non_square_machine_footprint"
REFUSAL_UNIT_FOOTPRINT_EXPECTED = "belt_or_inserter_not_one_tile"
REFUSAL_NO_CAPACITY = "sizing_yields_no_machine"
REFUSAL_OVERLAP = "placements_overlap"
REFUSAL_PARAMETER_OUT_OF_RANGE = "parameter_out_of_range"

#: What binds the machine count of a block.
LIMIT_DRILL = "drill"
LIMIT_INPUT_BELT = "input_belt"
LIMIT_OUTPUT_BELT = "output_belt"
LIMIT_BUDGET = "furnace_budget"
LIMIT_INGREDIENT_DEMAND = "ingredient_demand"

#: Where a local layout lands when no origin is given.
DEFAULT_ORIGIN = GridPoint(0, 0)

#: Directions, in the 16-way encoding the rest of the codebase uses.
DIRECTION_NORTH = 0
DIRECTION_EAST = 4
DIRECTION_SOUTH = 8
DIRECTION_WEST = 12
CARDINALS: tuple[int, ...] = (
    DIRECTION_NORTH,
    DIRECTION_EAST,
    DIRECTION_SOUTH,
    DIRECTION_WEST,
)

#: Local layouts are drawn flowing east and rotated into place.
_BASE_FLOW = DIRECTION_EAST

#: Constraints no payload this module reads can quantify. Listed on every
#: block so a caller knows where the plant is optimistic.
_INSERTER_CONSTRAINT = (
    "inserter throughput is not exported by the prototype probe, so a hand-over "
    "that cannot keep up would not show here"
)
_POWER_CONSTRAINT = (
    "power draw and fuel are not modelled here, so an unpowered or unfuelled "
    "block still reads as sustaining its output rate"
)
_RESOURCE_PATCH_CONSTRAINT = (
    "ore patch richness and drill coverage are not modelled, so the drill rate "
    "assumes an uninterrupted patch under the whole machine"
)

_VALID_PROBE_STATUSES = frozenset(
    {PROBE_MEASURED, "absent", "probe_failed", PROBE_UNKNOWN}
)


def _fmt(value: float) -> str:
    """Render a probed number the way the provenance string quotes it."""
    return repr(float(value))


def _finite_positive(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


@dataclass(frozen=True)
class DerivedRate:
    """One number the pattern used, with the chain it was derived from.

    ``value`` is ``None`` whenever ``status`` is :data:`RATE_INDETERMINATE`,
    so a rate that could not be established can never be read as a rate of
    zero.
    """

    name: str
    value: float | None
    unit: str
    status: str
    inputs: tuple[str, ...] = ()
    formula: str = ""
    item: str | None = None
    note: str | None = None

    @property
    def determined(self) -> bool:
        return self.value is not None and self.status != RATE_INDETERMINATE

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "item": self.item,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "inputs": list(self.inputs),
            "formula": self.formula,
            "note": self.note,
        }


def _indeterminate(
    name: str,
    unit: str,
    note: str,
    *,
    item: str | None = None,
    inputs: tuple[str, ...] = (),
) -> DerivedRate:
    return DerivedRate(
        name=name,
        value=None,
        unit=unit,
        status=RATE_INDETERMINATE,
        inputs=inputs,
        item=item,
        note=note,
    )


@dataclass(frozen=True)
class MachineThroughput:
    """What one machine running one recipe produces and eats, per second."""

    machine: str
    item: str
    crafts: DerivedRate
    output: DerivedRate
    consumption: Mapping[str, DerivedRate] = field(default_factory=dict)

    @property
    def determined(self) -> bool:
        return self.output.determined and all(
            rate.determined for rate in self.consumption.values()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "item": self.item,
            "crafts": self.crafts.as_dict(),
            "output": self.output.as_dict(),
            "consumption": {
                item: rate.as_dict() for item, rate in self.consumption.items()
            },
        }


@dataclass(frozen=True)
class PatternProvenance:
    """Where a layout came from: which pattern, which parameters, which rates."""

    pattern: str
    parameters: tuple[tuple[str, Any], ...]
    rates: tuple[DerivedRate, ...] = ()
    assumptions: tuple[str, ...] = ()

    def rate(self, name: str) -> DerivedRate | None:
        return next((rate for rate in self.rates if rate.name == name), None)

    def with_rates(self, *rates: DerivedRate) -> PatternProvenance:
        return PatternProvenance(
            pattern=self.pattern,
            parameters=self.parameters,
            rates=self.rates + rates,
            assumptions=self.assumptions,
        )

    def with_assumptions(self, *assumptions: str) -> PatternProvenance:
        fresh = tuple(
            entry for entry in assumptions if entry not in self.assumptions
        )
        return PatternProvenance(
            pattern=self.pattern,
            parameters=self.parameters,
            rates=self.rates,
            assumptions=self.assumptions + fresh,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "parameters": {name: value for name, value in self.parameters},
            "rates": [rate.as_dict() for rate in self.rates],
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class ProductionBlock:
    """A placeable block with its declared saturation and its origin."""

    pattern: str
    placements: tuple[Placement, ...]
    provenance: PatternProvenance
    #: Input rate per item that saturates the block as sized.
    input_saturation: tuple[DerivedRate, ...]
    #: Output rate per item the block sustains once saturated.
    output: tuple[DerivedRate, ...]
    #: Which constraint fixed the machine count.
    limiting_factor: str
    #: Tile box the block reserves, route margin included.
    bounding_box: tuple[GridPoint, GridPoint]
    unmeasured_constraints: tuple[str, ...] = ()

    def tiles(
        self,
        footprints: Mapping[str, tuple[int, int]] | None = None,
    ) -> set[GridPoint]:
        occupied: set[GridPoint] = set()
        for index, placement in enumerate(self.placements):
            occupied |= entity_tiles(placement.to_entity(index + 1), footprints)
        return occupied

    def as_dict(self) -> dict[str, Any]:
        minimum, maximum = self.bounding_box
        return {
            "pattern": self.pattern,
            "placements": [placement.to_dict() for placement in self.placements],
            "provenance": self.provenance.as_dict(),
            "input_saturation": [rate.as_dict() for rate in self.input_saturation],
            "output": [rate.as_dict() for rate in self.output],
            "limiting_factor": self.limiting_factor,
            "bounding_box": {
                "min": [minimum.x, minimum.y],
                "max": [maximum.x, maximum.y],
            },
            "unmeasured_constraints": list(self.unmeasured_constraints),
        }


@dataclass(frozen=True)
class PatternOutcome:
    """Either a block, or the reason there is none. Never a guessed block."""

    pattern: str
    provenance: PatternProvenance
    block: ProductionBlock | None = None
    refusal: str | None = None
    refusal_detail: str | None = None

    @property
    def produced(self) -> bool:
        return self.block is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "produced": self.produced,
            "refusal": self.refusal,
            "refusal_detail": self.refusal_detail,
            "provenance": self.provenance.as_dict(),
            "block": self.block.as_dict() if self.block is not None else None,
        }


@dataclass(frozen=True)
class SmeltingLineParams:
    """Knobs of the smelting line. Every field is meant to be mutated."""

    ore_item: str = "iron-ore"
    plate_item: str = "iron-plate"
    drill: str = "electric-mining-drill"
    furnace: str = "stone-furnace"
    belt: str = "transport-belt"
    inserter: str = "inserter"
    origin: GridPoint = DEFAULT_ORIGIN
    orientation: int = DIRECTION_EAST
    #: Tiles left between consecutive furnaces. Negative values are not
    #: rejected up front: they are what an unconstrained mutation produces,
    #: and the overlap check is what catches them.
    furnace_spacing: int = 0
    #: Extra belt tiles past the last furnace, on both belts.
    belt_tail: int = 0
    #: Empty tiles reserved around the block for later routing.
    route_margin: int = 1
    max_furnaces: int | None = None


@dataclass(frozen=True)
class AssemblyCellParams:
    """Knobs of the assembly cell. Every field is meant to be mutated."""

    target_item: str = "iron-gear-wheel"
    assembler: str = "assembling-machine-1"
    furnace: str = "stone-furnace"
    belt: str = "transport-belt"
    inserter: str = "inserter"
    origin: GridPoint = DEFAULT_ORIGIN
    orientation: int = DIRECTION_EAST
    furnace_spacing: int = 0
    #: Belt tiles between the last furnace and the feeding inserter.
    input_belt_tail: int = 1
    #: Belt tiles carrying the product away from the cell.
    output_belt_length: int = 3
    route_margin: int = 1
    max_furnaces: int | None = None


def _parameters(params: Any) -> tuple[tuple[str, Any], ...]:
    """Parameters as an ordered, hashable record for the provenance."""
    recorded: list[tuple[str, Any]] = []
    for entry in fields(params):
        value = getattr(params, entry.name)
        if isinstance(value, GridPoint):
            value = (value.x, value.y)
        recorded.append((entry.name, value))
    return tuple(recorded)


# --------------------------------------------------------------------------
# Reading the catalog: a number is only a number when a status vouches for it
# --------------------------------------------------------------------------


def _recipe_rows(catalog: RuntimeFactorioCatalog) -> Sequence[Mapping[str, Any]]:
    return catalog.recipe_rows


def _recipe_row(
    catalog: RuntimeFactorioCatalog,
    recipe_name: str,
) -> Mapping[str, Any] | None:
    for row in _recipe_rows(catalog):
        if str(row.get("name") or "") == recipe_name:
            return row
    return None


def recipe_time(
    catalog: RuntimeFactorioCatalog,
    recipe_name: str,
) -> tuple[float | None, str]:
    """Crafting time of a recipe, read off the raw row rather than the spec.

    ``RecipeSpec`` substitutes 0.5 s when a row carries no ``energy``, which
    is a sane default for a planner and a fabricated number for a pattern.
    Reading the row directly keeps the missing case missing.
    """
    row = _recipe_row(catalog, recipe_name)
    if row is None:
        return None, PROBE_UNKNOWN
    value = _finite_positive(row.get("energy"))
    if value is None:
        return None, PROBE_UNKNOWN
    return value, PROBE_MEASURED


def resource_mining_time(
    catalog: RuntimeFactorioCatalog,
    resource: str,
) -> tuple[float | None, str]:
    """Mining time of a resource prototype, with the status that qualifies it.

    Read from a ``resources`` section shaped like every other probed row:
    ``{"name": ..., "mining_time": ..., "mining_time_status": ...}``. The
    live prototype probe does not export that section yet, so this answers
    :data:`~factorio_ai_lab.planning.runtime_catalog.PROBE_UNKNOWN` against a
    live payload, and the smelting line refuses rather than assume 1 s.
    """
    raw_rows = catalog.payload.get("resources", [])
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        return None, PROBE_UNKNOWN
    for row in raw_rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("name") or "") != resource:
            continue
        raw_status = row.get("mining_time_status")
        status = (
            raw_status
            if isinstance(raw_status, str) and raw_status in _VALID_PROBE_STATUSES
            else PROBE_UNKNOWN
        )
        value = _finite_positive(row.get("mining_time"))
        if status != PROBE_MEASURED or value is None:
            return None, PROBE_UNKNOWN if value is None else status
        return value, PROBE_MEASURED
    return None, PROBE_UNKNOWN


def machine_categories(
    catalog: RuntimeFactorioCatalog,
    machine: str,
) -> tuple[str, ...] | None:
    """Crafting categories a machine reports, or ``None`` when it reported none.

    ``None`` is "the payload cannot say", not "the machine crafts nothing":
    the caller records it as an unmeasured constraint instead of refusing.
    """
    for row in catalog.machine_rows:
        if str(row.get("name") or "") != machine:
            continue
        raw = row.get("crafting_categories")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return None
        categories = tuple(str(entry) for entry in raw if entry)
        return categories or None
    return None


def belt_item_rate(catalog: RuntimeFactorioCatalog, belt: str) -> DerivedRate:
    """Items per second one belt carries, derived from its measured speed.

    ``belt_speed`` arrives in tiles per tick. Items per second follow from
    the tick rate the catalog already states and the two engine constants at
    the top of this module, which is why the status is
    :data:`RATE_ENGINE_CONSTANT` and never :data:`RATE_MEASURED`.
    """
    speed = catalog.belt_speed(belt)
    if speed is None:
        return _indeterminate(
            "belt_item_rate",
            "items_per_s",
            f"{belt} is absent from the belt rows, so its speed is unknown",
        )
    if not speed.belt_speed_measured or speed.tiles_per_tick is None:
        return _indeterminate(
            "belt_item_rate",
            "items_per_s",
            f"{belt}.belt_speed is {speed.belt_speed_status}, not measured",
        )
    tiles_per_second = speed.tiles_per_tick * TICKS_PER_SECOND
    value = tiles_per_second * BELT_LANES / BELT_ITEM_SPACING_TILES
    return DerivedRate(
        name="belt_item_rate",
        value=value,
        unit="items_per_s",
        status=RATE_ENGINE_CONSTANT,
        inputs=(
            (
                f"{belt}.belt_speed={_fmt(speed.tiles_per_tick)} tiles_per_tick "
                f"[{speed.belt_speed_status}]"
            ),
            f"ticks_per_second={_fmt(TICKS_PER_SECOND)} [catalog constant]",
            f"belt_lanes={BELT_LANES} [engine constant]",
            (
                f"belt_item_spacing_tiles={_fmt(BELT_ITEM_SPACING_TILES)} "
                "[engine constant]"
            ),
        ),
        formula="belt_speed * ticks_per_second * lanes / item_spacing",
    )


def drill_output_rate(
    catalog: RuntimeFactorioCatalog,
    drill: str,
    ore_item: str,
) -> DerivedRate:
    """Ore per second one drill yields: mining speed over resource mining time."""
    speed = catalog.machine_speed(drill)
    if not speed.mining_speed_measured or speed.mining_speed is None:
        return _indeterminate(
            "drill_output",
            "items_per_s",
            f"{drill}.mining_speed is {speed.mining_speed_status}, not measured",
            item=ore_item,
        )
    mining_time, status = resource_mining_time(catalog, ore_item)
    speed_source = (
        f"{drill}.mining_speed={_fmt(speed.mining_speed)} "
        f"[{speed.mining_speed_status}]"
    )
    if mining_time is None:
        return _indeterminate(
            "drill_output",
            "items_per_s",
            f"{ore_item}.mining_time is {status}, not measured",
            item=ore_item,
            inputs=(speed_source,),
        )
    return DerivedRate(
        name="drill_output",
        value=speed.mining_speed / mining_time,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=(
            speed_source,
            f"{ore_item}.mining_time={_fmt(mining_time)} [{status}]",
        ),
        formula="mining_speed / mining_time",
        item=ore_item,
    )


def furnace_throughput(
    catalog: RuntimeFactorioCatalog,
    machine: str,
    item: str,
) -> MachineThroughput:
    """Per-second output and per-ingredient draw of one machine on one recipe.

    Named for its first use; it holds for any crafting machine, which is why
    the assembly cell calls it for the assembler as well.
    """
    speed = catalog.machine_speed(machine)
    choice = catalog.recipe_choice(item)
    if choice is None:
        note = f"no recipe in the catalog produces {item}"
        return MachineThroughput(
            machine=machine,
            item=item,
            crafts=_indeterminate("crafts_per_s", "crafts_per_s", note),
            output=_indeterminate("output_rate", "items_per_s", note, item=item),
        )
    crafting_time, time_status = recipe_time(catalog, choice.recipe_name)
    if not speed.crafting_speed_measured or speed.crafting_speed is None:
        note = f"{machine}.crafting_speed is {speed.crafting_speed_status}, not measured"
    elif crafting_time is None:
        note = f"{choice.recipe_name}.energy is {time_status}, not measured"
    else:
        note = None
    if note is not None:
        return MachineThroughput(
            machine=machine,
            item=item,
            crafts=_indeterminate("crafts_per_s", "crafts_per_s", note),
            output=_indeterminate("output_rate", "items_per_s", note, item=item),
            consumption={
                ingredient.item: _indeterminate(
                    "ingredient_draw",
                    "items_per_s",
                    note,
                    item=ingredient.item,
                )
                for ingredient in choice.spec.ingredients
            },
        )

    sources = (
        (
            f"{machine}.crafting_speed={_fmt(speed.crafting_speed)} "
            f"[{speed.crafting_speed_status}]"
        ),
        f"{choice.recipe_name}.energy={_fmt(crafting_time)} [{time_status}]",
    )
    crafts_per_s = speed.crafting_speed / crafting_time
    crafts = DerivedRate(
        name="crafts_per_s",
        value=crafts_per_s,
        unit="crafts_per_s",
        status=RATE_MEASURED,
        inputs=sources,
        formula="crafting_speed / recipe_energy",
    )
    output = DerivedRate(
        name="output_rate",
        value=crafts_per_s * choice.product_amount,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=sources
        + (f"{choice.recipe_name}.product_amount={_fmt(choice.product_amount)}",),
        formula="crafts_per_s * product_amount",
        item=item,
    )
    consumption = {
        ingredient.item: DerivedRate(
            name="ingredient_draw",
            value=crafts_per_s * ingredient.count,
            unit="items_per_s",
            status=RATE_MEASURED,
            inputs=sources
            + (
                (
                    f"{choice.recipe_name}.ingredient[{ingredient.item}]"
                    f"={_fmt(ingredient.count)}"
                ),
            ),
            formula="crafts_per_s * ingredient_count",
            item=ingredient.item,
        )
        for ingredient in choice.spec.ingredients
    }
    return MachineThroughput(
        machine=machine,
        item=item,
        crafts=crafts,
        output=output,
        consumption=consumption,
    )


# --------------------------------------------------------------------------
# Geometry: a local layout flowing east, rotated and translated into place
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalEntity:
    """One entity in local tile coordinates, before rotation."""

    name: str
    left: int
    top: int
    width: int
    height: int
    direction: int


def _quarter_turns(orientation: int) -> int:
    return ((orientation - _BASE_FLOW) // 4) % 4


def _rotate(x: float, y: float, turns: int) -> tuple[float, float]:
    for _ in range(turns):
        x, y = -y, x
    return x + 0.0, y + 0.0


def _to_placements(
    local: Sequence[_LocalEntity],
    *,
    orientation: int,
    origin: GridPoint,
) -> tuple[Placement, ...]:
    turns = _quarter_turns(orientation)
    placed: list[Placement] = []
    for entity in local:
        centre_x = entity.left + entity.width / 2
        centre_y = entity.top + entity.height / 2
        rotated_x, rotated_y = _rotate(centre_x, centre_y, turns)
        placed.append(
            Placement(
                name=entity.name,
                x=rotated_x + origin.x,
                y=rotated_y + origin.y,
                direction=(entity.direction + 4 * turns) % 16,
            )
        )
    return tuple(placed)


def _square_footprint(
    name: str,
    footprints: Mapping[str, tuple[int, int]] | None,
) -> tuple[int, int] | None:
    width, height = entity_footprint({"name": name, "direction": 0}, footprints)
    if width != height:
        return None
    return width, height


def _overlaps(
    placements: Sequence[Placement],
    footprints: Mapping[str, tuple[int, int]] | None,
) -> bool:
    occupied: set[GridPoint] = set()
    for index, placement in enumerate(placements):
        tiles = entity_tiles(placement.to_entity(index + 1), footprints)
        if tiles & occupied:
            return True
        occupied |= tiles
    return False


def _bounding_box(
    placements: Sequence[Placement],
    footprints: Mapping[str, tuple[int, int]] | None,
    margin: int,
) -> tuple[GridPoint, GridPoint]:
    tiles: set[GridPoint] = set()
    for index, placement in enumerate(placements):
        tiles |= entity_tiles(placement.to_entity(index + 1), footprints)
    min_x = min(tile.x for tile in tiles) - margin
    min_y = min(tile.y for tile in tiles) - margin
    max_x = max(tile.x for tile in tiles) + margin
    max_y = max(tile.y for tile in tiles) + margin
    return GridPoint(min_x, min_y), GridPoint(max_x, max_y)


def _refuse(
    provenance: PatternProvenance,
    refusal: str,
    detail: str,
) -> PatternOutcome:
    return PatternOutcome(
        pattern=provenance.pattern,
        provenance=provenance,
        block=None,
        refusal=refusal,
        refusal_detail=detail,
    )


def _unit_footprints_ok(
    names: Sequence[str],
    footprints: Mapping[str, tuple[int, int]] | None,
) -> str | None:
    for name in names:
        width, height = entity_footprint({"name": name, "direction": 0}, footprints)
        if (width, height) != (1, 1):
            return f"{name} occupies {width}x{height} tiles, the layout assumes 1x1"
    return None


def _category_refusal(
    catalog: RuntimeFactorioCatalog,
    machine: str,
    recipe_category: str,
) -> tuple[str | None, str | None]:
    """``(refusal detail, unmeasured note)`` for a machine/recipe pairing."""
    categories = machine_categories(catalog, machine)
    if categories is None:
        return None, (
            f"{machine} reports no crafting category, so its ability to run "
            f"{recipe_category!r} recipes was not checked"
        )
    if recipe_category not in categories:
        return (
            f"{machine} runs {sorted(categories)}, the recipe needs "
            f"{recipe_category!r}"
        ), None
    return None, None


# --------------------------------------------------------------------------
# Pattern: smelting line
# --------------------------------------------------------------------------


def smelting_line(
    catalog: RuntimeFactorioCatalog,
    params: SmeltingLineParams | None = None,
    *,
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> PatternOutcome:
    """Drill into a belt, into N furnaces, out onto a second belt.

    N is whatever the rates say: the ore one drill yields divided by the ore
    one furnace eats, capped by what the output belt can carry away and by
    ``max_furnaces``. Nothing about N is written down here.

    Layout, drawn flowing east and then rotated by ``orientation``::

        row 0      input belt, ore, flowing along the axis
        row 1      one inserter per furnace, dropping south
        rows 2..   the furnace bank
        row 2+h    one inserter per furnace, dropping south
        row 3+h    output belt, plates, flowing along the axis

    The drill sits at the head of the input belt, facing it.
    """
    params = params or SmeltingLineParams()
    provenance = PatternProvenance(
        pattern=PATTERN_SMELTING_LINE,
        parameters=_parameters(params),
    )

    if params.orientation not in CARDINALS:
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            f"orientation {params.orientation} is not one of {list(CARDINALS)}",
        )
    if params.belt_tail < 0 or params.route_margin < 0:
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            "belt_tail and route_margin cannot be negative",
        )
    if params.max_furnaces is not None and params.max_furnaces < 1:
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            "max_furnaces has to be at least 1 when set",
        )

    choice = catalog.recipe_choice(params.plate_item)
    if choice is None:
        return _refuse(
            provenance,
            REFUSAL_NO_RECIPE,
            f"no recipe in the catalog produces {params.plate_item}",
        )
    ore_ingredient = next(
        (
            ingredient
            for ingredient in choice.spec.ingredients
            if ingredient.item == params.ore_item
        ),
        None,
    )
    if ore_ingredient is None:
        return _refuse(
            provenance,
            REFUSAL_INGREDIENT_MISMATCH,
            f"recipe {choice.recipe_name} does not take {params.ore_item}",
        )

    drill_rate = drill_output_rate(catalog, params.drill, params.ore_item)
    throughput = furnace_throughput(catalog, params.furnace, params.plate_item)
    belt_rate = belt_item_rate(catalog, params.belt)
    ore_draw = throughput.consumption.get(params.ore_item)
    provenance = provenance.with_rates(
        drill_rate, throughput.output, belt_rate
    ).with_assumptions(
        f"belt item rate uses the engine constants "
        f"lanes={BELT_LANES} and item_spacing={_fmt(BELT_ITEM_SPACING_TILES)}",
        "furnaces are fed and drained by one inserter each, whose own "
        "throughput is not exported by the probe",
    )
    if ore_draw is not None:
        provenance = provenance.with_rates(ore_draw)

    for rate in (drill_rate, throughput.output, belt_rate):
        if not rate.determined:
            return _refuse(provenance, REFUSAL_RATE_INDETERMINATE, rate.note or rate.name)
    if ore_draw is None or not ore_draw.determined:
        detail = ore_draw.note if ore_draw is not None else (
            f"recipe {choice.recipe_name} declares no draw of {params.ore_item}"
        )
        return _refuse(provenance, REFUSAL_RATE_INDETERMINATE, detail or "")

    unmeasured: list[str] = [
        _INSERTER_CONSTRAINT,
        _POWER_CONSTRAINT,
        _RESOURCE_PATCH_CONSTRAINT,
    ]
    detail, note = _category_refusal(catalog, params.furnace, choice.spec.category)
    if detail is not None:
        return _refuse(provenance, REFUSAL_CATEGORY_MISMATCH, detail)
    if note is not None:
        unmeasured.append(note)

    furnace_tiles = _square_footprint(params.furnace, footprints)
    drill_tiles = _square_footprint(params.drill, footprints)
    if furnace_tiles is None or drill_tiles is None:
        offender = params.furnace if furnace_tiles is None else params.drill
        return _refuse(
            provenance,
            REFUSAL_NON_SQUARE_MACHINE,
            f"{offender} has a non-square footprint, which this layout cannot rotate",
        )
    unit_detail = _unit_footprints_ok((params.belt, params.inserter), footprints)
    if unit_detail is not None:
        return _refuse(provenance, REFUSAL_UNIT_FOOTPRINT_EXPECTED, unit_detail)

    # Sizing. Every term below is one of the derived rates above.
    delivered_ore = min(drill_rate.value, belt_rate.value)
    needed = math.ceil(delivered_ore / ore_draw.value)
    belt_cap = math.floor(belt_rate.value / throughput.output.value)
    if belt_cap < 1:
        return _refuse(
            provenance,
            REFUSAL_NO_CAPACITY,
            f"{params.belt} carries {belt_rate.value} items/s, below the "
            f"{throughput.output.value} items/s one {params.furnace} emits",
        )
    budget = params.max_furnaces if params.max_furnaces is not None else needed
    count = max(1, min(needed, belt_cap, budget))

    if count == budget < min(needed, belt_cap):
        limiting = LIMIT_BUDGET
    elif count == belt_cap < needed:
        limiting = LIMIT_OUTPUT_BELT
    elif belt_rate.value < drill_rate.value:
        limiting = LIMIT_INPUT_BELT
    else:
        limiting = LIMIT_DRILL

    furnace_width, furnace_height = furnace_tiles
    drill_width, drill_height = drill_tiles
    stride = furnace_width + params.furnace_spacing
    lefts = [index * stride for index in range(count)]
    span_end = max(left + furnace_width - 1 for left in lefts)
    belt_end = span_end + params.belt_tail
    out_inserter_row = 2 + furnace_height
    out_belt_row = out_inserter_row + 1

    local: list[_LocalEntity] = [
        _LocalEntity(
            name=params.drill,
            left=-drill_width,
            top=-((drill_height - 1) // 2),
            width=drill_width,
            height=drill_height,
            direction=DIRECTION_EAST,
        )
    ]
    for column in range(belt_end + 1):
        local.append(
            _LocalEntity(params.belt, column, 0, 1, 1, DIRECTION_EAST)
        )
        local.append(
            _LocalEntity(params.belt, column, out_belt_row, 1, 1, DIRECTION_EAST)
        )
    for left in lefts:
        local.append(_LocalEntity(params.inserter, left, 1, 1, 1, DIRECTION_SOUTH))
        local.append(
            _LocalEntity(
                params.furnace,
                left,
                2,
                furnace_width,
                furnace_height,
                DIRECTION_NORTH,
            )
        )
        local.append(
            _LocalEntity(
                params.inserter, left, out_inserter_row, 1, 1, DIRECTION_SOUTH
            )
        )

    placements = _to_placements(
        local, orientation=params.orientation, origin=params.origin
    )
    if _overlaps(placements, footprints):
        return _refuse(
            provenance,
            REFUSAL_OVERLAP,
            f"furnace_spacing={params.furnace_spacing} makes entities share tiles",
        )

    saturating = DerivedRate(
        name="block_input_saturation",
        value=count * ore_draw.value,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=ore_draw.inputs + (f"furnace_count={count}",),
        formula="furnace_count * ingredient_draw",
        item=params.ore_item,
    )
    sustained = DerivedRate(
        name="block_output",
        value=count * throughput.output.value,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=throughput.output.inputs + (f"furnace_count={count}",),
        formula="furnace_count * output_rate",
        item=params.plate_item,
    )
    provenance = provenance.with_rates(
        DerivedRate(
            name="furnaces_per_drill",
            value=delivered_ore / ore_draw.value,
            unit="furnaces",
            status=RATE_MEASURED,
            inputs=drill_rate.inputs + ore_draw.inputs,
            formula="min(drill_output, belt_item_rate) / ingredient_draw",
        ),
        saturating,
        sustained,
    )
    block = ProductionBlock(
        pattern=PATTERN_SMELTING_LINE,
        placements=placements,
        provenance=provenance,
        input_saturation=(saturating,),
        output=(sustained,),
        limiting_factor=limiting,
        bounding_box=_bounding_box(placements, footprints, params.route_margin),
        unmeasured_constraints=tuple(unmeasured),
    )
    return PatternOutcome(
        pattern=PATTERN_SMELTING_LINE, provenance=provenance, block=block
    )


# --------------------------------------------------------------------------
# Pattern: assembly cell
# --------------------------------------------------------------------------


def assembly_cell(
    catalog: RuntimeFactorioCatalog,
    params: AssemblyCellParams | None = None,
    *,
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> PatternOutcome:
    """A furnace bank on a belt, an inserter, an assembler, a product belt.

    The furnace count is the assembler's ingredient draw divided by one
    furnace's output, both derived from crafting speed and recipe time. When
    the bank cannot cover the draw, the declared output falls with it instead
    of reporting the assembler's nominal rate.

    Layout, drawn flowing east and then rotated by ``orientation``::

        row 0      ingredient belt, then the feeding inserter, the assembler,
                   the unloading inserter and the product belt
        row 1      one inserter per furnace, dropping onto the belt
        rows 2..   the furnace bank
    """
    params = params or AssemblyCellParams()
    provenance = PatternProvenance(
        pattern=PATTERN_ASSEMBLY_CELL,
        parameters=_parameters(params),
    )

    if params.orientation not in CARDINALS:
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            f"orientation {params.orientation} is not one of {list(CARDINALS)}",
        )
    if (
        params.input_belt_tail < 0
        or params.output_belt_length < 1
        or params.route_margin < 0
    ):
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            "input_belt_tail and route_margin cannot be negative and the "
            "output belt needs at least one tile",
        )
    if params.max_furnaces is not None and params.max_furnaces < 1:
        return _refuse(
            provenance,
            REFUSAL_PARAMETER_OUT_OF_RANGE,
            "max_furnaces has to be at least 1 when set",
        )

    choice = catalog.recipe_choice(params.target_item)
    if choice is None:
        return _refuse(
            provenance,
            REFUSAL_NO_RECIPE,
            f"no recipe in the catalog produces {params.target_item}",
        )
    if len(choice.spec.ingredients) != 1:
        return _refuse(
            provenance,
            REFUSAL_MULTI_INGREDIENT,
            f"recipe {choice.recipe_name} takes "
            f"{len(choice.spec.ingredients)} ingredients; this cell feeds one",
        )
    ingredient = choice.spec.ingredients[0]

    assembler = furnace_throughput(catalog, params.assembler, params.target_item)
    furnace = furnace_throughput(catalog, params.furnace, ingredient.item)
    belt_rate = belt_item_rate(catalog, params.belt)
    draw = assembler.consumption.get(ingredient.item)
    provenance = provenance.with_rates(
        assembler.output, furnace.output, belt_rate
    ).with_assumptions(
        f"belt item rate uses the engine constants "
        f"lanes={BELT_LANES} and item_spacing={_fmt(BELT_ITEM_SPACING_TILES)}",
        "one inserter feeds the assembler, whose own throughput is not "
        "exported by the probe",
    )
    if draw is not None:
        provenance = provenance.with_rates(draw)

    for rate in (assembler.output, furnace.output, belt_rate):
        if not rate.determined:
            return _refuse(provenance, REFUSAL_RATE_INDETERMINATE, rate.note or rate.name)
    if draw is None or not draw.determined:
        detail = draw.note if draw is not None else (
            f"recipe {choice.recipe_name} declares no draw of {ingredient.item}"
        )
        return _refuse(provenance, REFUSAL_RATE_INDETERMINATE, detail or "")

    unmeasured: list[str] = [_INSERTER_CONSTRAINT, _POWER_CONSTRAINT]
    detail, note = _category_refusal(catalog, params.assembler, choice.spec.category)
    if detail is not None:
        return _refuse(provenance, REFUSAL_CATEGORY_MISMATCH, detail)
    if note is not None:
        unmeasured.append(note)
    ingredient_choice = catalog.recipe_choice(ingredient.item)
    if ingredient_choice is not None:
        detail, note = _category_refusal(
            catalog, params.furnace, ingredient_choice.spec.category
        )
        if detail is not None:
            return _refuse(provenance, REFUSAL_CATEGORY_MISMATCH, detail)
        if note is not None:
            unmeasured.append(note)

    furnace_tiles = _square_footprint(params.furnace, footprints)
    assembler_tiles = _square_footprint(params.assembler, footprints)
    if furnace_tiles is None or assembler_tiles is None:
        offender = params.furnace if furnace_tiles is None else params.assembler
        return _refuse(
            provenance,
            REFUSAL_NON_SQUARE_MACHINE,
            f"{offender} has a non-square footprint, which this layout cannot rotate",
        )
    unit_detail = _unit_footprints_ok((params.belt, params.inserter), footprints)
    if unit_detail is not None:
        return _refuse(provenance, REFUSAL_UNIT_FOOTPRINT_EXPECTED, unit_detail)

    # Sizing.
    ratio = draw.value / furnace.output.value
    needed = math.ceil(ratio)
    belt_cap = math.floor(belt_rate.value / furnace.output.value)
    if belt_cap < 1:
        return _refuse(
            provenance,
            REFUSAL_NO_CAPACITY,
            f"{params.belt} carries {belt_rate.value} items/s, below the "
            f"{furnace.output.value} items/s one {params.furnace} emits",
        )
    budget = params.max_furnaces if params.max_furnaces is not None else needed
    count = max(1, min(needed, belt_cap, budget))

    if count == budget < min(needed, belt_cap):
        limiting = LIMIT_BUDGET
    elif count == belt_cap < needed:
        limiting = LIMIT_INPUT_BELT
    else:
        limiting = LIMIT_INGREDIENT_DEMAND

    supplied = count * furnace.output.value
    effective_crafts = min(assembler.crafts.value, supplied / ingredient.count)

    furnace_width, furnace_height = furnace_tiles
    assembler_width, assembler_height = assembler_tiles
    stride = furnace_width + params.furnace_spacing
    lefts = [index * stride for index in range(count)]
    span_end = max(left + furnace_width - 1 for left in lefts)
    belt_end = span_end + params.input_belt_tail
    feed_column = belt_end + 1
    assembler_left = feed_column + 1
    unload_column = assembler_left + assembler_width
    output_start = unload_column + 1

    local: list[_LocalEntity] = []
    for column in range(belt_end + 1):
        local.append(_LocalEntity(params.belt, column, 0, 1, 1, DIRECTION_EAST))
    for left in lefts:
        local.append(_LocalEntity(params.inserter, left, 1, 1, 1, DIRECTION_NORTH))
        local.append(
            _LocalEntity(
                params.furnace,
                left,
                2,
                furnace_width,
                furnace_height,
                DIRECTION_NORTH,
            )
        )
    local.append(_LocalEntity(params.inserter, feed_column, 0, 1, 1, DIRECTION_EAST))
    local.append(
        _LocalEntity(
            params.assembler,
            assembler_left,
            -((assembler_height - 1) // 2),
            assembler_width,
            assembler_height,
            DIRECTION_NORTH,
        )
    )
    local.append(_LocalEntity(params.inserter, unload_column, 0, 1, 1, DIRECTION_EAST))
    for offset in range(params.output_belt_length):
        local.append(
            _LocalEntity(params.belt, output_start + offset, 0, 1, 1, DIRECTION_EAST)
        )

    placements = _to_placements(
        local, orientation=params.orientation, origin=params.origin
    )
    if _overlaps(placements, footprints):
        return _refuse(
            provenance,
            REFUSAL_OVERLAP,
            f"furnace_spacing={params.furnace_spacing} makes entities share tiles",
        )

    saturating = DerivedRate(
        name="block_input_saturation",
        value=draw.value,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=draw.inputs,
        formula="crafts_per_s * ingredient_count",
        item=ingredient.item,
    )
    sustained = DerivedRate(
        name="block_output",
        value=effective_crafts * choice.product_amount,
        unit="items_per_s",
        status=RATE_MEASURED,
        inputs=assembler.output.inputs
        + furnace.output.inputs
        + (f"furnace_count={count}",),
        formula=(
            "min(crafts_per_s, furnace_count * furnace_output / ingredient_count)"
            " * product_amount"
        ),
        item=params.target_item,
    )
    provenance = provenance.with_rates(
        DerivedRate(
            name="furnaces_per_assembler",
            value=ratio,
            unit="furnaces",
            status=RATE_MEASURED,
            inputs=draw.inputs + furnace.output.inputs,
            formula="ingredient_draw / furnace_output",
        ),
        saturating,
        sustained,
    )
    block = ProductionBlock(
        pattern=PATTERN_ASSEMBLY_CELL,
        placements=placements,
        provenance=provenance,
        input_saturation=(saturating,),
        output=(sustained,),
        limiting_factor=limiting,
        bounding_box=_bounding_box(placements, footprints, params.route_margin),
        unmeasured_constraints=tuple(unmeasured),
    )
    return PatternOutcome(
        pattern=PATTERN_ASSEMBLY_CELL, provenance=provenance, block=block
    )
