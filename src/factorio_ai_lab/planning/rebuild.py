"""Ruin-and-recreate (large neighborhood search) over a physical factory.

The runners already remove entities, but every call site is housekeeping: undo
what the experiment itself placed, decommission the bootstrap drill so it does
not contaminate evidence, drop a half-built inserter when placement failed.
None of them asks the only question that makes demolition a strategy: *is this
part of the factory worse than what could stand in its place*. This module is
that question, and nothing else.

Method, as registered in ``docs/pesquisa-metodologia.md`` section 4: destroy a
large piece of the current solution and repair it with a strong method, because
a neighbourhood reachable by single-entity edits is too small to escape a local
optimum. The pieces here map one to one onto that section:

* *destroy operators* (plural, per ALNS) - :func:`identify_ruin_neighborhoods`;
* *repair* - rate-free reconnection routed by :func:`weighted_astar`;
* *acceptance including the cost of rebuilding* - :class:`RebuildCostModel` and
  :meth:`RebuildProposal.accepted`, which is the Rosenblatt-style question of
  whether the gain pays for the downtime, not only whether the result is better.

Purity is a requirement, not a style choice: the module takes the same entity
mappings the snapshot already carries, returns plain data, and never touches
RCON. Whoever controls a runner turns a :class:`RebuildProposal` into
``pickup_entity``/``place_entity`` calls; that binding is deliberately outside.

Stopping criterion
------------------
Ruin-and-recreate with no stopping rule is perpetual demolition: every factory
has some entity that could be somewhere else, so an unbounded loop keeps paying
downtime forever and converges to nothing. :func:`ruin_and_recreate` stops on
the first of four conditions, and the reason is always reported:

1. ``STOP_ROUND_BUDGET`` - ``policy.max_rounds`` rounds were spent. This is the
   only bound that holds unconditionally, and it is the one to trust;
2. ``STOP_NO_CANDIDATES`` - no destroy operator finds anything to ruin;
3. ``STOP_PROPOSAL_REJECTED`` - the best proposal does not pay for itself;
4. ``STOP_NO_CAPACITY_GAIN`` - ``policy.patience`` consecutive accepted rounds
   moved entities without raising capacity, which is churn, not progress.

Independently of the budget, the loop terminates because every accepted round
strictly decreases the pair ``(unconnected producers, belt count)`` in
lexicographic order. An accepted round needs ``gain > 0``; the capacity guard
forbids ``connected_producers`` from falling; so either the round raises
``connected_producers``, which is bounded above by the producer count and can
only move one way, or it leaves capacity untouched and its whole gain comes
from ``belts_removed - belts_placed``, which then has to be strictly positive.
Both components are bounded below. With ``saved_tile_value`` at zero the second
disjunct disappears and the first still holds.

That argument counts belt tiles, and it holds only while every conveyor a round
removes is a tile the repair can put back. A splitter is a fork and an
underground pair is a jump: replacing either with ``policy.belt_name`` tiles
deletes a branch while the ledger books a tile saved, which is a wrong number
and not merely a worse factory. So a neighbourhood that holds one is refused by
name - ``REJECT_UNREPLACEABLE_TRANSPORT`` - and :func:`_belt_runs` ends a run at
the first node with more than one successor instead of guessing which branch
continues.

The tabu list - every entity ruined or placed during the session - is a guard,
not the termination argument: it keeps a later round from undoing an earlier
one, which would be legal under the rules above whenever the two layouts have
equal value. :func:`plan_rebuild` takes it as a parameter so a caller that
drives its own loop keeps the same protection.

Complexity, against the structures these modules actually use:

* the destroy operators walk a ``dict[str, set[str]]`` adjacency built from the
  edge list returned at ``learning/factory_graph.py:454``, so one pass is
  O(V + E);
* each repair is a single :func:`weighted_astar` call whose open set is a heap
  over ``(tile, direction)`` states (``planning/astar.py:46`` builds the heap,
  ``planning/astar.py:49`` keys the best-cost table by that pair), so it is
  O(4T log 4T) for T tiles inside the routing box;
* scoring a candidate rebuilds the whole graph once, and that rebuild is
  quadratic in the worst case rather than linear: ``_nearest`` is a linear scan
  over the node list (``learning/factory_graph.py:232``) called once per belt,
  inserter and drill, and the pole/consumer pass is a double loop
  (``learning/factory_graph.py:399``). A round therefore costs
  O(K * (V^2 + 4T log 4T)) for K = ``policy.max_neighborhoods_per_round``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.learning.factory_graph import (
    BELT_NAMES,
    DIRECTION_VECTORS,
    MATERIAL_RELATIONS,
    SPLITTER_NAMES,
    UNDERGROUND_BELT_NAMES,
    build_factory_graph,
    node_id,
)
from factorio_ai_lab.planning.astar import (
    DEFAULT_ROUTING_WEIGHTS,
    RoutingWeights,
    weighted_astar,
)
from factorio_ai_lab.planning.footprints import (
    blocked_tiles,
    cardinal_direction,
    entity_tiles,
)

#: A producer sits where the ore is. The graph knows nothing about resource
#: tiles, so it can never justify moving one, and a processing entity that is
#: already fed is capacity someone paid for. Both are out of reach of every
#: destroy operator here; only the logistics glue between them is negotiable.
PROTECTED_CATEGORIES = frozenset(
    {"extraction", "processing", "research", "energy", "power", "buffer", "agent"}
)

#: What a destroy operator may remove: belts and inserters, whose position is a
#: planning decision and nothing else.
DEMOLISHABLE_CATEGORIES = frozenset({"transport", "transfer"})

#: Conveyors inside that category whose job a straight run of
#: ``policy.belt_name`` cannot do: a splitter feeds two lanes and an
#: underground pair crosses tiles the repair would have to leave clear. Both
#: are demolishable in the physical sense and neither is replaceable here.
UNREPLACEABLE_TRANSPORT_NAMES = SPLITTER_NAMES | UNDERGROUND_BELT_NAMES

#: Destroy operators.
#: A producer whose output reaches neither a processing entity nor a buffer.
#: Strictly broader than the `isolated_producers` metric, which only counts
#: producers with zero outgoing material edge: a drill feeding a belt that ends
#: in open ground is not isolated and is still producing nothing.
REASON_UNCONNECTED_PRODUCER = "unconnected_producer"
#: A belt run whose tile count is far above the shortest path between its own
#: endpoints. The endpoints are preserved; only the detour is paid for.
REASON_DETOUR_ROUTE = "detour_route"
#: An entity with no material edge at all, in or out. It occupies tiles, blocks
#: routing and carries nothing.
REASON_ORPHAN_ENTITY = "orphan_entity"

#: Why a proposal was refused.
REJECT_GAIN_BELOW_COST = "gain_below_cost"
REJECT_CAPACITY_REGRESSION = "capacity_regression"
REJECT_ROUTE_UNAVAILABLE = "route_unavailable"
#: The neighbourhood holds a conveyor this module cannot rebuild. Refusing it
#: by name is the only honest answer: pricing it would mean calling a splitter
#: one belt tile, and applying it would mean deleting a fork for free.
REJECT_UNREPLACEABLE_TRANSPORT = "unreplaceable_transport"

#: Why the loop stopped. See the module docstring.
STOP_ROUND_BUDGET = "round_budget_exhausted"
STOP_NO_CANDIDATES = "no_candidates_left"
STOP_PROPOSAL_REJECTED = "proposal_rejected"
STOP_NO_CAPACITY_GAIN = "no_capacity_gain"

#: Findings of :func:`detect_regression`.
REGRESSION_CAPACITY = "capacity_regression"
REGRESSION_PARTIAL_APPLICATION = "partial_application"
#: Returned when the observed payload carries no metric at all. Absence of a
#: measurement is not evidence of health, so it is never an empty finding set.
REGRESSION_UNMEASURED = "unmeasured"

#: Inverse of `DIRECTION_VECTORS`: the Factorio direction that faces a step.
DIRECTION_BY_VECTOR: dict[tuple[int, int], int] = {
    (int(vector[0]), int(vector[1])): direction
    for direction, vector in DIRECTION_VECTORS.items()
}

_CARDINAL_STEPS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
_TILE_EPSILON = 1e-6

#: How many handover slots of a target entity are tried before the repair is
#: declared unroutable. Bounded because each try is a full A* call.
_MAX_HANDOVER_TRIES = 4


@dataclass(frozen=True)
class RuinPolicy:
    """Bounds of one ruin-and-recreate session."""

    #: A run is a detour candidate above this ratio of actual to shortest tiles.
    detour_ratio: float = 1.6
    #: And only if the rebuild actually saves at least this many tiles.
    min_saved_tiles: int = 2
    #: Runs shorter than this cannot detour in any meaningful way.
    min_run_tiles: int = 3
    #: Hard cap on one proposal, so a single round can never level the factory.
    max_removals: int = 24
    #: How many destroy operators are scored per round.
    max_neighborhoods_per_round: int = 6
    #: How far outside the factory bounding box a repair route may wander.
    route_margin_tiles: int = 8
    #: Gain must beat cost by this margin. Mirrors the already-evolved
    #: `EvolutionGenome.rebuild_gain_threshold` default (learning/evolution.py:21).
    gain_margin: float = 0.10
    max_rounds: int = 4
    #: Accepted rounds tolerated in a row without a capacity gain.
    patience: int = 1
    belt_name: str = "transport-belt"
    inserter_name: str = "inserter"
    routing_weights: RoutingWeights = DEFAULT_ROUTING_WEIGHTS


@dataclass(frozen=True)
class RebuildCostModel:
    """Value of what a rebuild buys and what it costs.

    Every field is a policy parameter, not a measured Factorio constant. The
    defaults are deliberately conservative: they make a cosmetic reroute fail to
    pay for itself while a producer that starts feeding a furnace pays easily.
    Calibration belongs to whoever has measured rates, which is the runner.
    """

    seconds_per_removal: float = 1.0
    seconds_per_placement: float = 1.0
    #: Value lost per second the factory spends being rebuilt instead of running.
    downtime_value_per_s: float = 1.0
    #: Factorio returns a mined entity to the inventory, so material already on
    #: the ground offsets what the rebuild needs. Lower it to model losses.
    salvage_fraction: float = 1.0
    default_item_value: float = 1.0
    item_value: Mapping[str, float] = field(default_factory=dict)
    #: Value of one producer that starts reaching a processing entity.
    connected_producer_value: float = 60.0
    #: Value of one belt tile the factory no longer has to own or power.
    saved_tile_value: float = 0.5

    def value_of(self, item: str) -> float:
        return float(self.item_value.get(item, self.default_item_value))

    def material_shortfall(self, recovered: Mapping[str, int], needed: Mapping[str, int]) -> float:
        """Value of the items the rebuild needs beyond what demolition returns."""
        total = 0.0
        for item, count in needed.items():
            salvaged = self.salvage_fraction * float(recovered.get(item, 0))
            missing = max(0.0, float(count) - salvaged)
            total += missing * self.value_of(item)
        return total


DEFAULT_POLICY = RuinPolicy()
DEFAULT_COST_MODEL = RebuildCostModel()


@dataclass(frozen=True)
class FactoryCapacity:
    """The part of the graph a rebuild is forbidden to trade away."""

    connected_producers: int
    buffered_producers: int
    producer_count: int
    processing_nodes: int
    material_edges: int

    @property
    def coverage(self) -> float:
        if self.producer_count <= 0:
            return 0.0
        return self.connected_producers / self.producer_count

    def regressions_against(self, baseline: FactoryCapacity) -> tuple[str, ...]:
        """Name every axis on which this state is worse than ``baseline``.

        `physical_processing_coverage` is deliberately absent. It is a ratio
        whose denominator is the producer count, so demolishing the producers
        that are not connected raises it while the factory makes exactly as much
        as before. Guarding the absolute counts, producer count included, closes
        that door; the ratio stays available as a report.
        """
        findings: list[str] = []
        if self.connected_producers < baseline.connected_producers:
            findings.append(
                f"connected_producers {baseline.connected_producers}->"
                f"{self.connected_producers}"
            )
        if self.buffered_producers < baseline.buffered_producers:
            findings.append(
                f"buffered_producers {baseline.buffered_producers}->"
                f"{self.buffered_producers}"
            )
        if self.producer_count < baseline.producer_count:
            findings.append(
                f"producer_count {baseline.producer_count}->{self.producer_count}"
            )
        if self.processing_nodes < baseline.processing_nodes:
            findings.append(
                f"processing_nodes {baseline.processing_nodes}->{self.processing_nodes}"
            )
        return tuple(findings)

    def to_dict(self) -> dict[str, float]:
        return {
            "connected_producers": self.connected_producers,
            "buffered_producers": self.buffered_producers,
            "producer_count": self.producer_count,
            "processing_nodes": self.processing_nodes,
            "material_edges": self.material_edges,
            "coverage": round(self.coverage, 6),
        }


@dataclass(frozen=True)
class DemolitionCandidate:
    node_id: str
    name: str
    category: str
    x: float
    y: float
    direction: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "name": self.name,
            "category": self.category,
            "x": self.x,
            "y": self.y,
            "direction": self.direction,
        }


@dataclass(frozen=True)
class Placement:
    """One entity the rebuild wants on the ground."""

    name: str
    x: float
    y: float
    direction: int

    def to_entity(self, unit: int) -> dict[str, Any]:
        """Entity mapping shaped like the snapshot rows the graph reads.

        The status is asserted as ``working`` because the simulated post-state
        exists to answer a topology question. It is not evidence about fuel or
        power, and no starvation count may be read off it.
        """
        return {
            "name": self.name,
            "position": {"x": self.x, "y": self.y},
            "direction": self.direction,
            "unit_number": unit,
            "status": "working",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "direction": self.direction,
        }


@dataclass(frozen=True)
class RepairIntent:
    """Where the repair has to start and end for the chain to close again."""

    start: GridPoint
    goal: GridPoint
    #: Direction the last belt must face, so the entity beyond the goal keeps
    #: receiving. ``None`` means the repair terminates in its own inserter.
    goal_direction: int | None = None
    #: Tile and facing of the inserter that hands over to the target entity.
    inserter_tile: GridPoint | None = None
    inserter_direction: int | None = None


@dataclass(frozen=True)
class RuinNeighborhood:
    """One destroy operator instance: what to remove and what to repair."""

    reason: str
    removals: tuple[DemolitionCandidate, ...]
    severity: float
    repair: RepairIntent | None = None
    source_id: str | None = None
    target_id: str | None = None

    @property
    def sort_key(self) -> tuple[float, str, str]:
        anchor = self.source_id or (self.removals[0].node_id if self.removals else "")
        return (-self.severity, self.reason, anchor)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "severity": self.severity,
            "removals": [row.to_dict() for row in self.removals],
            "source_id": self.source_id,
            "target_id": self.target_id,
        }


@dataclass(frozen=True)
class RebuildRoute:
    start: GridPoint
    goal: GridPoint
    path: tuple[GridPoint, ...]
    cost: float
    expanded_nodes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": [self.start.x, self.start.y],
            "goal": [self.goal.x, self.goal.y],
            "path": [[point.x, point.y] for point in self.path],
            "cost": round(self.cost, 6),
            "expanded_nodes": self.expanded_nodes,
        }


@dataclass(frozen=True)
class RebuildProposal:
    """A demolition with its price, its payoff and its undo."""

    reason: str
    removals: tuple[DemolitionCandidate, ...]
    placements: tuple[Placement, ...]
    routes: tuple[RebuildRoute, ...]
    baseline: FactoryCapacity
    projected: FactoryCapacity
    estimated_gain: float
    estimated_cost: float
    downtime_s: float
    regressions: tuple[str, ...]
    accepted: bool
    rejection: str | None
    #: Node count the graph must report once the proposal is fully applied. A
    #: run that dies halfway shows a different count, which is what makes the
    #: half-applied state detectable instead of silent.
    expected_node_count: int
    #: Everything demolished, in the shape needed to put it back.
    restoration: tuple[Placement, ...]

    @property
    def net_value(self) -> float:
        return self.estimated_gain - self.estimated_cost

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "accepted": self.accepted,
            "rejection": self.rejection,
            "estimated_gain": round(self.estimated_gain, 6),
            "estimated_cost": round(self.estimated_cost, 6),
            "net_value": round(self.net_value, 6),
            "downtime_s": round(self.downtime_s, 6),
            "regressions": list(self.regressions),
            "expected_node_count": self.expected_node_count,
            "baseline": self.baseline.to_dict(),
            "projected": self.projected.to_dict(),
            "removals": [row.to_dict() for row in self.removals],
            "placements": [row.to_dict() for row in self.placements],
            "restoration": [row.to_dict() for row in self.restoration],
            "routes": [row.to_dict() for row in self.routes],
        }


@dataclass(frozen=True)
class RuinRecreateRound:
    index: int
    proposal: RebuildProposal
    applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "applied": self.applied,
            "proposal": self.proposal.to_dict(),
        }


@dataclass(frozen=True)
class RuinRecreateResult:
    rounds: tuple[RuinRecreateRound, ...]
    entities: tuple[Mapping[str, Any], ...]
    initial: FactoryCapacity
    final: FactoryCapacity
    stop_reason: str
    tabu: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "initial": self.initial.to_dict(),
            "final": self.final.to_dict(),
            "rounds": [row.to_dict() for row in self.rounds],
            "tabu": list(self.tabu),
        }


def measure_capacity(graph: Mapping[str, Any]) -> FactoryCapacity:
    """Read the guarded axes off a graph produced by `build_factory_graph`."""
    metrics = _metrics_of(graph)
    categories = metrics.get("categories")
    processing = 0
    if isinstance(categories, Mapping):
        processing = _as_int(categories.get("processing"))
    return FactoryCapacity(
        connected_producers=_as_int(metrics.get("producers_reaching_processor")),
        buffered_producers=_as_int(metrics.get("producers_reaching_buffer")),
        producer_count=_as_int(metrics.get("producer_count")),
        processing_nodes=processing,
        material_edges=_as_int(metrics.get("material_edge_count")),
    )


def identify_ruin_neighborhoods(
    graph: Mapping[str, Any],
    *,
    policy: RuinPolicy = DEFAULT_POLICY,
    tabu: Iterable[str] = (),
) -> tuple[RuinNeighborhood, ...]:
    """Subsets of the factory whose removal most plausibly improves it.

    Ordered by severity, highest first, so a caller that can only afford one
    demolition spends it on lost capacity rather than on tidiness.
    """
    forbidden = frozenset(tabu)
    nodes = {row["id"]: row for row in _nodes_of(graph)}
    forward, reverse = _material_adjacency(graph)

    processors = {i for i, row in nodes.items() if row["category"] == "processing"}
    # Everything a material chain can legitimately end in. A buffer and a lab
    # are worse endings than a furnace, but a producer feeding either is doing
    # something, and the capacity guard below cannot see lab consumption at all:
    # excluding them here is what keeps the operator from quietly cutting a
    # chain no metric would miss.
    sinks = processors | {
        i
        for i, row in nodes.items()
        if row["category"] in {"buffer", "research"}
    }
    producers = sorted(i for i, row in nodes.items() if row["category"] == "extraction")

    found: list[RuinNeighborhood] = []
    claimed: set[str] = set()

    # Operator 1: producers whose output reaches nothing that consumes it. The
    # dead branch hanging off such a producer is safe to remove without any
    # further check: reachability is transitive, so if any node downstream of it
    # could reach a sink, the producer would reach that sink too.
    for producer in producers:
        downstream = _descendants(forward, producer)
        if downstream & sinks:
            continue
        target = _nearest_node(nodes, producer, processors)
        if target is None:
            continue
        removals = _candidates(nodes, sorted(downstream), forbidden, claimed)
        if len(removals) > policy.max_removals:
            removals = removals[: policy.max_removals]
        claimed.update(row.node_id for row in removals)
        found.append(
            RuinNeighborhood(
                reason=REASON_UNCONNECTED_PRODUCER,
                removals=removals,
                severity=3.0,
                source_id=producer,
                target_id=target,
            )
        )

    # Operator 2: belt runs that pay for a detour their own endpoints do not
    # require. A run that closes into a loop has no head and is skipped: an
    # undirected cycle needs an operator of its own, and guessing here would
    # remove belts whose replacement this module cannot plan.
    for run in _belt_runs(graph, nodes):
        if len(run) < policy.min_run_tiles:
            continue
        if any(i in forbidden or i in claimed for i in run):
            continue
        head = _tile_of(nodes[run[0]])
        tail = _tile_of(nodes[run[-1]])
        shortest = head.manhattan(tail) + 1
        if shortest <= 0 or len(run) / shortest <= policy.detour_ratio:
            continue
        if len(run) - shortest < policy.min_saved_tiles:
            continue
        removals = _candidates(nodes, list(run), forbidden, claimed)
        if len(removals) != len(run) or len(removals) > policy.max_removals:
            continue
        claimed.update(row.node_id for row in removals)
        found.append(
            RuinNeighborhood(
                reason=REASON_DETOUR_ROUTE,
                removals=removals,
                severity=2.0,
                repair=RepairIntent(
                    start=head,
                    goal=tail,
                    goal_direction=_direction_of(nodes[run[-1]]),
                ),
                source_id=run[0],
            )
        )

    # Operator 3: logistics entities that carry nothing at all.
    for identifier in sorted(nodes):
        row = nodes[identifier]
        if row["category"] not in DEMOLISHABLE_CATEGORIES:
            continue
        if identifier in forbidden or identifier in claimed:
            continue
        if forward.get(identifier) or reverse.get(identifier):
            continue
        claimed.add(identifier)
        found.append(
            RuinNeighborhood(
                reason=REASON_ORPHAN_ENTITY,
                removals=_candidates(nodes, [identifier], forbidden, set()),
                severity=1.0,
                source_id=identifier,
            )
        )

    return tuple(sorted(found, key=lambda row: row.sort_key))


def plan_rebuild(
    entities: Sequence[Mapping[str, Any]],
    *,
    policy: RuinPolicy = DEFAULT_POLICY,
    cost_model: RebuildCostModel = DEFAULT_COST_MODEL,
    footprints: Mapping[str, tuple[int, int]] | None = None,
    tabu: Iterable[str] = (),
) -> RebuildProposal | None:
    """Best scored demolition-plus-rebuild for one snapshot, or None.

    None means no destroy operator found anything: that is the difference
    between "nothing to do" and "everything on offer was refused", and the
    caller needs both to report a stopping reason honestly.
    """
    graph = build_factory_graph(entities)
    baseline = measure_capacity(graph)
    neighborhoods = identify_ruin_neighborhoods(graph, policy=policy, tabu=tabu)
    if not neighborhoods:
        return None

    by_id = {
        node_id(row, index): row
        for index, row in enumerate(entities)
        if isinstance(row, Mapping)
    }
    nodes = {row["id"]: row for row in _nodes_of(graph)}

    scored: list[RebuildProposal] = []
    for neighborhood in neighborhoods[: policy.max_neighborhoods_per_round]:
        if any(
            row.name in UNREPLACEABLE_TRANSPORT_NAMES
            for row in neighborhood.removals
        ):
            scored.append(_refused_proposal(neighborhood, baseline, len(nodes)))
            continue
        proposal = _evaluate(
            entities,
            by_id,
            neighborhood,
            baseline,
            policy=policy,
            cost_model=cost_model,
            footprints=footprints,
        )
        if proposal is not None:
            scored.append(proposal)

    if not scored:
        return _barren_proposal(neighborhoods[0].reason, baseline, len(nodes))

    accepted = [row for row in scored if row.accepted]
    if accepted:
        return max(accepted, key=lambda row: (row.net_value, -len(row.removals)))
    return scored[0]


def apply_proposal(
    entities: Sequence[Mapping[str, Any]],
    proposal: RebuildProposal,
) -> list[Mapping[str, Any]]:
    """Snapshot the factory would show if the proposal ran to completion."""
    removed = {row.node_id for row in proposal.removals}
    survivors = [
        row
        for index, row in enumerate(entities)
        if not isinstance(row, Mapping) or node_id(row, index) not in removed
    ]
    unit = _next_unit_number(entities)
    for placement in proposal.placements:
        survivors.append(placement.to_entity(unit))
        unit += 1
    return survivors


def detect_regression(
    proposal: RebuildProposal,
    observed: Mapping[str, Any],
) -> tuple[str, ...]:
    """Compare what the world reports after the attempt with what was promised.

    Returns every finding, empty when the observed state matches the plan. An
    empty result from a payload that measured nothing would be a false
    all-clear, so a payload with no metrics answers ``(REGRESSION_UNMEASURED,)``
    instead.

    The node-count check is what makes a rebuild that died halfway visible:
    capacity alone stays quiet whenever the baseline was already zero, which is
    exactly the case the unconnected-producer operator works on.
    """
    metrics = _metrics_of(observed)
    if "producers_reaching_processor" not in metrics and "node_count" not in metrics:
        return (REGRESSION_UNMEASURED,)

    findings: list[str] = []
    capacity = measure_capacity(observed)
    for row in capacity.regressions_against(proposal.baseline):
        findings.append(f"{REGRESSION_CAPACITY}:{row}")

    node_count = metrics.get("node_count")
    if isinstance(node_count, (int, float)) and int(node_count) != proposal.expected_node_count:
        findings.append(
            f"{REGRESSION_PARTIAL_APPLICATION}:"
            f"{proposal.expected_node_count}!={int(node_count)}"
        )
    return tuple(findings)


def ruin_and_recreate(
    entities: Sequence[Mapping[str, Any]],
    *,
    policy: RuinPolicy = DEFAULT_POLICY,
    cost_model: RebuildCostModel = DEFAULT_COST_MODEL,
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> RuinRecreateResult:
    """Run bounded rounds of destroy-and-repair over a snapshot.

    Pure: it simulates the accepted proposals against `build_factory_graph`
    rather than executing them, which is what lets the stopping rules be tested
    without a game running. See the module docstring for the five conditions.
    """
    current: list[Mapping[str, Any]] = list(entities)
    initial = measure_capacity(build_factory_graph(current))
    capacity = initial
    rounds: list[RuinRecreateRound] = []
    tabu: set[str] = set()
    stalled = 0
    stop_reason = STOP_ROUND_BUDGET

    for index in range(max(0, policy.max_rounds)):
        proposal = plan_rebuild(
            current,
            policy=policy,
            cost_model=cost_model,
            footprints=footprints,
            tabu=tabu,
        )
        if proposal is None:
            stop_reason = STOP_NO_CANDIDATES
            break
        if not proposal.accepted:
            rounds.append(RuinRecreateRound(index, proposal, applied=False))
            stop_reason = STOP_PROPOSAL_REJECTED
            break

        applied = apply_proposal(current, proposal)
        # The tabu covers what was placed as well as what was removed, so a
        # later round cannot undo an earlier one by calling its fresh belt a
        # detour. See the module docstring: this is a guard, and the bound that
        # holds unconditionally is the round budget.
        tabu.update(row.node_id for row in proposal.removals)
        tabu.update(_placed_ids(current, proposal))
        current = applied
        rounds.append(RuinRecreateRound(index, proposal, applied=True))

        if proposal.projected.connected_producers <= capacity.connected_producers:
            stalled += 1
        else:
            stalled = 0
        capacity = proposal.projected
        if stalled > max(0, policy.patience):
            stop_reason = STOP_NO_CAPACITY_GAIN
            break

    return RuinRecreateResult(
        rounds=tuple(rounds),
        entities=tuple(current),
        initial=initial,
        final=measure_capacity(build_factory_graph(current)),
        stop_reason=stop_reason,
        tabu=tuple(sorted(tabu)),
    )


def _evaluate(
    entities: Sequence[Mapping[str, Any]],
    by_id: Mapping[str, Mapping[str, Any]],
    neighborhood: RuinNeighborhood,
    baseline: FactoryCapacity,
    *,
    policy: RuinPolicy,
    cost_model: RebuildCostModel,
    footprints: Mapping[str, tuple[int, int]] | None,
) -> RebuildProposal | None:
    """Price one neighborhood; None when its repair cannot be routed."""
    removed_ids = {row.node_id for row in neighborhood.removals}
    survivors = [
        row
        for index, row in enumerate(entities)
        if not isinstance(row, Mapping) or node_id(row, index) not in removed_ids
    ]
    blocked = blocked_tiles(survivors, footprints)

    if neighborhood.repair is not None:
        intents = [neighborhood.repair]
    elif neighborhood.reason == REASON_UNCONNECTED_PRODUCER:
        intents = _connect_intents(
            by_id,
            neighborhood,
            blocked=blocked,
            footprints=footprints,
        )
        if not intents:
            return None
    else:
        intents = []

    placements: tuple[Placement, ...] = ()
    routes: tuple[RebuildRoute, ...] = ()
    if intents:
        built = None
        # One blocked side of a furnace is not a reason to leave a producer
        # disconnected: try the next handover slot before giving up.
        for intent in intents[:_MAX_HANDOVER_TRIES]:
            built = _route_repair(
                intent,
                survivors,
                blocked=blocked,
                policy=policy,
                footprints=footprints,
            )
            if built is not None:
                break
        if built is None:
            return None
        placements, routes = built
        if (
            neighborhood.reason == REASON_DETOUR_ROUTE
            and len(neighborhood.removals) - len(placements) < policy.min_saved_tiles
        ):
            return None

    draft = RebuildProposal(
        reason=neighborhood.reason,
        removals=neighborhood.removals,
        placements=placements,
        routes=routes,
        baseline=baseline,
        projected=baseline,
        estimated_gain=0.0,
        estimated_cost=0.0,
        downtime_s=0.0,
        regressions=(),
        accepted=False,
        rejection=None,
        expected_node_count=0,
        restoration=tuple(
            Placement(row.name, row.x, row.y, row.direction)
            for row in neighborhood.removals
        ),
    )
    after = apply_proposal(entities, draft)
    graph_after = build_factory_graph(after)
    projected = measure_capacity(graph_after)
    regressions = projected.regressions_against(baseline)

    belts_removed = sum(
        1 for row in neighborhood.removals if row.category == "transport"
    )
    belts_placed = sum(1 for row in placements if row.name == policy.belt_name)
    gain = (
        (projected.connected_producers - baseline.connected_producers)
        * cost_model.connected_producer_value
        + (belts_removed - belts_placed) * cost_model.saved_tile_value
    )

    downtime_s = (
        len(neighborhood.removals) * cost_model.seconds_per_removal
        + len(placements) * cost_model.seconds_per_placement
    )
    recovered = Counter(row.name for row in neighborhood.removals)
    needed = Counter(row.name for row in placements)
    cost = (
        downtime_s * cost_model.downtime_value_per_s
        + cost_model.material_shortfall(recovered, needed)
    )

    pays = gain > 0.0 and gain >= cost * (1.0 + max(0.0, policy.gain_margin))
    rejection: str | None = None
    if regressions:
        rejection = REJECT_CAPACITY_REGRESSION
    elif not pays:
        rejection = REJECT_GAIN_BELOW_COST

    node_count = _as_int(_metrics_of(graph_after).get("node_count"))
    return RebuildProposal(
        reason=neighborhood.reason,
        removals=neighborhood.removals,
        placements=placements,
        routes=routes,
        baseline=baseline,
        projected=projected,
        estimated_gain=gain,
        estimated_cost=cost,
        downtime_s=downtime_s,
        regressions=regressions,
        accepted=rejection is None,
        rejection=rejection,
        expected_node_count=node_count,
        restoration=draft.restoration,
    )


def _connect_intents(
    by_id: Mapping[str, Mapping[str, Any]],
    neighborhood: RuinNeighborhood,
    *,
    blocked: set[GridPoint],
    footprints: Mapping[str, tuple[int, int]] | None,
) -> list[RepairIntent]:
    """Where the belt must start and where its inserter may stand.

    Ranked by how far the handover slot is from the producer, nearest first.
    """
    source = by_id.get(neighborhood.source_id or "")
    target = by_id.get(neighborhood.target_id or "")
    if source is None or target is None:
        return []
    start = _front_tile(source, footprints)
    if start is None or start in blocked:
        return []

    ranked: list[tuple[tuple[int, int, int], RepairIntent]] = []
    for inserter_tile, approach, direction in _handover_slots(target, footprints):
        if inserter_tile in blocked or approach in blocked:
            continue
        ranked.append(
            (
                (start.manhattan(approach), approach.x, approach.y),
                RepairIntent(
                    start=start,
                    goal=approach,
                    inserter_tile=inserter_tile,
                    inserter_direction=direction,
                ),
            )
        )
    ranked.sort(key=lambda row: row[0])
    return [row[1] for row in ranked]


def _handover_slots(
    target: Mapping[str, Any],
    footprints: Mapping[str, tuple[int, int]] | None,
) -> list[tuple[GridPoint, GridPoint, int]]:
    """Inserter tile, belt approach tile and inserter facing, per target side.

    The approach tile is one step further out than the inserter so the last belt
    lands where `build_factory_graph` looks for an inserter's pickup: directly
    opposite its drop side.
    """
    tiles = entity_tiles(target, footprints)
    slots: list[tuple[GridPoint, GridPoint, int]] = []
    for tile in sorted(tiles, key=lambda point: (point.x, point.y)):
        for step in _CARDINAL_STEPS:
            outside = GridPoint(tile.x + step[0], tile.y + step[1])
            if outside in tiles:
                continue
            approach = GridPoint(outside.x + step[0], outside.y + step[1])
            if approach in tiles:
                continue
            facing = DIRECTION_BY_VECTOR.get((-step[0], -step[1]))
            if facing is None:
                continue
            slots.append((outside, approach, facing))
    return slots


def _route_repair(
    intent: RepairIntent,
    survivors: Sequence[Mapping[str, Any]],
    *,
    blocked: set[GridPoint],
    policy: RuinPolicy,
    footprints: Mapping[str, tuple[int, int]] | None,
) -> tuple[tuple[Placement, ...], tuple[RebuildRoute, ...]] | None:
    """Route the belt with A* and turn the path into placements."""
    obstacles = set(blocked)
    if intent.inserter_tile is not None:
        # Reserve the handover tile: a belt routed through it would leave the
        # inserter nowhere to stand.
        obstacles.add(intent.inserter_tile)
    obstacles.discard(intent.start)
    if intent.goal in obstacles:
        return None

    in_bounds = _routing_box(
        survivors,
        (intent.start, intent.goal),
        margin=policy.route_margin_tiles,
        footprints=footprints,
    )
    route = weighted_astar(
        intent.start,
        intent.goal,
        is_blocked=obstacles.__contains__,
        in_bounds=in_bounds,
        weights=policy.routing_weights,
    )
    if route is None:
        return None

    chain = list(route.path)
    tail_direction = intent.goal_direction
    if intent.inserter_tile is not None:
        chain.append(intent.inserter_tile)
    elif tail_direction is None:
        return None

    placements: list[Placement] = []
    for index, tile in enumerate(route.path):
        if index + 1 < len(chain):
            nxt = chain[index + 1]
            direction = DIRECTION_BY_VECTOR.get((nxt.x - tile.x, nxt.y - tile.y))
        else:
            direction = tail_direction
        if direction is None:
            return None
        placements.append(
            Placement(policy.belt_name, tile.x + 0.5, tile.y + 0.5, direction)
        )

    if intent.inserter_tile is not None and intent.inserter_direction is not None:
        placements.append(
            Placement(
                policy.inserter_name,
                intent.inserter_tile.x + 0.5,
                intent.inserter_tile.y + 0.5,
                intent.inserter_direction,
            )
        )

    built = RebuildRoute(
        start=intent.start,
        goal=intent.goal,
        path=route.path,
        cost=route.cost,
        expanded_nodes=route.expanded_nodes,
    )
    return tuple(placements), (built,)


def _routing_box(
    survivors: Sequence[Mapping[str, Any]],
    anchors: Iterable[GridPoint],
    *,
    margin: int,
    footprints: Mapping[str, tuple[int, int]] | None,
) -> Callable[[GridPoint], bool]:
    """Bounding box of the factory plus a margin, as an A* `in_bounds`."""
    points = list(anchors)
    for row in survivors:
        if isinstance(row, Mapping):
            points.extend(entity_tiles(row, footprints))
    if not points:
        return lambda point: True
    margin = max(0, margin)
    min_x = min(point.x for point in points) - margin
    max_x = max(point.x for point in points) + margin
    min_y = min(point.y for point in points) - margin
    max_y = max(point.y for point in points) + margin

    def predicate(point: GridPoint) -> bool:
        return min_x <= point.x <= max_x and min_y <= point.y <= max_y

    return predicate


def _barren_proposal(
    reason: str,
    baseline: FactoryCapacity,
    node_count: int,
) -> RebuildProposal:
    """Something was worth ruining and no repair could be routed for it."""
    return RebuildProposal(
        reason=reason,
        removals=(),
        placements=(),
        routes=(),
        baseline=baseline,
        projected=baseline,
        estimated_gain=0.0,
        estimated_cost=0.0,
        downtime_s=0.0,
        regressions=(),
        accepted=False,
        rejection=REJECT_ROUTE_UNAVAILABLE,
        expected_node_count=node_count,
        restoration=(),
    )


def _refused_proposal(
    neighborhood: RuinNeighborhood,
    baseline: FactoryCapacity,
    node_count: int,
) -> RebuildProposal:
    """Something was worth ruining and this module cannot put it back.

    Carries no removal on purpose: a caller that applies every proposal it
    receives must not be able to demolish a fork on the strength of a refusal.
    """
    return RebuildProposal(
        reason=neighborhood.reason,
        removals=(),
        placements=(),
        routes=(),
        baseline=baseline,
        projected=baseline,
        estimated_gain=0.0,
        estimated_cost=0.0,
        downtime_s=0.0,
        regressions=(),
        accepted=False,
        rejection=REJECT_UNREPLACEABLE_TRANSPORT,
        expected_node_count=node_count,
        restoration=(),
    )


def _candidates(
    nodes: Mapping[str, Mapping[str, Any]],
    identifiers: Iterable[str],
    forbidden: frozenset[str],
    claimed: set[str],
) -> tuple[DemolitionCandidate, ...]:
    rows: list[DemolitionCandidate] = []
    for identifier in identifiers:
        row = nodes.get(identifier)
        if row is None:
            continue
        if identifier in forbidden or identifier in claimed:
            continue
        if row["category"] not in DEMOLISHABLE_CATEGORIES:
            continue
        rows.append(
            DemolitionCandidate(
                node_id=identifier,
                name=str(row["name"]),
                category=str(row["category"]),
                x=float(row["x"]),
                y=float(row["y"]),
                direction=_direction_of(row),
            )
        )
    return tuple(rows)


def _material_adjacency(
    graph: Mapping[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    forward: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = {}
    for edge in _edges_of(graph):
        if edge.get("relation") not in MATERIAL_RELATIONS:
            continue
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        forward.setdefault(source, set()).add(target)
        reverse.setdefault(target, set()).add(source)
    return forward, reverse


def _descendants(forward: Mapping[str, set[str]], source: str) -> set[str]:
    seen: set[str] = set()
    stack = list(forward.get(source, ()))
    while stack:
        current = stack.pop()
        if current in seen or current == source:
            continue
        seen.add(current)
        stack.extend(forward.get(current, ()))
    return seen


def _belt_runs(
    graph: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, ...]]:
    """Linear chains of plain belt tiles, as the detour operator can rebuild them.

    Two limits are deliberate, and both exist so the tile count a run reports
    is a count some physical layout actually has:

    * flow forks. A node with more than one successor ends the walk, because
      following one branch would report the length of a line that nobody
      built. Storing a single successor per node did exactly that silently;
    * only plain belts are members. The repair routes ``policy.belt_name``
      tiles, so a splitter or an underground end inside a run would be priced
      as a tile and rebuilt as something else.

    A belt fed by a splitter or by an underground exit still heads a run: what
    disqualifies a head is a plain-belt predecessor, not any predecessor.
    """
    successors: dict[str, set[str]] = {}
    predecessors: dict[str, set[str]] = {}
    for edge in _edges_of(graph):
        if edge.get("relation") != "belt_flow":
            continue
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        successors.setdefault(source, set()).add(target)
        predecessors.setdefault(target, set()).add(source)

    def plain_belt(identifier: str) -> bool:
        row = nodes.get(identifier)
        return row is not None and str(row.get("name")) in BELT_NAMES

    runs: list[tuple[str, ...]] = []
    for identifier in sorted(nodes):
        if not plain_belt(identifier):
            continue
        if any(plain_belt(row) for row in predecessors.get(identifier, ())):
            continue
        chain: list[str] = []
        seen: set[str] = set()
        cursor: str | None = identifier
        while cursor is not None and cursor not in seen and plain_belt(cursor):
            seen.add(cursor)
            chain.append(cursor)
            forward = successors.get(cursor, frozenset())
            cursor = next(iter(forward)) if len(forward) == 1 else None
        runs.append(tuple(chain))
    return runs


def _nearest_node(
    nodes: Mapping[str, Mapping[str, Any]],
    source: str,
    targets: Iterable[str],
) -> str | None:
    origin = nodes.get(source)
    if origin is None:
        return None
    best: str | None = None
    best_key: tuple[float, str] | None = None
    for identifier in targets:
        row = nodes.get(identifier)
        if row is None:
            continue
        distance = math.hypot(
            float(row["x"]) - float(origin["x"]),
            float(row["y"]) - float(origin["y"]),
        )
        key = (distance, identifier)
        if best_key is None or key < best_key:
            best_key = key
            best = identifier
    return best


def _front_tile(
    entity: Mapping[str, Any],
    footprints: Mapping[str, tuple[int, int]] | None,
) -> GridPoint | None:
    """Tile the entity outputs into, given its footprint and facing."""
    tiles = entity_tiles(entity, footprints)
    if not tiles:
        return None
    vector = DIRECTION_VECTORS[cardinal_direction(entity.get("direction"))]
    step = (int(vector[0]), int(vector[1]))
    front = {GridPoint(tile.x + step[0], tile.y + step[1]) for tile in tiles} - tiles
    if not front:
        return None
    centre_x = sum(tile.x for tile in tiles) / len(tiles)
    centre_y = sum(tile.y for tile in tiles) / len(tiles)

    def alignment(point: GridPoint) -> tuple[float, int, int]:
        offset = abs(point.x - centre_x) if step[0] == 0 else abs(point.y - centre_y)
        return (offset, point.x, point.y)

    return min(front, key=alignment)


def _placed_ids(
    entities: Sequence[Mapping[str, Any]],
    proposal: RebuildProposal,
) -> set[str]:
    """Identifiers `apply_proposal` will give the entities it places."""
    unit = _next_unit_number(entities)
    identifiers: set[str] = set()
    for _ in proposal.placements:
        identifiers.add(f"u{unit}")
        unit += 1
    return identifiers


def _next_unit_number(entities: Sequence[Mapping[str, Any]]) -> int:
    highest = 0
    for row in entities:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("unit_number", row.get("entity_number"))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        highest = max(highest, int(raw))
    return highest + 1


def _tile_of(node: Mapping[str, Any]) -> GridPoint:
    """Tile under a 1x1 node, whose centre the snapshot reports at ``t + 0.5``."""
    return GridPoint(
        math.floor(float(node["x"]) + _TILE_EPSILON),
        math.floor(float(node["y"]) + _TILE_EPSILON),
    )


def _direction_of(node: Mapping[str, Any]) -> int:
    return cardinal_direction(node.get("direction"))


def _nodes_of(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = graph.get("nodes") if isinstance(graph, Mapping) else None
    if not isinstance(rows, Sequence):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _edges_of(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = graph.get("edges") if isinstance(graph, Mapping) else None
    if not isinstance(rows, Sequence):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _metrics_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    metrics = payload.get("metrics")
    if isinstance(metrics, Mapping):
        return metrics
    return payload


def _as_int(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    return int(raw)
