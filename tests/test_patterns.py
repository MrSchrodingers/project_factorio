"""Community layout patterns as a separable, auditable seed.

Every number a pattern uses has to come from a runtime measurement that
carries a ``measured`` status. The fixture below is a payload shaped exactly
like the one ``dashboard/state.py`` emits, carrying the figures read from the
live game on 2026-09-23 and quoted in the task: ``stone-furnace``
crafting_speed 1.0, ``assembling-machine-2`` crafting_speed 0.75,
``electric-mining-drill`` mining_speed 0.5, ``transport-belt`` belt_speed
0.03125 tiles/tick.

``iron-ore`` mining_time is the one figure the live probe does not export
today, so it is present here only to exercise the determined branch; the
payload without a ``resources`` section is the live case, and the smelting
line refuses there.
"""

import math

import pytest

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.planning.footprints import entity_tiles, prototype_footprints
from factorio_ai_lab.planning.patterns import (
    BELT_ITEM_SPACING_TILES,
    BELT_LANES,
    PATTERN_ASSEMBLY_CELL,
    PATTERN_SMELTING_LINE,
    RATE_ENGINE_CONSTANT,
    RATE_INDETERMINATE,
    RATE_MEASURED,
    REFUSAL_CATEGORY_MISMATCH,
    REFUSAL_MULTI_INGREDIENT,
    REFUSAL_NON_SQUARE_MACHINE,
    REFUSAL_OVERLAP,
    REFUSAL_PARAMETER_OUT_OF_RANGE,
    REFUSAL_RATE_INDETERMINATE,
    AssemblyCellParams,
    SmeltingLineParams,
    assembly_cell,
    belt_item_rate,
    furnace_throughput,
    smelting_line,
)
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

TICKS_PER_SECOND = 60.0

FOOTPRINT_PAYLOAD = {
    "connected": True,
    "prototypes": [
        {"name": "assembling-machine-1", "tile_width": 3, "tile_height": 3},
        {"name": "assembling-machine-2", "tile_width": 3, "tile_height": 3},
        {"name": "boiler", "tile_width": 3, "tile_height": 2},
        {"name": "burner-mining-drill", "tile_width": 2, "tile_height": 2},
        {"name": "electric-mining-drill", "tile_width": 3, "tile_height": 3},
        {"name": "inserter", "tile_width": 1, "tile_height": 1},
        {"name": "stone-furnace", "tile_width": 2, "tile_height": 2},
        {"name": "transport-belt", "tile_width": 1, "tile_height": 1},
    ],
}
FOOTPRINTS = prototype_footprints(FOOTPRINT_PAYLOAD)

RECIPES = [
    {
        "name": "iron-plate",
        "energy": 3.2,
        "categories": ["smelting"],
        "ingredients": [{"name": "iron-ore", "type": "item", "amount": 1}],
        "products": [{"name": "iron-plate", "type": "item", "amount": 1}],
        "enabled": True,
    },
    {
        "name": "iron-gear-wheel",
        "energy": 0.5,
        "categories": ["crafting"],
        "ingredients": [{"name": "iron-plate", "type": "item", "amount": 2}],
        "products": [{"name": "iron-gear-wheel", "type": "item", "amount": 1}],
        "enabled": True,
    },
    {
        "name": "automation-science-pack",
        "energy": 5.0,
        "categories": ["crafting"],
        "ingredients": [
            {"name": "iron-gear-wheel", "type": "item", "amount": 1},
            {"name": "copper-plate", "type": "item", "amount": 1},
        ],
        "products": [
            {"name": "automation-science-pack", "type": "item", "amount": 1}
        ],
        "enabled": True,
    },
]

MACHINES = [
    {
        "name": "assembling-machine-1",
        "type": "assembling-machine",
        "crafting_categories": ["crafting"],
        # A value that travelled with a status that does not vouch for it.
        "crafting_speed": 0.5,
        "crafting_speed_status": "probe_failed",
        "resource_categories": [],
        "mining_speed": None,
        "mining_speed_status": "absent",
    },
    {
        "name": "assembling-machine-2",
        "type": "assembling-machine",
        "crafting_categories": ["crafting"],
        "crafting_speed": 0.75,
        "crafting_speed_status": "measured",
        "resource_categories": [],
        "mining_speed": None,
        "mining_speed_status": "absent",
    },
    {
        "name": "stone-furnace",
        "type": "furnace",
        "crafting_categories": ["smelting"],
        "crafting_speed": 1.0,
        "crafting_speed_status": "measured",
        "resource_categories": [],
        "mining_speed": None,
        "mining_speed_status": "absent",
    },
    {
        "name": "electric-mining-drill",
        "type": "mining-drill",
        "crafting_categories": [],
        "crafting_speed": None,
        "crafting_speed_status": "absent",
        "resource_categories": ["basic-solid"],
        "mining_speed": 0.5,
        "mining_speed_status": "measured",
    },
    {
        "name": "boiler",
        "type": "boiler",
        "crafting_categories": ["smelting"],
        "crafting_speed": 1.0,
        "crafting_speed_status": "measured",
        "resource_categories": [],
        "mining_speed": None,
        "mining_speed_status": "absent",
    },
]

