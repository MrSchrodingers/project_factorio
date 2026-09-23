"""The repair loop has to read what was measured and refuse the rest.

Every test here is behavioural: it asserts what the loop decides from a
payload, never the shape of the code that decides it. Three tests in this
repository once asserted the wrong belt rotation in the abstract syntax tree
and passed while the game dropped coal on the ground, so a test that cannot
fail against wrong behaviour is not evidence here.
"""

from __future__ import annotations

from factorio_ai_lab.learning.factory_graph import (
    STALL_CAUSE_INGREDIENTS,
    STALL_CAUSE_POWER,
    build_factory_graph,
)
from factorio_ai_lab.learning.repair_loop import (
    CAUSE_CHAIN_REACHES_NO_SINK,
    DEFICIT_DEAD_OUTPUT_CHAIN,
    DEFICIT_FUEL_STARVED,
    DEFICIT_ISOLATED_PRODUCER,
    DEFICIT_MACHINE_STALLED,
    DEFICIT_OUTPUT_UNPROCESSED,
    DEFICIT_POWER_STARVED,
    DEFICIT_STEAM_PATH_DEAD,
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    INTENT_ATTACH_TO_LIVE_NETWORK,
    INTENT_EXTEND_POWER_SUPPLY,
    INTENT_PLACE_PROCESSING,
    METRIC_FUEL_STARVED,
    METRIC_PRODUCERS_PROCESSED,
    PREDICTION_HELD,
    PREDICTION_MISSED,
    PREDICTION_UNMEASURED,
    RANK_AFTER_PROVIDER,
    RANK_CYCLE_CUT,
    RANK_NO_PREREQUISITE,
    REFUSAL_CAUSE_UNDETERMINED,
    REFUSAL_LONGER_WINDOW_NEEDED,
    REFUSAL_NO_KNOWN_ACTION,
    REFUSAL_POWER_TOPOLOGY_UNREAD,
    REFUSAL_SUPPLY_NETWORK_DEAD,
    RESOURCE_FUEL,
    RESOURCE_MATERIAL,
    RESOURCE_POWER,
    SOURCE_GRAPH_METRICS,
    SOURCE_GRAPH_NODES,
    TOOL_DEPENDENCY_PLAN,
    TOOL_PLACEMENT,
    TOOL_REBUILD,
    TOOL_RESUPPLY,
    Deficit,
    Diagnosis,
    MachineReport,
    Prediction,
    RepairAction,
    RepairObservation,
    RepairProposal,
    detect_deficits,
    diagnose,
    evaluate_prediction,
    machine_reports_from_diagnostics,
    order_steps,
    plan_repairs,
    produces_into_live_chain,
    propose_actions,
    record_repair,
    select_action,
    symptom_key,
)


def entity(name, x, y, *, direction=0, unit=1, status="working"):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "direction": direction,
        "unit_number": unit,
        "status": status,
    }


def node(identifier, category, status, *, name="entity"):
    return {
        "id": identifier,
        "name": name,
        "category": category,
        "x": 0.0,
        "y": 0.0,
        "status": status,
    }


def observation(nodes=(), edges=(), metrics=None, reports=(), prefix=""):
    graph = {"nodes": list(nodes), "edges": list(edges), "metrics": dict(metrics or {})}
    return RepairObservation(
        graph=graph,
        machine_reports=tuple(reports),
        graph_metric_prefix=prefix,
    )


# -- detection --------------------------------------------------------------


def test_starved_nodes_are_grouped_by_the_category_that_decides_the_repair():
    deficits = detect_deficits(
        observation(
            nodes=[
                node("u1", "energy", "no_fuel", name="boiler"),
                node("u2", "extraction", "no_fuel", name="burner-mining-drill"),
                node("u3", "extraction", "no_fuel", name="burner-mining-drill"),
                node("u4", "extraction", "working", name="burner-mining-drill"),
            ],
        )
    )
    by_category = {row.category: row for row in deficits if row.kind == DEFICIT_FUEL_STARVED}
    assert set(by_category) == {"energy", "extraction"}
    assert by_category["energy"].entities == ("u1",)
    assert by_category["extraction"].entities == ("u2", "u3")
    assert by_category["extraction"].severity == 2.0
    assert by_category["energy"].source == SOURCE_GRAPH_NODES


