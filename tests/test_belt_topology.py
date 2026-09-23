"""Conveyor topology beyond a straight belt: splitters and underground pairs.

The factory graph is the instrument the selection reads. A conveyor it cannot
see is a chain it reports as broken, so an agent that builds a bus would be
measured as a disconnected factory and removed for it. These cases pin the
three shapes a belt network takes that a single line does not cover: the
tunnel that skips tiles, the fork that has two outputs, and the unpaired
tunnel end that carries nothing at all.
"""

from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.planning.rebuild import (
    REASON_DETOUR_ROUTE,
    REJECT_UNREPLACEABLE_TRANSPORT,
    STOP_PROPOSAL_REJECTED,
    identify_ruin_neighborhoods,
    plan_rebuild,
    ruin_and_recreate,
)


def entity(name, x, y, *, direction=0):
    """Snapshot row without a unit number, so ids read as `i<index>`."""
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        "status": "working",
    }


def relations(graph):
    return {
        (edge["source"], edge["target"], edge["relation"])
        for edge in graph["edges"]
    }


def flow(graph):
    return {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
        if edge["relation"] == "belt_flow"
    }


NORTH = 0
EAST = 4
SOUTH = 8
WEST = 12


def test_control_plain_belt_chain_is_three_edges():
    """Positive control: the instrument does report a straight chain."""
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=EAST),
            entity("transport-belt", 1.5, 0, direction=EAST),
            entity("transport-belt", 2.5, 0, direction=EAST),
            entity("transport-belt", 3.5, 0, direction=EAST),
            entity("stone-furnace", 5.5, 0),
        ]
    )
    assert relations(graph) == {
        ("i0", "i1", "material_output"),
        ("i1", "i2", "belt_flow"),
        ("i2", "i3", "belt_flow"),
    }


def test_underground_pair_carries_the_chain_over_the_gap():
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=EAST),
            entity("transport-belt", 1.5, 0, direction=EAST),
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("underground-belt", 6.5, 0, direction=EAST),
            entity("transport-belt", 7.5, 0, direction=EAST),
        ]
    )
    assert relations(graph) == {
        ("i0", "i1", "material_output"),
        ("i1", "i2", "belt_flow"),
        ("i2", "i3", "belt_flow"),
        ("i3", "i4", "belt_flow"),
    }


def test_splitter_carries_the_chain():
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=EAST),
            entity("transport-belt", 1.5, 0.5, direction=EAST),
            entity("splitter", 2.5, 0.0, direction=EAST),
            entity("transport-belt", 3.5, 0.5, direction=EAST),
        ]
    )
    assert relations(graph) == {
        ("i0", "i1", "material_output"),
        ("i1", "i2", "belt_flow"),
        ("i2", "i3", "belt_flow"),
    }


def test_splitter_feeds_both_of_its_outputs():
    """The reason a splitter exists: one input, two outputs."""
    graph = build_factory_graph(
        [
            entity("transport-belt", 1.5, 0.5, direction=EAST),
            entity("splitter", 2.5, 0.0, direction=EAST),
            entity("transport-belt", 3.5, 0.5, direction=EAST),
            entity("transport-belt", 3.5, -0.5, direction=EAST),
        ]
    )
    assert flow(graph) == {("i0", "i1"), ("i1", "i2"), ("i1", "i3")}


def test_producer_reaches_processor_through_a_splitter():
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=EAST),
            entity("transport-belt", 1.5, 0.5, direction=EAST),
            entity("splitter", 2.5, 0.0, direction=EAST),
            entity("transport-belt", 3.5, 0.5, direction=EAST),
            entity("inserter", 4.5, 0.5, direction=EAST),
            entity("stone-furnace", 5.65, 0.5),
        ]
    )
    assert graph["metrics"]["producers_reaching_processor"] == 1
    assert graph["metrics"]["physical_processing_coverage"] == 1.0


def test_producer_reaches_processor_through_an_underground_pair():
    graph = build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=EAST),
            entity("transport-belt", 1.5, 0, direction=EAST),
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("underground-belt", 6.5, 0, direction=EAST),
            entity("transport-belt", 7.5, 0, direction=EAST),
            entity("inserter", 8.5, 0, direction=EAST),
            entity("stone-furnace", 9.65, 0),
        ]
    )
    assert graph["metrics"]["producers_reaching_processor"] == 1