BELTS = [
    {
        "name": "transport-belt",
        "type": "transport-belt",
        "belt_speed": 0.03125,
        "belt_speed_status": "measured",
        "belt_speed_unit": "tiles_per_tick",
        "max_underground_distance": None,
        "max_underground_distance_status": "absent",
    }
]

RESOURCES = [
    {"name": "iron-ore", "mining_time": 1.0, "mining_time_status": "measured"}
]

PAYLOAD = {
    "connected": True,
    "factorio_version": "2.0.73",
    "recipes": RECIPES,
    "technologies": [],
    "machines": MACHINES,
    "belts": BELTS,
    "resources": RESOURCES,
}

# The live payload: same rows, no resource section at all.
LIVE_SHAPED_PAYLOAD = {key: value for key, value in PAYLOAD.items() if key != "resources"}


@pytest.fixture()
def catalog():
    return RuntimeFactorioCatalog(PAYLOAD)


@pytest.fixture()
def live_catalog():
    return RuntimeFactorioCatalog(LIVE_SHAPED_PAYLOAD)


def placed_tiles(block):
    """Tiles each placement occupies, by real footprint, as a list per entity."""
    return [
        entity_tiles(placement.to_entity(index + 1), FOOTPRINTS)
        for index, placement in enumerate(block.placements)
    ]


def named(block, name):
    return [placement for placement in block.placements if placement.name == name]


# --------------------------------------------------------------------------
# Derived rates: the hand computation is written out, not only its result.
# --------------------------------------------------------------------------


def test_belt_item_rate_derives_from_the_measured_tiles_per_tick(catalog):
    rate = belt_item_rate(catalog, "transport-belt")
    # 0.03125 tiles/tick * 60 ticks/s = 1.875 tiles/s.
    # 1.875 tiles/s * 2 lanes / 0.25 tiles per item = 15.0 items/s.
    expected = 0.03125 * TICKS_PER_SECOND * BELT_LANES / BELT_ITEM_SPACING_TILES
    assert expected == 15.0
    assert rate.value == pytest.approx(expected)
    # Lanes and item spacing are engine geometry the runtime does not report,
    # so the rate may not claim to be purely measured.
    assert rate.status == RATE_ENGINE_CONSTANT
    assert any("0.03125" in source for source in rate.inputs)


def test_belt_item_rate_is_indeterminate_when_the_belt_was_never_probed(catalog):
    rate = belt_item_rate(catalog, "fast-transport-belt")
    assert rate.value is None
    assert rate.status == RATE_INDETERMINATE
    assert "fast-transport-belt" in (rate.note or "")


def test_furnace_throughput_derives_from_speed_and_recipe_time(catalog):
    throughput = furnace_throughput(catalog, "stone-furnace", "iron-plate")
    # crafting_speed 1.0 / recipe energy 3.2 s = 0.3125 crafts/s.
    # 0.3125 crafts/s * 1 product per craft = 0.3125 plate/s.
    # 0.3125 crafts/s * 1 ore per craft    = 0.3125 ore/s consumed.
    assert 1.0 / 3.2 == 0.3125
    assert throughput.output.value == pytest.approx(0.3125)
    assert throughput.output.status == RATE_MEASURED
    assert throughput.consumption["iron-ore"].value == pytest.approx(0.3125)


def test_furnace_throughput_refuses_a_speed_whose_status_is_not_measured(catalog):
    # assembling-machine-1 carries 0.5 in the payload under probe_failed.
    throughput = furnace_throughput(catalog, "assembling-machine-1", "iron-gear-wheel")
    assert throughput.output.value is None
    assert throughput.output.status == RATE_INDETERMINATE
    assert "crafting_speed" in (throughput.output.note or "")


# --------------------------------------------------------------------------
# Smelting line
# --------------------------------------------------------------------------