def test_aggregate_counts_name_no_entity_and_say_so():
    deficits = detect_deficits(
        observation(
            nodes=(),
            metrics={
                "entity_status_observed": True,
                METRIC_FUEL_STARVED: 10,
                "power_starved_entities": 0,
            },
        )
    )
    fuel = [row for row in deficits if row.kind == DEFICIT_FUEL_STARVED]
    assert len(fuel) == 1
    assert fuel[0].entities is None
    assert fuel[0].severity == 10.0
    assert fuel[0].source == SOURCE_GRAPH_METRICS
    assert not [row for row in deficits if row.kind == DEFICIT_POWER_STARVED]


def test_starvation_counts_are_ignored_when_no_status_was_observed():
    # A non-zero count beside a status nobody took is not evidence of
    # starvation: the count has no provenance, and reading it would let a
    # stale or partially written report drive a repair.
    assert (
        detect_deficits(
            observation(
                nodes=(),
                metrics={"entity_status_observed": False, METRIC_FUEL_STARVED: 4},
            )
        )
        == ()
    )
    assert (
        detect_deficits(observation(nodes=(), metrics={METRIC_FUEL_STARVED: 4})) == ()
    )
    assert (
        detect_deficits(
            observation(
                nodes=(),
                metrics={"entity_status_observed": False, METRIC_FUEL_STARVED: 0},
            )
        )
        == ()
    )


def test_missing_metrics_produce_no_deficit_at_all():
    assert detect_deficits(observation(nodes=(), metrics={})) == ()


def test_steam_path_reported_dead_is_a_deficit_and_reported_live_is_not():
    dead = detect_deficits(observation(metrics={"steam_path_live": False}))
    assert [row.kind for row in dead] == [DEFICIT_STEAM_PATH_DEAD]
    assert detect_deficits(observation(metrics={"steam_path_live": True})) == ()


# -- the live-chain guard ---------------------------------------------------


def _drill_into_chest():
    return build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=8, unit=1),
            entity("wooden-chest", 0, 1.5, unit=2),
        ]
    )


def _drill_into_dead_belt():
    return build_factory_graph(
        [
            entity("burner-mining-drill", 0, 0, direction=8, unit=1),
            entity("transport-belt", 0, 1.5, direction=8, unit=2),
        ]
    )


def test_producer_feeding_a_container_is_never_offered_for_demolition():
    graph = _drill_into_chest()
    assert produces_into_live_chain(graph, "u1") is True
    deficits = detect_deficits(RepairObservation(graph=graph))
    kinds = {row.kind for row in deficits}
    assert DEFICIT_DEAD_OUTPUT_CHAIN not in kinds
    assert DEFICIT_OUTPUT_UNPROCESSED in kinds
    observed = RepairObservation(graph=graph)
    for deficit in deficits:
        proposal = propose_actions(diagnose(deficit, observed), observed)
        for action in proposal.candidates:
            assert action.removes == ()


def test_producer_whose_chain_dies_is_offered_with_the_dead_tail_named():
    graph = _drill_into_dead_belt()
    assert produces_into_live_chain(graph, "u1") is False
    deficits = detect_deficits(RepairObservation(graph=graph))
    dead = [row for row in deficits if row.kind == DEFICIT_DEAD_OUTPUT_CHAIN]
    assert [row.entities for row in dead] == [("u1",)]
    observed = RepairObservation(graph=graph)
    proposal = propose_actions(diagnose(dead[0], observed), observed)
    assert proposal.diagnosis.cause == CAUSE_CHAIN_REACHES_NO_SINK
    assert proposal.candidates[0].tool == TOOL_REBUILD
    assert proposal.candidates[0].removes == ("u2",)
    for action in proposal.candidates:
        for removed in action.removes:
            assert produces_into_live_chain(graph, removed) is False


def test_live_chain_is_unanswered_when_no_edges_were_read():
    assert produces_into_live_chain({"nodes": [node("u1", "extraction", "working")]}, "u1") is None
    assert produces_into_live_chain({"nodes": [], "edges": []}, "u1") is None


def test_buffered_producer_asks_for_a_machine_before_it_asks_for_demolition():
    graph = _drill_into_chest()
    observed = RepairObservation(graph=graph)
    deficit = next(
        row for row in detect_deficits(observed) if row.kind == DEFICIT_OUTPUT_UNPROCESSED
    )
    proposal = propose_actions(diagnose(deficit, observed), observed)
    assert [action.tool for action in proposal.candidates] == [TOOL_PLACEMENT, TOOL_REBUILD]
    assert proposal.candidates[0].intent == INTENT_PLACE_PROCESSING
    assert proposal.candidates[0].prediction == Prediction(
        METRIC_PRODUCERS_PROCESSED, DIRECTION_INCREASE
    )


