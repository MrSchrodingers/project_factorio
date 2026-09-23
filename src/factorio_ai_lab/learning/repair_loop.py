"""Detect, diagnose, propose, order and score a factory repair, as pure data.

Six consecutive halts of the curriculum were each diagnosed by a human, fixed
by a hand-written branch for that one flow, and followed by a seventh halt of a
different shape: placement collision, empty inventory, empty fuel feed, an
unprioritised boiler, coal dropped on the ground, an assembler with no power.
What the loop was missing is not a seventh branch. It is a cycle that turns the
readings the lab already takes into a repair the agent can choose, predict and
then be scored on.

This module is that cycle and nothing else. It performs no I/O, opens no RCON
connection and never touches the game: it takes an observation and answers
decisions, so the same observation always answers the same plan and a plan can
be replayed from a stored report. Whoever drives a runner turns a
:class:`RepairAction` into calls on :mod:`factorio_ai_lab.planning.resupply`,
:mod:`~factorio_ai_lab.planning.placement`,
:mod:`~factorio_ai_lab.planning.rebuild` or
:mod:`~factorio_ai_lab.planning.dependency_plan`; that binding is deliberately
outside.

The five steps, and what each one is forbidden from doing:

``detect``
    a deficit is a reading, never an inference from absence. A metric that is
    not in the payload answers ``None`` and produces no deficit, and a
    starvation count is only read when ``entity_status_observed`` says a status
    was taken: this project lost eleven generations to ``getattr(ns, k, 0.0)``,
    and a zero that means "not measured" is the same failure wearing a metric's
    name.
``diagnose``
    the cause comes from the classifiers that already exist --
    :func:`~factorio_ai_lab.learning.factory_graph.classify_assembler_stall`
    over a machine's own readings, the graph's own topology over the chains.
    An undetermined cause is a result and is reported as one.
``propose``
    zero or more candidate actions per cause, each naming the tool that
    executes it and carrying a prediction that some named metric will move in a
    named direction. A cause with no action available is a declared refusal,
    which is a record, not a silence.
``order``
    by derived prerequisite, not by blind severity. Every action declares the
    resources it needs and the resources it restores; the rank of a step is one
    above the highest-ranked step that provides something it requires. This is
    what puts the boiler before the assembler: repairing an electric machine
    while the grid is dead buys nothing, and the ordering has to say so from
    the data rather than from a hand-ranked table of symptoms.
``score``
    given the metrics before and after, :func:`evaluate_prediction` says
    whether the prediction held. A metric missing on either side is
    ``unmeasured`` and carries no reward, because a repair that could not be
    measured must not be learned from as a failure.

The ledger is the point. :func:`record_repair` writes symptom, action,
prediction and verdict as one row, and :func:`symptom_key` and
:attr:`RepairAction.key` are the pair a bandit indexes on, so the fixed rule in
:func:`select_action` can be replaced by
:class:`~factorio_ai_lab.learning.bandit.UCB1Bandit` without this module
changing shape: pass ``score`` and the choice stops being a rule.

Removal is guarded at the point of detection. A producer is only offered to the
demolition operator when :func:`produces_into_live_chain` answers ``False`` for
it -- that is, when the graph proves its output reaches nothing that consumes
it. A producer feeding a chest is feeding a chain, and a loop that dismantles a
working line reports its ancestor's collapse as its own regression.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.learning.factory_graph import (
    FUEL_STARVED_CATEGORIES,
    FUEL_STARVED_STATUSES,
    MATERIAL_RELATIONS,
    POWER_STARVED_CATEGORIES,
    POWER_STARVED_STATUSES,
    STALL_CAUSE_FUEL,
    STALL_CAUSE_INGREDIENTS,
    STALL_CAUSE_POWER,
    STALL_CAUSE_PRODUCING,
    STALL_CAUSE_WINDOW,
    classify_assembler_stall,
)

#: Categories a material chain may legitimately end in. A producer reaching one
#: of them is doing something, and demolishing what feeds it destroys capacity.
#: The same three are what `rebuild.identify_ruin_neighborhoods` treats as
#: sinks; neither module can import the set from the other because neither
#: publishes it, and the duplication is recorded here so a future divergence is
#: at least legible instead of silent.
LIVE_CHAIN_CATEGORIES = frozenset({"processing", "buffer", "research"})

#: What the graph measured to be wrong. One name per distinct reading, because
#: the repair each one calls for is different and a merged kind would make the
#: ledger unable to tell which symptom an action was learned against.
DEFICIT_FUEL_STARVED = "fuel_starved"
DEFICIT_POWER_STARVED = "power_starved"
#: A producer with no outgoing material edge at all.
DEFICIT_ISOLATED_PRODUCER = "isolated_producer"
#: A producer whose chain runs somewhere and ends in nothing that consumes it.
DEFICIT_DEAD_OUTPUT_CHAIN = "producer_chain_reaches_no_sink"
#: A producer whose output reaches a container but never a processing machine.
DEFICIT_OUTPUT_UNPROCESSED = "producer_output_unprocessed"
#: One crafting machine produced nothing inside the measured window.
DEFICIT_MACHINE_STALLED = "machine_stalled"
#: No water path reaches a generator, so the electric grid has no source.
DEFICIT_STEAM_PATH_DEAD = "steam_path_dead"

#: Where the reading came from. A deficit named entity by entity and one read
#: off an aggregate count are not the same evidence, and a reader has to be
#: able to tell them apart.
SOURCE_GRAPH_NODES = "graph_nodes"
SOURCE_GRAPH_TOPOLOGY = "graph_topology"
SOURCE_GRAPH_METRICS = "graph_metrics"
SOURCE_MACHINE_REPORT = "machine_report"

#: How the cause was established.
DIAGNOSIS_FROM_STATUS = "entity_status"
DIAGNOSIS_FROM_TOPOLOGY = "graph_topology"
DIAGNOSIS_FROM_METRIC = "graph_metric"
DIAGNOSIS_FROM_STALL_CLASSIFIER = "stall_classifier"
#: The readings do not decide. Kept as a first-class answer: a cause that was
#: never established is not a machine that was found healthy.
DIAGNOSIS_UNDETERMINED = "undetermined"

#: Causes this module names itself, for the deficits the stall classifier does
#: not cover. The starvation causes are reused from ``factory_graph`` so one
#: vocabulary covers both halves of the loop.
CAUSE_NO_MATERIAL_OUTPUT = "no_material_output"
CAUSE_CHAIN_REACHES_NO_SINK = "chain_reaches_no_sink"
CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED = "output_buffered_not_processed"
CAUSE_STEAM_PATH_BROKEN = "steam_path_broken"

#: Which module executes an action. The loop calls these; it never alters them.
TOOL_RESUPPLY = "resupply"
TOOL_PLACEMENT = "placement"
TOOL_REBUILD = "rebuild"
TOOL_DEPENDENCY_PLAN = "dependency_plan"

#: What an action needs and what it restores. Ordering is derived from these
#: two sets and from nothing else.
RESOURCE_FUEL = "fuel"
RESOURCE_POWER = "power"
RESOURCE_MATERIAL = "material"
RESOURCE_MACHINE_OUTPUT = "machine_output"

#: What an action intends, in words, so a journal row can be read without the
#: code beside it.
INTENT_INSERT_FUEL = "insert_fuel_from_world_container"
INTENT_EXTEND_POWER_SUPPLY = "extend_power_supply_to_machine"
INTENT_ATTACH_TO_LIVE_NETWORK = "attach_machine_to_live_network"
INTENT_PLAN_MISSING_INGREDIENTS = "plan_missing_ingredients"
INTENT_CONNECT_PRODUCER = "connect_producer_to_consumer"
INTENT_REROUTE_PRODUCER = "reroute_producer_logistics"
INTENT_PLACE_PROCESSING = "place_processing_for_buffered_output"
INTENT_RESTORE_STEAM_PATH = "restore_water_path_to_generator"

#: Why a deficit is recorded with no action against it. A refusal is a result:
#: a deficit nobody knows how to repair is exactly what the ledger has to keep
#: so the gap can be closed on purpose rather than rediscovered.
REFUSAL_CAUSE_UNDETERMINED = "cause_undetermined"
REFUSAL_NO_KNOWN_ACTION = "no_known_action_for_cause"
REFUSAL_LONGER_WINDOW_NEEDED = "window_too_short_to_judge"
REFUSAL_SUPPLY_NETWORK_DEAD = "machine_is_wired_to_a_dead_network"
REFUSAL_POWER_TOPOLOGY_UNREAD = "power_topology_not_measured"
REFUSAL_TARGET_ITEM_UNREAD = "target_item_not_measured"
REFUSAL_NO_MEASURABLE_PREDICTION = "no_metric_measures_this_repair"

#: Direction a prediction commits to.
DIRECTION_DECREASE = "decrease"
DIRECTION_INCREASE = "increase"

#: Verdict of a prediction once the world was measured again.
PREDICTION_HELD = "held"
PREDICTION_MISSED = "did_not_hold"
#: The metric was absent on at least one side. Never folded into a miss: a
#: repair that could not be measured must not be learned from as a failure.
PREDICTION_UNMEASURED = "unmeasured"

#: Why a step sits where it sits in the order.
RANK_NO_PREREQUISITE = "no_prerequisite"
RANK_AFTER_PROVIDER = "after_resource_provider"
#: Requirements formed a cycle, so the fixed point was cut at a bound. The
#: order is still deterministic; it is simply no longer a proof of precedence.
RANK_CYCLE_CUT = "prerequisite_cycle_cut"

#: Metric names this module predicts on. Every one is produced by
#: `build_factory_graph`; nothing here invents a measurement.
METRIC_FUEL_STARVED = "fuel_starved_entities"
METRIC_POWER_STARVED = "power_starved_entities"
METRIC_ISOLATED_PRODUCERS = "isolated_producers"
METRIC_PRODUCERS_PROCESSED = "producers_reaching_processor"
METRIC_STEAM_PATH_LIVE = "steam_path_live"


def _as_number(raw: Any) -> float | None:
    """One finite number, or ``None`` when the payload carries no reading.

    ``bool`` is accepted because ``steam_path_live`` is one, and a string is
    not: a count that arrived as text was never a count.
    """
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) else None
    return None


def resolve_metric(metrics: Mapping[str, Any] | None, path: str) -> float | None:
    """Follow a dotted path into a metric mapping.

    The path is dotted so one prediction can name a metric that lives under a
    report's own prefix -- ``physical_factory_graph.fuel_starved_entities`` and
    ``electronic_circuit_diagnostics.circuit_assembler.output_after`` are both
    reachable from ``report["metrics"]``. Anything unreachable is ``None``.
    """
    if not isinstance(metrics, Mapping) or not path:
        return None
    current: Any = metrics
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return _as_number(current)


@dataclass(frozen=True)
class MachineReport:
    """One crafting machine as a diagnostics block reported it.

    Every field defaults to ``None`` rather than to a zero, because a reading
    that was never taken has to stay distinguishable from a reading of zero.
    ``output_count`` is the amount produced inside the window, not the amount
    sitting in the output slot: a machine whose slot holds six items and still
    holds six produced nothing, and the classifier is asked about the window.
    """

    machine_id: str
    status: Any = None
    recipe: str | None = None
    input_count: float | None = None
    output_count: float | None = None
    #: Electric network the machine itself is attached to. Factorio answers a
    #: non-positive identifier for a machine attached to none.
    network_id: float | None = None
    #: A network measured to be live near it, when the report took that
    #: reading. It is what tells "no pole reaches this machine" from "the grid
    #: this machine is on delivers nothing".
    supply_network_id: float | None = None
    #: Dotted path of the metric that measures this machine's output. Without
    #: one there is no verifiable prediction to make about repairing it.
    output_metric: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine_id": self.machine_id,
            "status": self.status,
            "recipe": self.recipe,
            "input_count": self.input_count,
            "output_count": self.output_count,
            "network_id": self.network_id,
            "supply_network_id": self.supply_network_id,
            "output_metric": self.output_metric,
        }


def machine_reports_from_diagnostics(
    block: Mapping[str, Any] | None,
    *,
    metric_prefix: str = "",
    supply_network_path: str = "power.circuit_pole_network_id",
) -> tuple[MachineReport, ...]:
    """Read every machine a diagnostics block describes, in key order.

    A block entry counts as a machine when it is a mapping carrying a
    ``recipe``; the scalars beside them are left alone. The window output is
    ``output_after - output_before`` and is ``None`` unless both were read,
    because one endpoint alone measures a stock rather than a production.
    """
    if not isinstance(block, Mapping):
        return ()
    supply = resolve_metric(block, supply_network_path)
    reports: list[MachineReport] = []
    for key in sorted(block):
        entry = block[key]
        if not isinstance(entry, Mapping) or "recipe" not in entry:
            continue
        before = _as_number(entry.get("output_before"))
        after = _as_number(entry.get("output_after"))
        produced = None if before is None or after is None else after - before
        inputs = entry.get("inputs_after")
        if not isinstance(inputs, Mapping):
            inputs = entry.get("inputs_before")
        input_count = None
        if isinstance(inputs, Mapping):
            readings = [_as_number(value) for value in inputs.values()]
            if all(value is not None for value in readings):
                input_count = float(sum(value for value in readings if value is not None))
        status = entry.get("status_after", entry.get("status_before"))
        recipe = entry.get("recipe")
        reports.append(
            MachineReport(
                machine_id=str(key),
                status=status,
                recipe=None if recipe is None else str(recipe),
                input_count=input_count,
                output_count=produced,
                network_id=_as_number(entry.get("network_id")),
                supply_network_id=supply,
                output_metric=(
                    None if after is None else f"{metric_prefix}{key}.output_after"
                ),
            )
        )
    return tuple(reports)


@dataclass(frozen=True)
class RepairObservation:
    """Everything one repair cycle is allowed to read.

    ``graph`` is whatever ``build_factory_graph`` returned, or the metrics-only
    remnant a generation report stores under ``physical_factory_graph``; both
    are accepted because the second is what survives a run and the loop has to
    stay usable over a stored report. ``graph_metric_prefix`` is where those
    metrics sit relative to the mapping the caller will later hand
    :func:`evaluate_prediction`, so a prediction written now can be resolved
    then without either side guessing the layout.
    """

    graph: Mapping[str, Any] = field(default_factory=dict)
    machine_reports: tuple[MachineReport, ...] = ()
    graph_metric_prefix: str = ""

    @property
    def nodes(self) -> tuple[Mapping[str, Any], ...]:
        rows = self.graph.get("nodes")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return ()
        return tuple(row for row in rows if isinstance(row, Mapping))

    @property
    def edges_read(self) -> bool:
        """True only when the payload carries an edge list at all."""
        rows = self.graph.get("edges")
        return isinstance(rows, Sequence) and not isinstance(rows, (str, bytes))

    @property
    def metrics(self) -> Mapping[str, Any]:
        raw = self.graph.get("metrics")
        if isinstance(raw, Mapping):
            return raw
        # A stored report keeps the metrics without the nodes, so the payload
        # itself is the metric mapping.
        return self.graph

    def metric(self, name: str) -> float | None:
        return _as_number(self.metrics.get(name))

    def metric_path(self, name: str) -> str:
        return f"{self.graph_metric_prefix}{name}"

    def report(self, machine_id: str) -> MachineReport | None:
        return next(
            (row for row in self.machine_reports if row.machine_id == machine_id),
            None,
        )


def material_adjacency(graph: Mapping[str, Any]) -> dict[str, set[str]] | None:
    """Forward material adjacency, or ``None`` when no edge list was read.

    Built over ``MATERIAL_RELATIONS`` and nothing else, which is the set
    ``producers_reaching_processor`` counts over: a look-alike set here would
    answer a different question while reading like the same one.
    """
    rows = graph.get("edges")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    adjacency: dict[str, set[str]] = {}
    for edge in rows:
        if not isinstance(edge, Mapping):
            continue
        if edge.get("relation") not in MATERIAL_RELATIONS:
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and isinstance(target, str):
            adjacency.setdefault(source, set()).add(target)
    return adjacency


def produces_into_live_chain(
    graph: Mapping[str, Any],
    node_id: str,
) -> bool | None:
    """Whether this node's output reaches anything that consumes it.

    ``None`` when the graph carries no edge list or does not hold the node: the
    question was not answered, which is not an answer of ``False``. Every
    action that removes an entity is gated on a ``False`` from here, so an
    unread graph can never authorise a demolition.
    """
    adjacency = material_adjacency(graph)
    if adjacency is None:
        return None
    categories: dict[str, str] = {}
    rows = graph.get("nodes")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("id"), str):
            categories[str(row["id"])] = str(row.get("category", ""))
    if node_id not in categories:
        return None
    queue: deque[str] = deque([node_id])
    seen = {node_id}
    while queue:
        current = queue.popleft()
        for nxt in sorted(adjacency.get(current, set())):
            if nxt in seen:
                continue
            if categories.get(nxt) in LIVE_CHAIN_CATEGORIES:
                return True
            seen.add(nxt)
            queue.append(nxt)
    return False


def _downstream(adjacency: Mapping[str, set[str]], source: str) -> tuple[str, ...]:
    queue: deque[str] = deque([source])
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        for nxt in sorted(adjacency.get(current, set())):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return tuple(sorted(seen))


@dataclass(frozen=True)
class Deficit:
    """One measured shortfall, with the reading that proves it.

    ``entities`` is ``None`` when the source could not enumerate them -- a
    metrics-only report counts ten starved burners without naming one -- and an
    empty tuple only when enumeration happened and found none. Collapsing the
    two would turn "not listed" into "none".
    """

    kind: str
    source: str
    severity: float
    entities: tuple[str, ...] | None = None
    category: str | None = None
    measurement: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "severity": self.severity,
            "entities": None if self.entities is None else list(self.entities),
            "category": self.category,
            "measurement": dict(self.measurement),
        }


@dataclass(frozen=True)
class Diagnosis:
    """The probable cause of one deficit, and how it was established."""

    deficit: Deficit
    cause: str | None
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "deficit": self.deficit.to_dict(),
            "cause": self.cause,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class Prediction:
    """What an action commits to changing, in a way that can be checked."""

    metric: str
    direction: str

    def to_dict(self) -> dict[str, str]:
        return {"metric": self.metric, "direction": self.direction}


@dataclass(frozen=True)
class RepairAction:
    """One candidate repair: which tool runs it, and what it should change."""

    tool: str
    intent: str
    prediction: Prediction
    requires: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    targets: tuple[str, ...] | None = None
    #: Entities the action takes down. Only ever non-empty when the graph
    #: proved each one feeds nothing that consumes its output.
    removes: tuple[str, ...] = ()
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Identifier a bandit indexes an arm by."""
        return f"{self.tool}:{self.intent}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "intent": self.intent,
            "key": self.key,
            "prediction": self.prediction.to_dict(),
            "requires": list(self.requires),
            "provides": list(self.provides),
            "targets": None if self.targets is None else list(self.targets),
            "removes": list(self.removes),
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True)
class RepairProposal:
    """Every action available against one diagnosis, or the refusal instead."""

    diagnosis: Diagnosis
    candidates: tuple[RepairAction, ...] = ()
    refusal: str | None = None

    @property
    def arms(self) -> tuple[str, ...]:
        """Arm identifiers a bandit would choose between for this symptom."""
        return tuple(action.key for action in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis.to_dict(),
            "symptom": symptom_key(self.diagnosis),
            "candidates": [action.to_dict() for action in self.candidates],
            "refusal": self.refusal,
        }