def test_smelting_line_furnace_count_matches_the_hand_computation(catalog):
    outcome = smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS)
    assert outcome.produced, outcome.refusal_detail
    block = outcome.block

    # drill: mining_speed 0.5 / iron-ore mining_time 1.0 = 0.5 ore/s.
    drill_rate = 0.5 / 1.0
    # furnace: 1.0 / 3.2 = 0.3125 crafts/s, 1 ore each -> 0.3125 ore/s.
    furnace_ore = 1.0 / 3.2
    # belt carries 15.0 items/s, far above one drill, so the drill binds.
    belt_rate = 0.03125 * TICKS_PER_SECOND * BELT_LANES / BELT_ITEM_SPACING_TILES
    delivered = min(drill_rate, belt_rate)
    expected_count = math.ceil(delivered / furnace_ore)
    assert delivered / furnace_ore == pytest.approx(1.6)
    assert expected_count == 2

    assert len(named(block, "stone-furnace")) == expected_count
    assert block.limiting_factor == "drill"


def test_smelting_line_declares_saturation_consistent_with_the_rates(catalog):
    outcome = smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS)
    block = outcome.block
    furnace_count = len(named(block, "stone-furnace"))

    saturating_ore = {rate.item: rate for rate in block.input_saturation}["iron-ore"]
    sustained_plate = {rate.item: rate for rate in block.output}["iron-plate"]

    # 2 furnaces * 0.3125 ore/s each = 0.625 ore/s saturates the block.
    assert saturating_ore.value == pytest.approx(furnace_count * (1.0 / 3.2))
    assert saturating_ore.value == pytest.approx(0.625)
    # Same count * 0.3125 plate/s each = 0.625 plate/s sustained.
    assert sustained_plate.value == pytest.approx(0.625)