def test_producer_wired_to_nothing_is_isolated_not_dead_chain():
    graph = build_factory_graph([entity("burner-mining-drill", 0, 0, unit=1)])
    kinds = [row.kind for row in detect_deficits(RepairObservation(graph=graph))]
    assert DEFICIT_ISOLATED_PRODUCER in kinds
    assert DEFICIT_DEAD_OUTPUT_CHAIN not in kinds


# -- ordering ---------------------------------------------------------------


def _boiler_and_two_dead_assemblers():
    return observation(
        nodes=[
            node("u1", "energy", "no_fuel", name="boiler"),
            node("u2", "processing", "no_power", name="assembling-machine-1"),
            node("u3", "processing", "no_power", name="assembling-machine-1"),
        ],
        edges=[],
    )


def test_power_is_restored_before_the_machines_that_need_it():
    plan = plan_repairs(_boiler_and_two_dead_assemblers())
    kinds = [step.deficit.kind for step in plan.steps]
    assert kinds == [DEFICIT_FUEL_STARVED, DEFICIT_POWER_STARVED]
    # The order contradicts severity on purpose: two dead assemblers outweigh
    # one dry boiler, and repairing them first would buy nothing.
    assert plan.steps[0].deficit.severity == 1.0
    assert plan.steps[1].deficit.severity == 2.0
    assert plan.steps[0].rank == 0
    assert plan.steps[0].rank_basis == RANK_NO_PREREQUISITE
    assert plan.steps[1].rank == 1
    assert plan.steps[1].rank_basis == RANK_AFTER_PROVIDER
    assert RESOURCE_POWER in plan.steps[0].action.provides
    assert RESOURCE_POWER in plan.steps[1].action.requires


def test_severity_orders_only_inside_one_rank():
    plan = plan_repairs(
        observation(
            nodes=[
                node("u1", "transfer", "no_fuel", name="burner-inserter"),
                node("u2", "transfer", "no_fuel", name="burner-inserter"),
                node("u3", "processing", "no_fuel", name="stone-furnace"),
            ],
            edges=[],
        )
    )
    assert [step.deficit.category for step in plan.steps] == ["transfer", "processing"]
    assert [step.deficit.severity for step in plan.steps] == [2.0, 1.0]
    assert {step.rank for step in plan.steps} == {0}


def _pair(kind, severity, *, requires, provides, intent):
    deficit = Deficit(kind=kind, source=SOURCE_GRAPH_NODES, severity=severity)
    proposal = RepairProposal(
        diagnosis=Diagnosis(deficit=deficit, cause="c", basis="b"),
        candidates=(),
    )
    action = RepairAction(
        tool=TOOL_RESUPPLY,
        intent=intent,
        prediction=Prediction("m", DIRECTION_DECREASE),
        requires=requires,
        provides=provides,
    )
    return proposal, action


def test_mutually_dependent_repairs_are_cut_instead_of_looping():
    # A coal drill needs the power its own coal generates. No precedence can
    # order that pair, so the loop says so by name rather than iterating.
    steps = order_steps(
        [
            _pair(
                DEFICIT_FUEL_STARVED,
                1.0,
                requires=(RESOURCE_POWER,),
                provides=(RESOURCE_FUEL,),
                intent="a",
            ),
            _pair(
                DEFICIT_POWER_STARVED,
                1.0,
                requires=(RESOURCE_FUEL,),
                provides=(RESOURCE_POWER,),
                intent="b",
            ),
        ]
    )
    assert len(steps) == 2
    assert {step.rank_basis for step in steps} == {RANK_CYCLE_CUT}


def test_refuelling_a_drill_does_not_claim_to_restore_power():
    plan = plan_repairs(
        observation(
            nodes=[node("u1", "extraction", "no_fuel", name="burner-mining-drill")],
            edges=[],
        )
    )
    assert plan.steps[0].action.provides == (RESOURCE_MATERIAL,)


# -- machine stalls ---------------------------------------------------------


def _machine(**overrides):
    base = {
        "machine_id": "circuit_assembler",
        "status": "no_power",
        "recipe": "electronic-circuit",
        "input_count": 41.0,
        "output_count": 0.0,
        "network_id": -1.0,
        "supply_network_id": 7743.0,
        "output_metric": "circuit_assembler.output_after",
    }
    base.update(overrides)
    return MachineReport(**base)


