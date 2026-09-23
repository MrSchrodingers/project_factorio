from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.factory_graph import (
    DIRECTION_VECTORS,
    MATERIAL_RELATIONS,
    build_factory_graph,
    node_id,
)
from factorio_ai_lab.planning.rebuild import (
    REASON_DETOUR_ROUTE,
    REASON_ORPHAN_ENTITY,
    REASON_UNCONNECTED_PRODUCER,
    REGRESSION_CAPACITY,
    REGRESSION_PARTIAL_APPLICATION,
    REGRESSION_UNMEASURED,
    REJECT_GAIN_BELOW_COST,
    STOP_NO_CANDIDATES,
    STOP_ROUND_BUDGET,
    FactoryCapacity,
    RebuildCostModel,
    RuinPolicy,
    apply_proposal,
    detect_regression,
    identify_ruin_neighborhoods,
    measure_capacity,
    plan_rebuild,
    ruin_and_recreate,
)


def entity(name, x, y, *, direction=0, unit):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        "unit_number": unit,
        "status": "working",
    }


def dangling_drill_world():
    """Drill facing a stray belt that leads nowhere; furnace 6 tiles south."""
    return [
        entity("burner-mining-drill", 0, 0, direction=8, unit=1),
        entity("transport-belt", -0.5, 1.5, direction=4, unit=2),
        entity("stone-furnace", 0, 6, unit=3),
    ]


def two_drill_world():
    return [
        entity("burner-mining-drill", 0, 0, direction=8, unit=1),
        entity("transport-belt", -0.5, 1.5, direction=4, unit=2),
        entity("stone-furnace", 0, 6, unit=3),
        entity("burner-mining-drill", 8, 0, direction=8, unit=4),
        entity("transport-belt", 7.5, 1.5, direction=4, unit=5),
        entity("stone-furnace", 8, 6, unit=6),
    ]


def _belt_run(tiles, tail_direction, first_unit):
    """Belts over ``tiles`` in order, each pointing at the next tile."""
    vector_to_direction = {
        (int(v[0]), int(v[1])): key for key, v in DIRECTION_VECTORS.items()
    }
    rows = []
    for index, tile in enumerate(tiles):
        if index + 1 < len(tiles):
            nxt = tiles[index + 1]
            direction = vector_to_direction[(nxt[0] - tile[0], nxt[1] - tile[1])]
        else:
            direction = tail_direction
        rows.append(
            entity(
                "transport-belt",
                tile[0] + 0.5,
                tile[1] + 0.5,
                direction=direction,
                unit=first_unit + index,
            )
        )
    return rows


def detour_world():
    """Connected drill whose belt takes a 17-tile path where 9 would do."""
    tiles = (
        [(-1, 1), (0, 1), (1, 1), (2, 1), (3, 1)]
        + [(3, y) for y in range(2, 10)]
        + [(2, 9), (1, 9), (0, 9), (-1, 9)]
    )
    return [
        entity("burner-mining-drill", 0, 0, direction=8, unit=1),
        *_belt_run(tiles, tail_direction=8, first_unit=10),
        # Faces north, at the belt it lifts from: an inserter's direction
        # is its pickup side. It drops south, into the furnace.
        entity("inserter", -0.5, 10.5, direction=0, unit=90),
        entity("stone-furnace", 0, 12, unit=91),
    ]


def test_factory_graph_exposes_shared_vocabulary():
    assert MATERIAL_RELATIONS == frozenset(
        {"belt_flow", "pickup", "drop", "material_output"}
    )
    assert set(DIRECTION_VECTORS) == {0, 4, 8, 12}
    assert node_id({"unit_number": 7}, 3) == "u7"
    assert node_id({}, 3) == "i3"


def test_unconnected_producer_is_a_ruin_neighborhood():
    graph = build_factory_graph(dangling_drill_world())
    assert graph["metrics"]["producers_reaching_processor"] == 0

    neighborhoods = identify_ruin_neighborhoods(graph)
    reasons = {row.reason for row in neighborhoods}
    assert REASON_UNCONNECTED_PRODUCER in reasons

    target = next(
        row for row in neighborhoods if row.reason == REASON_UNCONNECTED_PRODUCER
    )
    assert {row.node_id for row in target.removals} == {"u2"}


def test_extraction_and_processing_are_never_demolished():
    graph = build_factory_graph(dangling_drill_world())
    removed = {
        candidate.node_id
        for row in identify_ruin_neighborhoods(graph)
        for candidate in row.removals
    }
    assert "u1" not in removed
    assert "u3" not in removed


def test_orphan_belt_is_identified():
    graph = build_factory_graph([entity("transport-belt", 20.5, 20.5, unit=1)])
    neighborhoods = identify_ruin_neighborhoods(graph)
    assert [row.reason for row in neighborhoods] == [REASON_ORPHAN_ENTITY]
    assert [row.node_id for row in neighborhoods[0].removals] == ["u1"]