@dataclass(frozen=True)
class RepairStep:
    """One chosen action, and where the derived order puts it."""

    proposal: RepairProposal
    action: RepairAction
    rank: int
    rank_basis: str

    @property
    def diagnosis(self) -> Diagnosis:
        return self.proposal.diagnosis

    @property
    def deficit(self) -> Deficit:
        return self.proposal.diagnosis.deficit

    def to_dict(self) -> dict[str, Any]:
        return {
            "symptom": symptom_key(self.diagnosis),
            "deficit": self.deficit.to_dict(),
            "cause": self.diagnosis.cause,
            "basis": self.diagnosis.basis,
            "action": self.action.to_dict(),
            "rank": self.rank,
            "rank_basis": self.rank_basis,
        }


@dataclass(frozen=True)
class RepairPlan:
    """What the cycle decided: ordered steps, and every declared refusal."""

    steps: tuple[RepairStep, ...] = ()
    refusals: tuple[RepairProposal, ...] = ()
    proposals: tuple[RepairProposal, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "refusals": [row.to_dict() for row in self.refusals],
        }


def symptom_key(diagnosis: Diagnosis) -> str:
    """Identifier a ledger groups outcomes under.

    Kind and cause together, because the same kind repaired for two different
    causes is two different lessons, and ``undetermined`` is spelled out rather
    than dropped so an unexplained deficit can be counted.
    """
    cause = diagnosis.cause or DIAGNOSIS_UNDETERMINED
    return f"{diagnosis.deficit.kind}:{cause}"