def _machine_proposal(report):
    observed = observation(reports=[report])
    deficits = detect_deficits(observed)
    assert [row.kind for row in deficits] == [DEFICIT_MACHINE_STALLED]
    return propose_actions(diagnose(deficits[0], observed), observed)


def test_machine_off_every_network_beside_a_live_one_is_told_to_attach():
    proposal = _machine_proposal(_machine())
    assert proposal.diagnosis.cause == STALL_CAUSE_POWER
    action = proposal.candidates[0]
    assert action.tool == TOOL_PLACEMENT
    assert action.intent == INTENT_ATTACH_TO_LIVE_NETWORK
    assert action.requires == (RESOURCE_POWER,)
    assert action.prediction == Prediction(
        "circuit_assembler.output_after", DIRECTION_INCREASE
    )


def test_machine_off_every_network_with_no_live_one_read_extends_supply():
    proposal = _machine_proposal(_machine(supply_network_id=None))
    assert proposal.candidates[0].intent == INTENT_EXTEND_POWER_SUPPLY


def test_machine_already_wired_to_a_dead_grid_is_refused_by_name():
    proposal = _machine_proposal(_machine(network_id=7743.0))
    assert proposal.candidates == ()
    assert proposal.refusal == REFUSAL_SUPPLY_NETWORK_DEAD


def test_machine_whose_network_was_never_read_is_refused_by_name():
    proposal = _machine_proposal(_machine(network_id=None))
    assert proposal.refusal == REFUSAL_POWER_TOPOLOGY_UNREAD


def test_machine_short_of_ingredients_asks_the_dependency_planner():
    proposal = _machine_proposal(
        _machine(status="item_ingredient_shortage", network_id=7743.0, input_count=0.0)
    )
    assert proposal.diagnosis.cause == STALL_CAUSE_INGREDIENTS
    action = proposal.candidates[0]
    assert action.tool == TOOL_DEPENDENCY_PLAN
    assert action.arguments["target_item"] == "electronic-circuit"


def test_machine_with_no_recipe_is_a_deficit_with_no_known_action():
    proposal = _machine_proposal(_machine(status="no_recipe", recipe=""))
    assert proposal.candidates == ()
    assert proposal.refusal == REFUSAL_NO_KNOWN_ACTION


def test_working_machine_that_produced_nothing_asks_for_a_longer_window():
    proposal = _machine_proposal(_machine(status="working", network_id=7743.0))
    assert proposal.refusal == REFUSAL_LONGER_WINDOW_NEEDED


def test_unread_status_is_an_undetermined_cause_and_is_recorded_as_one():
    proposal = _machine_proposal(_machine(status=None, output_count=0.0))
    assert proposal.diagnosis.cause is None
    assert proposal.refusal == REFUSAL_CAUSE_UNDETERMINED
    assert symptom_key(proposal.diagnosis) == "machine_stalled:undetermined"


def test_producing_machine_raises_no_deficit():
    assert detect_deficits(observation(reports=[_machine(output_count=6.0)])) == ()


def test_starved_nodes_without_edges_refuse_the_power_repair():
    observed = RepairObservation(
        graph={"nodes": [node("u1", "processing", "no_power")], "metrics": {}}
    )
    plan = plan_repairs(observed)
    assert plan.steps == ()
    assert [row.refusal for row in plan.refusals] == [REFUSAL_POWER_TOPOLOGY_UNREAD]


# -- reading a diagnostics block --------------------------------------------


def test_diagnostics_block_yields_window_output_not_stock():
    block = {
        "circuit_assembler": {
            "recipe": "electronic-circuit",
            "status_before": "no_power",
            "status_after": "no_power",
            "output_before": 6.0,
            "output_after": 6.0,
            "inputs_after": {"copper_cable": 22.0, "iron_plate": 24.0},
            "network_id": -1.0,
        },
        "power": {"circuit_pole_network_id": 7743.0},
        "accepted": True,
    }
    reports = machine_reports_from_diagnostics(
        block, metric_prefix="electronic_circuit_diagnostics."
    )
    assert [row.machine_id for row in reports] == ["circuit_assembler"]
    report = reports[0]
    assert report.output_count == 0.0
    assert report.input_count == 46.0
    assert report.supply_network_id == 7743.0
    assert report.output_metric == (
        "electronic_circuit_diagnostics.circuit_assembler.output_after"
    )