def test_rebuild_connects_producer_to_processor():
    world = dangling_drill_world()
    proposal = plan_rebuild(world)
    assert proposal is not None
    assert proposal.accepted, proposal.rejection
    assert proposal.baseline.connected_producers == 0
    assert proposal.projected.connected_producers == 1
    assert proposal.routes
    assert {row.name for row in proposal.placements} == {
        "transport-belt",
        "inserter",
    }
    assert proposal.estimated_gain > proposal.estimated_cost

    rebuilt = build_factory_graph(apply_proposal(world, proposal))
    assert measure_capacity(rebuilt).connected_producers == 1


def test_proposal_is_refused_when_gain_does_not_cover_cost():
    proposal = plan_rebuild(
        dangling_drill_world(),
        cost_model=RebuildCostModel(connected_producer_value=1.0),
    )
    assert proposal is not None
    assert not proposal.accepted
    assert proposal.rejection == REJECT_GAIN_BELOW_COST


def test_capacity_guard_refuses_to_trade_producers_for_coverage():
    before = FactoryCapacity(
        connected_producers=1,
        buffered_producers=0,
        producer_count=3,
        processing_nodes=1,
        material_edges=4,
    )
    # Removing the two unconnected drills lifts the coverage ratio from 1/3 to
    # 1/1 while the factory produces exactly as much as before.
    after = FactoryCapacity(
        connected_producers=1,
        buffered_producers=0,
        producer_count=1,
        processing_nodes=1,
        material_edges=4,
    )
    assert after.coverage > before.coverage
    assert after.regressions_against(before)


def test_detour_run_is_shortened_without_losing_capacity():
    world = detour_world()
    graph = build_factory_graph(world)
    assert graph["metrics"]["producers_reaching_processor"] == 1

    neighborhoods = identify_ruin_neighborhoods(graph)
    assert REASON_DETOUR_ROUTE in {row.reason for row in neighborhoods}

    proposal = plan_rebuild(
        world,
        cost_model=RebuildCostModel(saved_tile_value=10.0, downtime_value_per_s=0.1),
    )
    assert proposal is not None
    assert proposal.reason == REASON_DETOUR_ROUTE
    assert proposal.accepted, proposal.rejection
    assert len(proposal.placements) < len(proposal.removals)
    assert proposal.projected.connected_producers == 1


def test_detect_regression_reports_partial_application():
    world = dangling_drill_world()
    proposal = plan_rebuild(world)
    assert proposal is not None

    demolished_only = [
        row
        for index, row in enumerate(world)
        if node_id(row, index) not in {row.node_id for row in proposal.removals}
    ]
    observed = build_factory_graph(demolished_only)
    # Capacity alone stays silent: it was already zero before the attempt.
    assert observed["metrics"]["producers_reaching_processor"] == 0
    findings = detect_regression(proposal, observed)
    assert any(row.startswith(REGRESSION_PARTIAL_APPLICATION) for row in findings)
    assert not any(row.startswith(REGRESSION_CAPACITY) for row in findings)


def test_detect_regression_refuses_to_certify_an_unmeasured_snapshot():
    proposal = plan_rebuild(dangling_drill_world())
    assert proposal is not None
    assert detect_regression(proposal, {}) == (REGRESSION_UNMEASURED,)


def test_loop_stops_and_never_ruins_the_same_entity_twice():
    result = ruin_and_recreate(two_drill_world(), policy=RuinPolicy(max_rounds=4))
    assert result.initial.connected_producers == 0
    assert result.final.connected_producers == 2
    assert len(result.rounds) == 2
    assert all(row.applied for row in result.rounds)
    assert result.stop_reason == STOP_NO_CANDIDATES

    demolished = [
        candidate.node_id
        for row in result.rounds
        for candidate in row.proposal.removals
    ]
    assert demolished
    assert len(demolished) == len(set(demolished))


def test_round_budget_stops_the_loop_with_work_still_on_the_table():
    result = ruin_and_recreate(two_drill_world(), policy=RuinPolicy(max_rounds=1))
    assert len(result.rounds) == 1
    assert result.stop_reason == STOP_ROUND_BUDGET
    # One drill is still unconnected, so the loop stopped because it was told
    # to and not because it ran out of candidates.
    assert result.final.connected_producers == 1
    assert result.final.producer_count == 2


def test_tabu_removes_an_entity_from_every_destroy_operator():
    graph = build_factory_graph(dangling_drill_world())
    free = identify_ruin_neighborhoods(graph)
    assert any(
        candidate.node_id == "u2"
        for row in free
        for candidate in row.removals
    )

    held = identify_ruin_neighborhoods(graph, tabu={"u2"})
    assert not any(
        candidate.node_id == "u2"
        for row in held
        for candidate in row.removals
    )


def test_front_tile_feeds_the_route_from_the_drill_output():
    proposal = plan_rebuild(dangling_drill_world())
    assert proposal is not None
    assert proposal.routes[0].start == GridPoint(-1, 1)