def detect_deficits(observation: RepairObservation) -> tuple[Deficit, ...]:
    """Every shortfall the observation measured, in a fixed order.

    Node-level readings take precedence over aggregate counts for the same
    fact: when the payload names entities, the deficit names them too, and the
    aggregate is not re-reported beside it. Nothing is derived from a metric
    being absent.
    """
    nodes = observation.nodes
    found: list[Deficit] = []
    if nodes:
        found.extend(_starvation_from_nodes(nodes))
        found.extend(_chains_from_nodes(observation, nodes))
    else:
        found.extend(_starvation_from_metrics(observation))
        found.extend(_chains_from_metrics(observation))
    found.extend(_steam_from_metrics(observation))
    found.extend(_stalls_from_reports(observation))
    return tuple(found)


def _status_of(node: Mapping[str, Any]) -> str:
    # `build_factory_graph` already normalised the status onto the node, so a
    # second normalisation here would only hide a payload that never went
    # through it.
    return str(node.get("status", ""))


def _grouped(
    nodes: Sequence[Mapping[str, Any]],
    *,
    categories: frozenset[str],
    statuses: frozenset[str],
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for node in nodes:
        category = str(node.get("category", ""))
        identifier = node.get("id")
        if category not in categories or not isinstance(identifier, str):
            continue
        if _status_of(node) not in statuses:
            continue
        groups.setdefault(category, []).append(identifier)
    return {key: sorted(value) for key, value in sorted(groups.items())}


def _starvation_from_nodes(nodes: Sequence[Mapping[str, Any]]) -> list[Deficit]:
    """Starved entities, grouped by the category that decides the repair.

    The grouping is not cosmetic: a starved boiler is the grid's supply and a
    starved drill is the ore supply, so the two restore different resources and
    have to be able to sit at different ranks of the same plan.
    """
    found: list[Deficit] = []
    fuel = _grouped(
        nodes,
        categories=FUEL_STARVED_CATEGORIES,
        statuses=FUEL_STARVED_STATUSES,
    )
    for category, identifiers in fuel.items():
        found.append(
            Deficit(
                kind=DEFICIT_FUEL_STARVED,
                source=SOURCE_GRAPH_NODES,
                severity=float(len(identifiers)),
                entities=tuple(identifiers),
                category=category,
                measurement={"starved_entities": len(identifiers)},
            )
        )
    power = _grouped(
        nodes,
        categories=POWER_STARVED_CATEGORIES,
        statuses=POWER_STARVED_STATUSES,
    )
    for category, identifiers in power.items():
        found.append(
            Deficit(
                kind=DEFICIT_POWER_STARVED,
                source=SOURCE_GRAPH_NODES,
                severity=float(len(identifiers)),
                entities=tuple(identifiers),
                category=category,
                measurement={"starved_entities": len(identifiers)},
            )
        )
    return found


def _chains_from_nodes(
    observation: RepairObservation,
    nodes: Sequence[Mapping[str, Any]],
) -> list[Deficit]:
    """Producers whose output goes nowhere useful, split by what is missing.

    Three readings, never merged, because each one is a different tool's job:
    a producer wired to nothing needs a connection built, a producer whose
    chain dies in open ground is what the demolition operator was written for,
    and a producer filling a chest is short of a machine, not of logistics.
    """
    adjacency = material_adjacency(observation.graph)
    if adjacency is None:
        return []
    processors = {
        str(row["id"])
        for row in nodes
        if isinstance(row.get("id"), str) and row.get("category") == "processing"
    }
    isolated: list[str] = []
    dead: list[tuple[str, tuple[str, ...]]] = []
    unprocessed: list[str] = []
    for row in nodes:
        identifier = row.get("id")
        if row.get("category") != "extraction" or not isinstance(identifier, str):
            continue
        if not adjacency.get(identifier):
            isolated.append(identifier)
            continue
        if produces_into_live_chain(observation.graph, identifier) is not True:
            dead.append((identifier, _downstream(adjacency, identifier)))
            continue
        if not (set(_downstream(adjacency, identifier)) & processors):
            unprocessed.append(identifier)

    found: list[Deficit] = []
    if isolated:
        found.append(
            Deficit(
                kind=DEFICIT_ISOLATED_PRODUCER,
                source=SOURCE_GRAPH_TOPOLOGY,
                severity=float(len(isolated)),
                entities=tuple(sorted(isolated)),
                measurement={"isolated_producers": len(isolated)},
            )
        )
    for identifier, downstream in sorted(dead):
        found.append(
            Deficit(
                kind=DEFICIT_DEAD_OUTPUT_CHAIN,
                source=SOURCE_GRAPH_TOPOLOGY,
                severity=1.0,
                entities=(identifier,),
                measurement={"downstream_entities": list(downstream)},
            )
        )
    if unprocessed:
        found.append(
            Deficit(
                kind=DEFICIT_OUTPUT_UNPROCESSED,
                source=SOURCE_GRAPH_TOPOLOGY,
                severity=float(len(unprocessed)),
                entities=tuple(sorted(unprocessed)),
                measurement={"buffered_only_producers": len(unprocessed)},
            )
        )
    return found


def _starvation_from_metrics(observation: RepairObservation) -> list[Deficit]:
    """Aggregate starvation counts, read only when a status was taken.

    ``entity_status_observed`` is the gate. Without it a count of zero is an
    absence of measurement wearing the shape of health, and a count above zero
    could not have been produced anyway.
    """
    if observation.metric("entity_status_observed") != 1.0:
        return []
    found: list[Deficit] = []
    for kind, name in (
        (DEFICIT_FUEL_STARVED, METRIC_FUEL_STARVED),
        (DEFICIT_POWER_STARVED, METRIC_POWER_STARVED),
    ):
        count = observation.metric(name)
        if count is None or count <= 0:
            continue
        found.append(
            Deficit(
                kind=kind,
                source=SOURCE_GRAPH_METRICS,
                severity=count,
                entities=None,
                measurement={name: count},
            )
        )
    return found


def _chains_from_metrics(observation: RepairObservation) -> list[Deficit]:
    found: list[Deficit] = []
    isolated = observation.metric(METRIC_ISOLATED_PRODUCERS)
    if isolated is not None and isolated > 0:
        found.append(
            Deficit(
                kind=DEFICIT_ISOLATED_PRODUCER,
                source=SOURCE_GRAPH_METRICS,
                severity=isolated,
                entities=None,
                measurement={METRIC_ISOLATED_PRODUCERS: isolated},
            )
        )
    producers = observation.metric("producer_count")
    processed = observation.metric(METRIC_PRODUCERS_PROCESSED)
    if producers is None or processed is None or producers <= 0:
        return found
    shortfall = producers - processed - (isolated or 0.0)
    if shortfall > 0:
        found.append(
            Deficit(
                kind=DEFICIT_OUTPUT_UNPROCESSED,
                source=SOURCE_GRAPH_METRICS,
                severity=shortfall,
                entities=None,
                measurement={
                    "producer_count": producers,
                    METRIC_PRODUCERS_PROCESSED: processed,
                    METRIC_ISOLATED_PRODUCERS: isolated,
                },
            )
        )
    return found


def _steam_from_metrics(observation: RepairObservation) -> list[Deficit]:
    live = observation.metric(METRIC_STEAM_PATH_LIVE)
    if live is None or live > 0:
        return []
    return [
        Deficit(
            kind=DEFICIT_STEAM_PATH_DEAD,
            source=SOURCE_GRAPH_METRICS,
            severity=1.0,
            entities=None,
            measurement={METRIC_STEAM_PATH_LIVE: live},
        )
    ]


def _stalls_from_reports(observation: RepairObservation) -> list[Deficit]:
    found: list[Deficit] = []
    for report in observation.machine_reports:
        cause = classify_assembler_stall(
            status=report.status,
            recipe=report.recipe,
            input_count=report.input_count,
            output_count=report.output_count,
        )
        if cause == STALL_CAUSE_PRODUCING:
            continue
        found.append(
            Deficit(
                kind=DEFICIT_MACHINE_STALLED,
                source=SOURCE_MACHINE_REPORT,
                severity=1.0,
                entities=(report.machine_id,),
                measurement={
                    "status": report.status,
                    "recipe": report.recipe,
                    "input_count": report.input_count,
                    "output_count": report.output_count,
                    "network_id": report.network_id,
                },
            )
        )
    return found


#: Cause of every deficit this module names itself, by kind. The stalled
#: machine is deliberately absent: its cause is the classifier's answer, not a
#: constant, and mapping it here would be a second opinion about the same
#: reading.
_CAUSE_BY_KIND = {
    DEFICIT_FUEL_STARVED: STALL_CAUSE_FUEL,
    DEFICIT_POWER_STARVED: STALL_CAUSE_POWER,
    DEFICIT_ISOLATED_PRODUCER: CAUSE_NO_MATERIAL_OUTPUT,
    DEFICIT_DEAD_OUTPUT_CHAIN: CAUSE_CHAIN_REACHES_NO_SINK,
    DEFICIT_OUTPUT_UNPROCESSED: CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
    DEFICIT_STEAM_PATH_DEAD: CAUSE_STEAM_PATH_BROKEN,
}

_BASIS_BY_SOURCE = {
    SOURCE_GRAPH_NODES: DIAGNOSIS_FROM_STATUS,
    SOURCE_GRAPH_TOPOLOGY: DIAGNOSIS_FROM_TOPOLOGY,
    SOURCE_GRAPH_METRICS: DIAGNOSIS_FROM_METRIC,
}


def diagnose(deficit: Deficit, observation: RepairObservation) -> Diagnosis:
    """Name the probable cause of one deficit, using the existing classifiers.

    A stalled machine is handed back to
    :func:`~factorio_ai_lab.learning.factory_graph.classify_assembler_stall`
    rather than re-decided here, so one vocabulary answers both halves of the
    loop. When it declines to decide, so does this function.
    """
    if deficit.kind == DEFICIT_MACHINE_STALLED:
        machine_id = (deficit.entities or ("",))[0]
        report = observation.report(machine_id)
        cause = (
            None
            if report is None
            else classify_assembler_stall(
                status=report.status,
                recipe=report.recipe,
                input_count=report.input_count,
                output_count=report.output_count,
            )
        )
        return Diagnosis(
            deficit=deficit,
            cause=cause,
            basis=(
                DIAGNOSIS_UNDETERMINED
                if cause is None
                else DIAGNOSIS_FROM_STALL_CLASSIFIER
            ),
        )
    cause = _CAUSE_BY_KIND.get(deficit.kind)
    if cause is None:
        return Diagnosis(deficit=deficit, cause=None, basis=DIAGNOSIS_UNDETERMINED)
    return Diagnosis(
        deficit=deficit,
        cause=cause,
        basis=_BASIS_BY_SOURCE.get(deficit.source, DIAGNOSIS_UNDETERMINED),
    )


def _fuel_provides(category: str | None) -> tuple[str, ...]:
    """What refuelling this category restores, as far as it was measured.

    An aggregate count names no category, so the action claims only that fuel
    arrives. Claiming power from a count that might hold no boiler would be an
    inference dressed as a reading, and the ordering is built on these claims.
    """
    if category is None:
        return (RESOURCE_FUEL,)
    if category == "energy":
        return (RESOURCE_POWER,)
    return (RESOURCE_MATERIAL,)


def _power_provides(category: str | None) -> tuple[str, ...]:
    if category == "extraction":
        return (RESOURCE_MATERIAL,)
    return (RESOURCE_MACHINE_OUTPUT,)


def _refused(diagnosis: Diagnosis, refusal: str) -> RepairProposal:
    return RepairProposal(diagnosis=diagnosis, refusal=refusal)


def _machine_power_actions(
    diagnosis: Diagnosis,
    observation: RepairObservation,
) -> RepairProposal:
    """What to do about a machine that reported no power.

    Three measured situations, three different answers. A machine on no
    network needs one reaching it. A machine already on a network that
    delivers nothing is not a placement problem at all -- the generation is,
    and that deficit is raised on its own -- so this refuses by name instead of
    proposing a pole that would change nothing. A machine whose network was
    never read gets neither answer.
    """
    machine_id = (diagnosis.deficit.entities or ("",))[0]
    report = observation.report(machine_id)
    if report is None or report.network_id is None:
        return _refused(diagnosis, REFUSAL_POWER_TOPOLOGY_UNREAD)
    if report.network_id > 0:
        return _refused(diagnosis, REFUSAL_SUPPLY_NETWORK_DEAD)
    if report.output_metric is None:
        return _refused(diagnosis, REFUSAL_NO_MEASURABLE_PREDICTION)
    live_supply = report.supply_network_id is not None and report.supply_network_id > 0
    action = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_ATTACH_TO_LIVE_NETWORK if live_supply else INTENT_EXTEND_POWER_SUPPLY,
        prediction=Prediction(report.output_metric, DIRECTION_INCREASE),
        requires=(RESOURCE_POWER,),
        provides=(RESOURCE_MACHINE_OUTPUT,),
        targets=(machine_id,),
        arguments={
            "machine_id": machine_id,
            "machine_network_id": report.network_id,
            "supply_network_id": report.supply_network_id,
        },
    )
    return RepairProposal(diagnosis=diagnosis, candidates=(action,))