def test_diagnostics_block_without_both_output_readings_measures_nothing():
    reports = machine_reports_from_diagnostics(
        {"cell": {"recipe": "copper-cable", "output_after": 22.0}}
    )
    assert reports[0].output_count is None
    assert reports[0].output_metric == "cell.output_after"


def test_absent_diagnostics_block_yields_no_machine():
    assert machine_reports_from_diagnostics(None) == ()


# -- prediction and scoring -------------------------------------------------


def test_prediction_holds_only_when_the_metric_actually_moved():
    prediction = Prediction("fuel_starved_entities", DIRECTION_DECREASE)
    held = evaluate_prediction(
        prediction, {"fuel_starved_entities": 10}, {"fuel_starved_entities": 3}
    )
    assert held.verdict == PREDICTION_HELD
    assert held.reward == 1.0
    flat = evaluate_prediction(
        prediction, {"fuel_starved_entities": 10}, {"fuel_starved_entities": 10}
    )
    assert flat.verdict == PREDICTION_MISSED
    assert flat.reward == 0.0


def test_a_metric_absent_on_either_side_is_unmeasured_and_carries_no_reward():
    prediction = Prediction("fuel_starved_entities", DIRECTION_DECREASE)
    outcome = evaluate_prediction(prediction, {"fuel_starved_entities": 10}, {})
    assert outcome.verdict == PREDICTION_UNMEASURED
    assert outcome.reward is None
    assert outcome.after is None


def test_dotted_prediction_path_reaches_into_a_report_layout():
    prediction = Prediction(
        "electronic_circuit_diagnostics.circuit_assembler.output_after",
        DIRECTION_INCREASE,
    )
    before = {"electronic_circuit_diagnostics": {"circuit_assembler": {"output_after": 0.0}}}
    after = {"electronic_circuit_diagnostics": {"circuit_assembler": {"output_after": 5.0}}}
    assert evaluate_prediction(prediction, before, after).verdict == PREDICTION_HELD


def test_record_carries_symptom_action_and_verdict_together():
    plan = plan_repairs(_boiler_and_two_dead_assemblers())
    step = plan.steps[0]
    record = record_repair(
        step,
        {METRIC_FUEL_STARVED: 1},
        {METRIC_FUEL_STARVED: 0},
    )
    assert record.symptom == "fuel_starved:no_fuel"
    assert record.action_key == f"{TOOL_RESUPPLY}:insert_fuel_from_world_container"
    assert record.tool == TOOL_RESUPPLY
    assert record.reward == 1.0
    assert record.to_dict()["outcome"]["verdict"] == PREDICTION_HELD


# -- the seam a bandit plugs into -------------------------------------------


def test_without_a_scorer_the_declared_rule_decides():
    graph = _drill_into_chest()
    observed = RepairObservation(graph=graph)
    deficit = next(
        row for row in detect_deficits(observed) if row.kind == DEFICIT_OUTPUT_UNPROCESSED
    )
    proposal = propose_actions(diagnose(deficit, observed), observed)
    assert select_action(proposal.candidates).tool == TOOL_PLACEMENT


def test_a_scorer_overrides_the_rule_and_an_unpulled_arm_is_not_a_zero():
    graph = _drill_into_chest()
    observed = RepairObservation(graph=graph)
    deficit = next(
        row for row in detect_deficits(observed) if row.kind == DEFICIT_OUTPUT_UNPROCESSED
    )
    proposal = propose_actions(diagnose(deficit, observed), observed)
    assert proposal.arms == (
        f"{TOOL_PLACEMENT}:{INTENT_PLACE_PROCESSING}",
        f"{TOOL_REBUILD}:reroute_producer_logistics",
    )
    learned = {f"{TOOL_REBUILD}:reroute_producer_logistics": 0.9}
    chosen = select_action(proposal.candidates, score=learned.get)
    assert chosen.tool == TOOL_REBUILD
    # Nothing pulled yet: the rule keeps its position instead of every arm
    # being read as a score of zero.
    assert select_action(proposal.candidates, score=lambda _key: None).tool == TOOL_PLACEMENT


def test_scored_choice_does_not_change_the_derived_order():
    always_rebuild = {f"{TOOL_REBUILD}:reroute_producer_logistics": 1.0}
    plan = plan_repairs(_boiler_and_two_dead_assemblers(), score=always_rebuild.get)
    assert [step.deficit.kind for step in plan.steps] == [
        DEFICIT_FUEL_STARVED,
        DEFICIT_POWER_STARVED,
    ]