def test_splitter_and_underground_are_transport_entities():
    graph = build_factory_graph(
        [
            entity("splitter", 2.5, 0.0, direction=EAST),
            entity("fast-splitter", 12.5, 0.0, direction=EAST),
            entity("express-splitter", 22.5, 0.0, direction=EAST),
            entity("underground-belt", 32.5, 0.5, direction=EAST),
            entity("fast-underground-belt", 42.5, 0.5, direction=EAST),
            entity("express-underground-belt", 52.5, 0.5, direction=EAST),
        ]
    )
    assert graph["metrics"]["categories"] == {"transport": 6}


def test_unpaired_underground_end_carries_nothing_forward():
    """An underground end with no partner swallows the line; it is not a belt."""
    graph = build_factory_graph(
        [
            entity("transport-belt", 1.5, 0, direction=EAST),
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("transport-belt", 3.5, 0, direction=EAST),
        ]
    )
    assert flow(graph) == {("i0", "i1")}


def test_underground_pair_beyond_the_prototype_distance_is_not_linked():
    """`underground-belt` spans 5 tiles; `fast-underground-belt` spans 7."""
    far = [
        entity("underground-belt", 2.5, 0, direction=EAST),
        entity("underground-belt", 9.5, 0, direction=EAST),
    ]
    assert flow(build_factory_graph(far)) == set()

    faster = [
        entity("fast-underground-belt", 2.5, 0, direction=EAST),
        entity("fast-underground-belt", 9.5, 0, direction=EAST),
    ]
    assert flow(build_factory_graph(faster)) == {("i0", "i1")}


def test_underground_ends_of_different_tiers_do_not_pair():
    graph = build_factory_graph(
        [
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("fast-underground-belt", 5.5, 0, direction=EAST),
        ]
    )
    assert flow(graph) == set()


def test_underground_ends_pair_only_along_their_own_direction():
    crossed = build_factory_graph(
        [
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("underground-belt", 5.5, 0, direction=SOUTH),
        ]
    )
    assert flow(crossed) == set()

    off_axis = build_factory_graph(
        [
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("underground-belt", 5.5, 1, direction=EAST),
        ]
    )
    assert flow(off_axis) == set()

    behind = build_factory_graph(
        [
            entity("underground-belt", 5.5, 0, direction=EAST),
            entity("underground-belt", 2.5, 0, direction=EAST),
        ]
    )
    assert flow(behind) == {("i1", "i0")}


def test_underground_pairs_with_the_nearest_end_in_front():
    graph = build_factory_graph(
        [
            entity("underground-belt", 2.5, 0, direction=EAST),
            entity("underground-belt", 4.5, 0, direction=EAST),
            entity("underground-belt", 6.5, 0, direction=EAST),
        ]
    )
    assert flow(graph) == {("i0", "i1")}


def test_splitter_is_not_fed_from_a_side_it_cannot_accept():
    """A splitter takes items at its two rear tiles, not from its flanks."""
    graph = build_factory_graph(
        [
            entity("transport-belt", 2.5, 1.5, direction=0),
            entity("splitter", 2.5, 0.0, direction=EAST),
        ]
    )
    assert flow(graph) == set()


def unit_entity(name, x, y, *, direction=0, unit=1):
    """Snapshot row with a unit number, so ids read as `u<unit>`."""
    row = entity(name, x, y, direction=direction)
    row["unit_number"] = unit
    return row


def forked_belt_world():
    """One feed, a splitter, and two branches that double back on the feed."""
    return [
        unit_entity("transport-belt", 0.5, -0.5, direction=EAST, unit=1),
        unit_entity("transport-belt", 1.5, -0.5, direction=EAST, unit=2),
        unit_entity("transport-belt", 2.5, -0.5, direction=EAST, unit=3),
        unit_entity("splitter", 3.5, 0.0, direction=EAST, unit=4),
        unit_entity("transport-belt", 4.5, -0.5, direction=NORTH, unit=5),
        unit_entity("transport-belt", 4.5, -1.5, direction=WEST, unit=6),
        unit_entity("transport-belt", 3.5, -1.5, direction=WEST, unit=7),
        unit_entity("transport-belt", 2.5, -1.5, direction=WEST, unit=8),
        unit_entity("transport-belt", 1.5, -1.5, direction=WEST, unit=9),
        unit_entity("transport-belt", 0.5, -1.5, direction=WEST, unit=10),
        unit_entity("transport-belt", 4.5, 0.5, direction=SOUTH, unit=11),
        unit_entity("transport-belt", 4.5, 1.5, direction=WEST, unit=12),
        unit_entity("transport-belt", 3.5, 1.5, direction=WEST, unit=13),
        unit_entity("transport-belt", 2.5, 1.5, direction=WEST, unit=14),
        unit_entity("transport-belt", 1.5, 1.5, direction=WEST, unit=15),
        unit_entity("transport-belt", 0.5, 1.5, direction=WEST, unit=16),
    ]