def _machine_actions(
    diagnosis: Diagnosis,
    observation: RepairObservation,
) -> RepairProposal:
    machine_id = (diagnosis.deficit.entities or ("",))[0]
    report = observation.report(machine_id)
    cause = diagnosis.cause
    if cause is None:
        return _refused(diagnosis, REFUSAL_CAUSE_UNDETERMINED)
    if cause == STALL_CAUSE_WINDOW:
        return _refused(diagnosis, REFUSAL_LONGER_WINDOW_NEEDED)
    if cause == STALL_CAUSE_POWER:
        return _machine_power_actions(diagnosis, observation)
    if report is None or report.output_metric is None:
        return _refused(diagnosis, REFUSAL_NO_MEASURABLE_PREDICTION)
    prediction = Prediction(report.output_metric, DIRECTION_INCREASE)
    if cause == STALL_CAUSE_FUEL:
        action = RepairAction(
            tool=TOOL_RESUPPLY,
            intent=INTENT_INSERT_FUEL,
            prediction=prediction,
            provides=(RESOURCE_MACHINE_OUTPUT,),
            targets=(machine_id,),
            arguments={"machine_id": machine_id},
        )
        return RepairProposal(diagnosis=diagnosis, candidates=(action,))
    if cause == STALL_CAUSE_INGREDIENTS:
        if not report.recipe:
            return _refused(diagnosis, REFUSAL_TARGET_ITEM_UNREAD)
        action = RepairAction(
            tool=TOOL_DEPENDENCY_PLAN,
            intent=INTENT_PLAN_MISSING_INGREDIENTS,
            prediction=prediction,
            requires=(RESOURCE_MATERIAL,),
            provides=(RESOURCE_MACHINE_OUTPUT,),
            targets=(machine_id,),
            arguments={"machine_id": machine_id, "target_item": report.recipe},
        )
        return RepairProposal(diagnosis=diagnosis, candidates=(action,))
    # STALL_CAUSE_RECIPE and every raw status the classifier hands back
    # unchanged: measured, named, and outside what these four tools do.
    return _refused(diagnosis, REFUSAL_NO_KNOWN_ACTION)