def test_smelting_line_refuses_when_the_ore_mining_time_was_never_measured(live_catalog):
    outcome = smelting_line(live_catalog, SmeltingLineParams(), footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.block is None
    assert outcome.refusal == REFUSAL_RATE_INDETERMINATE
    assert "iron-ore" in outcome.refusal_detail
    assert "mining_time" in outcome.refusal_detail
    # The refusal still carries provenance, so the caller can see what was read.
    assert outcome.provenance.pattern == PATTERN_SMELTING_LINE
    assert outcome.provenance.rate("drill_output") is not None
    assert outcome.provenance.rate("drill_output").status == RATE_INDETERMINATE


def test_smelting_line_refuses_an_unmeasured_furnace_speed(catalog):
    params = SmeltingLineParams(furnace="assembling-machine-1")
    outcome = smelting_line(catalog, params, footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_RATE_INDETERMINATE
    assert "crafting_speed" in outcome.refusal_detail


def test_smelting_line_entities_never_overlap(catalog):
    for orientation in (0, 4, 8, 12):
        params = SmeltingLineParams(
            orientation=orientation,
            origin=GridPoint(11, -7),
            furnace_spacing=1,
        )
        outcome = smelting_line(catalog, params, footprints=FOOTPRINTS)
        assert outcome.produced, outcome.refusal_detail
        per_entity = placed_tiles(outcome.block)
        union = set().union(*per_entity)
        assert sum(len(tiles) for tiles in per_entity) == len(union)


def test_smelting_line_refuses_a_spacing_that_would_stack_furnaces(catalog):
    params = SmeltingLineParams(furnace_spacing=-1)
    outcome = smelting_line(catalog, params, footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_OVERLAP


def test_smelting_line_parameters_change_the_layout_not_the_ratio(catalog):
    base = smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS)
    spaced = smelting_line(
        catalog, SmeltingLineParams(furnace_spacing=3), footprints=FOOTPRINTS
    )
    turned = smelting_line(
        catalog, SmeltingLineParams(orientation=8), footprints=FOOTPRINTS
    )
    moved = smelting_line(
        catalog, SmeltingLineParams(origin=GridPoint(40, 40)), footprints=FOOTPRINTS
    )

    layouts = [
        frozenset(outcome.block.placements)
        for outcome in (base, spaced, turned, moved)
    ]
    assert len({layout for layout in layouts}) == 4
    counts = {len(named(outcome.block, "stone-furnace")) for outcome in
              (base, spaced, turned, moved)}
    assert counts == {2}


def test_smelting_line_orientation_turns_every_direction(catalog):
    east = smelting_line(catalog, SmeltingLineParams(orientation=4), footprints=FOOTPRINTS)
    south = smelting_line(catalog, SmeltingLineParams(orientation=8), footprints=FOOTPRINTS)
    east_belts = {placement.direction for placement in named(east.block, "transport-belt")}
    south_belts = {
        placement.direction for placement in named(south.block, "transport-belt")
    }
    assert east_belts == {4}
    assert south_belts == {8}
    east_inserters = {placement.direction for placement in named(east.block, "inserter")}
    south_inserters = {placement.direction for placement in named(south.block, "inserter")}
    assert east_inserters == {8}
    assert south_inserters == {12}


def test_smelting_line_route_margin_widens_the_reserved_box_only(catalog):
    tight = smelting_line(catalog, SmeltingLineParams(route_margin=0), footprints=FOOTPRINTS)
    loose = smelting_line(catalog, SmeltingLineParams(route_margin=3), footprints=FOOTPRINTS)
    assert tight.block.placements == loose.block.placements
    (tight_min, tight_max) = tight.block.bounding_box
    (loose_min, loose_max) = loose.block.bounding_box
    assert loose_min.x == tight_min.x - 3
    assert loose_max.y == tight_max.y + 3


def test_smelting_line_provenance_names_pattern_parameters_and_sources(catalog):
    params = SmeltingLineParams(furnace_spacing=2, orientation=12)
    outcome = smelting_line(catalog, params, footprints=FOOTPRINTS)
    provenance = outcome.block.provenance
    assert provenance.pattern == PATTERN_SMELTING_LINE
    recorded = dict(provenance.parameters)
    assert recorded["furnace_spacing"] == 2
    assert recorded["orientation"] == 12
    assert recorded["furnace"] == "stone-furnace"
    drill_rate = provenance.rate("drill_output")
    assert drill_rate.value == pytest.approx(0.5)
    assert any("mining_speed=0.5" in source for source in drill_rate.inputs)
    assert any("mining_time=1.0" in source for source in drill_rate.inputs)
    assert provenance.assumptions


def test_smelting_line_refuses_a_non_square_machine(catalog):
    params = SmeltingLineParams(furnace="boiler")
    outcome = smelting_line(catalog, params, footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_NON_SQUARE_MACHINE


def test_smelting_line_refuses_an_orientation_off_the_cardinals(catalog):
    outcome = smelting_line(
        catalog, SmeltingLineParams(orientation=2), footprints=FOOTPRINTS
    )
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_PARAMETER_OUT_OF_RANGE


# --------------------------------------------------------------------------
# Assembly cell
# --------------------------------------------------------------------------


def test_assembly_cell_furnace_count_matches_the_hand_computation(catalog):
    params = AssemblyCellParams(assembler="assembling-machine-2")
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    assert outcome.produced, outcome.refusal_detail
    block = outcome.block

    # assembler: crafting_speed 0.75 / recipe energy 0.5 s = 1.5 crafts/s.
    crafts_per_s = 0.75 / 0.5
    assert crafts_per_s == 1.5
    # each craft eats 2 iron-plate -> 3.0 plate/s demanded.
    demand = crafts_per_s * 2
    assert demand == 3.0
    # each furnace supplies 1.0 / 3.2 = 0.3125 plate/s.
    supply = 1.0 / 3.2
    # 3.0 / 0.3125 = 9.6 -> 10 furnaces.
    assert demand / supply == pytest.approx(9.6)
    assert math.ceil(demand / supply) == 10

    assert len(named(block, "stone-furnace")) == 10
    assert len(named(block, "assembling-machine-2")) == 1
    assert block.limiting_factor == "ingredient_demand"


def test_assembly_cell_output_and_saturation_follow_the_rates(catalog):
    params = AssemblyCellParams(assembler="assembling-machine-2")
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    block = outcome.block
    saturating = {rate.item: rate for rate in block.input_saturation}["iron-plate"]
    produced = {rate.item: rate for rate in block.output}["iron-gear-wheel"]
    # 1.5 crafts/s * 2 plate each = 3.0 plate/s saturates the assembler.
    assert saturating.value == pytest.approx(3.0)
    # 10 furnaces * 0.3125 = 3.125 plate/s supplied, above the 3.0 demanded,
    # so the assembler runs at its own 1.5 craft/s and yields 1.5 gear/s.
    assert produced.value == pytest.approx(1.5)


def test_assembly_cell_budget_limits_the_count_and_lowers_the_output(catalog):
    params = AssemblyCellParams(assembler="assembling-machine-2", max_furnaces=3)
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    block = outcome.block
    assert len(named(block, "stone-furnace")) == 3
    assert block.limiting_factor == "furnace_budget"
    # 3 furnaces * 0.3125 = 0.9375 plate/s, 2 plate per craft ->
    # 0.46875 crafts/s -> 0.46875 gear/s.
    produced = {rate.item: rate for rate in block.output}["iron-gear-wheel"]
    assert produced.value == pytest.approx(0.46875)


def test_assembly_cell_refuses_an_assembler_speed_that_was_not_measured(catalog):
    # The default assembler is assembling-machine-1, whose 0.5 arrived under
    # probe_failed. A pattern that used it would be inventing the ratio.
    outcome = assembly_cell(catalog, AssemblyCellParams(), footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_RATE_INDETERMINATE
    assert "assembling-machine-1" in outcome.refusal_detail
    assert "crafting_speed" in outcome.refusal_detail


def test_assembly_cell_refuses_a_recipe_with_more_than_one_ingredient(catalog):
    params = AssemblyCellParams(
        target_item="automation-science-pack", assembler="assembling-machine-2"
    )
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_MULTI_INGREDIENT


def test_assembly_cell_refuses_a_machine_that_cannot_run_the_category(catalog):
    params = AssemblyCellParams(assembler="stone-furnace")
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    assert not outcome.produced
    assert outcome.refusal == REFUSAL_CATEGORY_MISMATCH


def test_assembly_cell_entities_never_overlap(catalog):
    for orientation in (0, 4, 8, 12):
        for spacing in (0, 2):
            params = AssemblyCellParams(
                assembler="assembling-machine-2",
                orientation=orientation,
                furnace_spacing=spacing,
                origin=GridPoint(-5, 13),
            )
            outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
            assert outcome.produced, outcome.refusal_detail
            per_entity = placed_tiles(outcome.block)
            union = set().union(*per_entity)
            assert sum(len(tiles) for tiles in per_entity) == len(union)


def test_assembly_cell_parameters_change_the_layout(catalog):
    base = AssemblyCellParams(assembler="assembling-machine-2")
    variants = [
        base,
        AssemblyCellParams(assembler="assembling-machine-2", furnace_spacing=1),
        AssemblyCellParams(assembler="assembling-machine-2", orientation=0),
        AssemblyCellParams(assembler="assembling-machine-2", output_belt_length=6),
        AssemblyCellParams(assembler="assembling-machine-2", input_belt_tail=4),
    ]
    layouts = {
        frozenset(assembly_cell(catalog, params, footprints=FOOTPRINTS).block.placements)
        for params in variants
    }
    assert len(layouts) == len(variants)


def test_assembly_cell_provenance_is_present_and_carries_the_ratio(catalog):
    params = AssemblyCellParams(assembler="assembling-machine-2", furnace_spacing=2)
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    provenance = outcome.block.provenance
    assert provenance.pattern == PATTERN_ASSEMBLY_CELL
    assert dict(provenance.parameters)["furnace_spacing"] == 2
    ratio = provenance.rate("furnaces_per_assembler")
    assert ratio.value == pytest.approx(9.6)
    assert any("crafting_speed=0.75" in source for source in ratio.inputs)
    assert any("energy=0.5" in source for source in ratio.inputs)


def test_assembly_cell_reports_the_constraints_it_could_not_measure(catalog):
    params = AssemblyCellParams(assembler="assembling-machine-2")
    outcome = assembly_cell(catalog, params, footprints=FOOTPRINTS)
    constraints = outcome.block.unmeasured_constraints
    # The probe exports no inserter throughput, so the plant is optimistic in
    # a way the caller has to know about before trusting it.
    assert any("inserter" in entry for entry in constraints)


# --------------------------------------------------------------------------
# Cross-cutting guards
# --------------------------------------------------------------------------


def test_serialised_outcomes_never_carry_the_failure_substring(catalog):
    produced = [
        smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS),
        assembly_cell(
            catalog,
            AssemblyCellParams(assembler="assembling-machine-2"),
            footprints=FOOTPRINTS,
        ),
        assembly_cell(catalog, AssemblyCellParams(), footprints=FOOTPRINTS),
        smelting_line(
            RuntimeFactorioCatalog(LIVE_SHAPED_PAYLOAD),
            SmeltingLineParams(),
            footprints=FOOTPRINTS,
        ),
    ]
    for outcome in produced:
        text = repr(outcome.as_dict())
        assert "error" not in text.lower()


def test_blocks_are_hashable_and_comparable_for_the_evolution_loop(catalog):
    first = smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS).block
    second = smelting_line(catalog, SmeltingLineParams(), footprints=FOOTPRINTS).block
    assert first.placements == second.placements
    assert hash(first.provenance) == hash(second.provenance)