def test_the_splitter_bifurcation_is_in_the_graph():
    graph = build_factory_graph(forked_belt_world())
    assert flow(graph) >= {("u3", "u4"), ("u4", "u5"), ("u4", "u11")}


def test_a_forked_line_is_never_priced_as_one_run():
    """Walking one branch would report the length of a line nobody built."""
    graph = build_factory_graph(forked_belt_world())
    neighborhoods = identify_ruin_neighborhoods(graph)

    assert REASON_DETOUR_ROUTE not in {row.reason for row in neighborhoods}
    removed = {
        candidate.node_id
        for row in neighborhoods
        for candidate in row.removals
    }
    assert "u4" not in removed


def dead_end_world(middle):
    """A drill whose output reaches nothing, through `middle`."""
    return [
        unit_entity("burner-mining-drill", 0, 0, direction=EAST, unit=1),
        unit_entity("transport-belt", 1.5, 0.5, direction=EAST, unit=2),
        *middle,
        unit_entity("transport-belt", 8.5, 0.5, direction=EAST, unit=6),
        unit_entity("stone-furnace", 20.5, 0.5, unit=7),
    ]


def test_a_splitter_is_refused_by_name_not_priced_as_a_belt():
    world = dead_end_world(
        [
            unit_entity("splitter", 2.5, 0.0, direction=EAST, unit=3),
            unit_entity("transport-belt", 3.5, 0.5, direction=EAST, unit=4),
        ]
    )
    graph = build_factory_graph(world)
    assert graph["metrics"]["producers_reaching_processor"] == 0
    assert "u3" in {
        candidate.node_id
        for row in identify_ruin_neighborhoods(graph)
        for candidate in row.removals
    }

    proposal = plan_rebuild(world)
    assert proposal is not None
    assert proposal.accepted is False
    assert proposal.rejection == REJECT_UNREPLACEABLE_TRANSPORT
    assert proposal.removals == ()

    result = ruin_and_recreate(world)
    assert result.stop_reason == STOP_PROPOSAL_REJECTED
    assert len(result.entities) == len(world)


def test_an_underground_pair_is_refused_by_name_too():
    world = dead_end_world(
        [
            unit_entity("underground-belt", 2.5, 0.5, direction=EAST, unit=3),
            unit_entity("underground-belt", 6.5, 0.5, direction=EAST, unit=4),
            unit_entity("transport-belt", 7.5, 0.5, direction=EAST, unit=5),
        ]
    )
    proposal = plan_rebuild(world)
    assert proposal is not None
    assert proposal.accepted is False
    assert proposal.rejection == REJECT_UNREPLACEABLE_TRANSPORT
    assert proposal.removals == ()


def test_a_fork_in_the_flow_ends_the_run_it_cannot_price():
    """A node with two successors ends a run; following one is a wrong number.

    The payload is built and then forked by hand because the graph builder
    forks at splitters only, and a splitter is already refused by name one
    layer up. What is pinned here is the contract of the payload: given a flow
    that branches, the detour operator never reports the length of the single
    line it happened to walk.
    """
    world = [
        unit_entity("transport-belt", 0.5, 0.5, direction=EAST, unit=1),
        unit_entity("transport-belt", 1.5, 0.5, direction=EAST, unit=2),
        unit_entity("transport-belt", 2.5, 0.5, direction=EAST, unit=3),
        unit_entity("transport-belt", 1.5, 1.5, direction=WEST, unit=4),
        unit_entity("transport-belt", 0.5, 1.5, direction=WEST, unit=5),
    ]
    graph = build_factory_graph(world)
    assert flow(graph) == {("u1", "u2"), ("u2", "u3"), ("u4", "u5")}

    forked = dict(graph)
    forked["edges"] = [
        *graph["edges"],
        {"source": "u2", "target": "u4", "relation": "belt_flow"},
    ]
    assert identify_ruin_neighborhoods(forked) == ()