def _chain_actions(
    diagnosis: Diagnosis,
    observation: RepairObservation,
) -> RepairProposal:
    """Candidates for a producer whose output does not reach a machine.

    Two tools can answer each of these, which is the whole reason the choice is
    worth learning: building the missing link and re-routing what is already
    standing cost different amounts and succeed in different worlds. The fixed
    rule below prefers to build before it demolishes; a scorer replaces that
    preference without changing anything else.
    """
    deficit = diagnosis.deficit
    targets = deficit.entities
    if deficit.kind == DEFICIT_ISOLATED_PRODUCER:
        prediction = Prediction(
            observation.metric_path(METRIC_ISOLATED_PRODUCERS),
            DIRECTION_DECREASE,
        )
    else:
        prediction = Prediction(
            observation.metric_path(METRIC_PRODUCERS_PROCESSED),
            DIRECTION_INCREASE,
        )
    build = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=(
            INTENT_PLACE_PROCESSING
            if deficit.kind == DEFICIT_OUTPUT_UNPROCESSED
            else INTENT_CONNECT_PRODUCER
        ),
        prediction=prediction,
        provides=(RESOURCE_MATERIAL,),
        targets=targets,
        arguments={"producers": None if targets is None else list(targets)},
    )
    removes: tuple[str, ...] = ()
    if deficit.kind == DEFICIT_DEAD_OUTPUT_CHAIN:
        # The demolition is only offered because detection already proved, over
        # the graph, that this chain reaches nothing that consumes its output.
        # Re-asking here would be a second copy of the same decision.
        downstream = deficit.measurement.get("downstream_entities")
        removes = tuple(downstream) if isinstance(downstream, Sequence) else ()
    reroute = RepairAction(
        tool=TOOL_REBUILD,
        intent=INTENT_REROUTE_PRODUCER,
        prediction=prediction,
        provides=(RESOURCE_MATERIAL,),
        targets=targets,
        removes=removes,
        arguments={"producers": None if targets is None else list(targets)},
    )
    candidates = (
        (reroute, build)
        if deficit.kind == DEFICIT_DEAD_OUTPUT_CHAIN
        else (build, reroute)
    )
    return RepairProposal(diagnosis=diagnosis, candidates=candidates)


def propose_actions(
    diagnosis: Diagnosis,
    observation: RepairObservation,
) -> RepairProposal:
    """Zero or more candidate repairs for one diagnosis.

    Zero is a result and is returned as a named refusal, never as an empty
    proposal a caller could mistake for "nothing was wrong".
    """
    deficit = diagnosis.deficit
    if deficit.kind == DEFICIT_MACHINE_STALLED:
        return _machine_actions(diagnosis, observation)
    if deficit.kind == DEFICIT_FUEL_STARVED:
        action = RepairAction(
            tool=TOOL_RESUPPLY,
            intent=INTENT_INSERT_FUEL,
            prediction=Prediction(
                observation.metric_path(METRIC_FUEL_STARVED),
                DIRECTION_DECREASE,
            ),
            provides=_fuel_provides(deficit.category),
            targets=deficit.entities,
            arguments={
                "category": deficit.category,
                "entities": None if deficit.entities is None else list(deficit.entities),
            },
        )
        return RepairProposal(diagnosis=diagnosis, candidates=(action,))
    if deficit.kind == DEFICIT_POWER_STARVED:
        if not observation.edges_read:
            return _refused(diagnosis, REFUSAL_POWER_TOPOLOGY_UNREAD)
        action = RepairAction(
            tool=TOOL_PLACEMENT,
            intent=INTENT_EXTEND_POWER_SUPPLY,
            prediction=Prediction(
                observation.metric_path(METRIC_POWER_STARVED),
                DIRECTION_DECREASE,
            ),
            requires=(RESOURCE_POWER,),
            provides=_power_provides(deficit.category),
            targets=deficit.entities,
            arguments={
                "category": deficit.category,
                "entities": None if deficit.entities is None else list(deficit.entities),
            },
        )
        return RepairProposal(diagnosis=diagnosis, candidates=(action,))
    if deficit.kind == DEFICIT_STEAM_PATH_DEAD:
        action = RepairAction(
            tool=TOOL_PLACEMENT,
            intent=INTENT_RESTORE_STEAM_PATH,
            prediction=Prediction(
                observation.metric_path(METRIC_STEAM_PATH_LIVE),
                DIRECTION_INCREASE,
            ),
            provides=(RESOURCE_POWER,),
        )
        return RepairProposal(diagnosis=diagnosis, candidates=(action,))
    if deficit.kind in {
        DEFICIT_ISOLATED_PRODUCER,
        DEFICIT_DEAD_OUTPUT_CHAIN,
        DEFICIT_OUTPUT_UNPROCESSED,
    }:
        return _chain_actions(diagnosis, observation)
    return _refused(diagnosis, REFUSAL_NO_KNOWN_ACTION)


def select_action(
    candidates: Sequence[RepairAction],
    *,
    score: Callable[[str], float | None] | None = None,
) -> RepairAction | None:
    """Pick one candidate: by rule, or by whatever ``score`` has learned.

    Without ``score`` the first candidate wins, which is the fixed rule the
    first version ships with. With it, the highest-scored arm wins and the
    declared order breaks ties, so a bandit can be handed in without this
    module changing: an arm it has never pulled answers ``None`` and keeps its
    rule position instead of being read as a score of zero.
    """
    if not candidates:
        return None
    if score is None:
        return candidates[0]
    ranked = [
        (score(action.key), index, action)
        for index, action in enumerate(candidates)
    ]
    scored = [row for row in ranked if row[0] is not None]
    if not scored:
        return candidates[0]
    return max(scored, key=lambda row: (row[0], -row[1]))[2]


def _prerequisite_ranks(
    actions: Sequence[RepairAction],
) -> tuple[list[int], list[str]]:
    """Rank of every action, one above the highest provider it depends on.

    Computed by iterating to a fixed point, bounded at one pass per action.
    The bound is what makes a cycle survivable: requirements that form one --
    a coal drill that needs the power its own coal generates -- cannot be
    ordered by precedence at all, and the steps involved are marked
    :data:`RANK_CYCLE_CUT` rather than left to loop.

    The cost is ``O(n^2)`` in the number of actions against a topological
    lower bound of ``O(n + e)``. It stays because ``n`` is the number of
    deficits one observation holds -- three and two over the stored
    generations this was measured on -- and a heap-ordered rewrite would buy
    nothing at that size while making the criterion harder to read.
    """
    providers: dict[str, list[int]] = {}
    for index, action in enumerate(actions):
        for resource in action.provides:
            providers.setdefault(resource, []).append(index)

    ranks = [0] * len(actions)
    basis = [RANK_NO_PREREQUISITE] * len(actions)
    settled = True
    for _ in range(len(actions) + 1):
        changed = False
        for index, action in enumerate(actions):
            highest = max(
                (
                    ranks[provider] + 1
                    for resource in action.requires
                    for provider in providers.get(resource, ())
                    if provider != index
                ),
                default=ranks[index],
            )
            if highest > ranks[index]:
                ranks[index] = highest
                basis[index] = RANK_AFTER_PROVIDER
                changed = True
        if not changed:
            break
    else:
        settled = False

    if not settled:
        basis = [
            RANK_CYCLE_CUT if entry == RANK_AFTER_PROVIDER else entry
            for entry in basis
        ]
    return ranks, basis


def order_steps(pairs: Sequence[tuple[RepairProposal, RepairAction]]) -> tuple[RepairStep, ...]:
    """Order repairs by derived prerequisite, then by measured severity.

    The rank of a step is one above the highest-ranked step that provides a
    resource it requires; a step that requires nothing available is rank zero.
    That is the whole criterion, and it is computed from the ``requires`` and
    ``provides`` each action declares rather than from a table of symptoms:
    repairing an electric machine requires power, refuelling a boiler provides
    it, and so the boiler is planned first without anyone ranking boilers.

    Severity breaks ties inside one rank and never across ranks, which is the
    difference between this and sorting by how bad each symptom looks: two
    dead assemblers outweigh one dry boiler and still have to wait for it.
    """
    ranks, basis = _prerequisite_ranks([action for _, action in pairs])

    def sort_key(index: int) -> tuple[int, float, str, str, str]:
        proposal, action = pairs[index]
        deficit = proposal.diagnosis.deficit
        first = (deficit.entities or ("",))[0] if deficit.entities else ""
        return (ranks[index], -deficit.severity, deficit.kind, first, action.key)

    return tuple(
        RepairStep(
            proposal=pairs[index][0],
            action=pairs[index][1],
            rank=ranks[index],
            rank_basis=basis[index],
        )
        for index in sorted(range(len(pairs)), key=sort_key)
    )


def plan_repairs(
    observation: RepairObservation,
    *,
    score: Callable[[str], float | None] | None = None,
) -> RepairPlan:
    """Run detect, diagnose, propose and order over one observation."""
    proposals = tuple(
        propose_actions(diagnose(deficit, observation), observation)
        for deficit in detect_deficits(observation)
    )
    pairs: list[tuple[RepairProposal, RepairAction]] = []
    refusals: list[RepairProposal] = []
    for proposal in proposals:
        chosen = select_action(proposal.candidates, score=score)
        if chosen is None:
            refusals.append(proposal)
            continue
        pairs.append((proposal, chosen))
    return RepairPlan(
        steps=order_steps(pairs),
        refusals=tuple(refusals),
        proposals=proposals,
    )


@dataclass(frozen=True)
class PredictionOutcome:
    """Whether a prediction held, and the two readings that decided it."""

    prediction: Prediction
    before: float | None
    after: float | None
    verdict: str

    @property
    def reward(self) -> float | None:
        """Reward a bandit may be updated with, or ``None`` to skip the update.

        An unmeasured outcome has no reward at all. Folding it into zero would
        teach the chooser that a repair nobody measured is a repair that
        failed, which is how a working action gets abandoned.
        """
        if self.verdict == PREDICTION_HELD:
            return 1.0
        if self.verdict == PREDICTION_MISSED:
            return 0.0
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction": self.prediction.to_dict(),
            "before": self.before,
            "after": self.after,
            "verdict": self.verdict,
            "reward": self.reward,
        }


def evaluate_prediction(
    prediction: Prediction,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> PredictionOutcome:
    """Say whether the predicted metric moved the way the action claimed.

    Equality is a miss, not a hold: an action that promised a metric would fall
    and left it where it was did not repair anything. A metric absent from
    either side is :data:`PREDICTION_UNMEASURED`, which carries no reward.
    """
    start = resolve_metric(before, prediction.metric)
    end = resolve_metric(after, prediction.metric)
    if start is None or end is None:
        verdict = PREDICTION_UNMEASURED
    elif prediction.direction == DIRECTION_DECREASE:
        verdict = PREDICTION_HELD if end < start else PREDICTION_MISSED
    elif prediction.direction == DIRECTION_INCREASE:
        verdict = PREDICTION_HELD if end > start else PREDICTION_MISSED
    else:
        verdict = PREDICTION_UNMEASURED
    return PredictionOutcome(
        prediction=prediction,
        before=start,
        after=end,
        verdict=verdict,
    )


@dataclass(frozen=True)
class RepairRecord:
    """One repair as the ledger keeps it: symptom, action, claim, verdict.

    This row is the point of the whole module. With it, the choice between two
    tools for the same symptom can stop being the rule in
    :func:`select_action` and become something a bandit was taught; without it
    every generation re-decides from scratch and the loop learns nothing.
    """

    symptom: str
    action_key: str
    tool: str
    rank: int
    rank_basis: str
    outcome: PredictionOutcome

    @property
    def reward(self) -> float | None:
        return self.outcome.reward

    def to_dict(self) -> dict[str, Any]:
        return {
            "symptom": self.symptom,
            "action_key": self.action_key,
            "tool": self.tool,
            "rank": self.rank,
            "rank_basis": self.rank_basis,
            "outcome": self.outcome.to_dict(),
        }


def record_repair(
    step: RepairStep,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> RepairRecord:
    """Score one executed step and shape it as a ledger row."""
    return RepairRecord(
        symptom=symptom_key(step.diagnosis),
        action_key=step.action.key,
        tool=step.action.tool,
        rank=step.rank,
        rank_basis=step.rank_basis,
        outcome=evaluate_prediction(step.action.prediction, before, after),
    )
