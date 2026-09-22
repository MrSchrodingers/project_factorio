from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_DIRECTION_VECTORS = {
    0: (0.0, -1.0),
    4: (1.0, 0.0),
    8: (0.0, 1.0),
    12: (-1.0, 0.0),
}
_BELTS = {"transport-belt", "fast-transport-belt", "express-transport-belt"}
_INSERTERS = {
    "burner-inserter",
    "inserter",
    "fast-inserter",
    "long-handed-inserter",
    "bulk-inserter",
}
_DRILLS = {"burner-mining-drill", "electric-mining-drill"}
_PROCESSORS = {
    "stone-furnace",
    "steel-furnace",
    "electric-furnace",
    "assembling-machine-1",
    "assembling-machine-2",
    "assembling-machine-3",
    "chemical-plant",
    "oil-refinery",
    "centrifuge",
}
_BUFFERS = {
    "wooden-chest",
    "iron-chest",
    "steel-chest",
    "passive-provider-chest",
    "active-provider-chest",
    "storage-chest",
    "buffer-chest",
    "requester-chest",
}
_POLES = {
    "small-electric-pole",
    "medium-electric-pole",
    "big-electric-pole",
    "substation",
}
_POWER_CONSUMERS = _PROCESSORS | {"lab", "electric-mining-drill"} | _INSERTERS
_FLUID_TRANSPORT = {"pipe", "pipe-to-ground", "pump"}
_FLUID_ENDPOINTS = {"offshore-pump", "boiler", "steam-engine", "steam-turbine"}


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    name: str
    category: str
    x: float
    y: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "name": self.name,
            "category": self.category,
            "x": self.x,
            "y": self.y,
            "status": self.status,
        }


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
        }


def _position(entity: Mapping[str, Any]) -> tuple[float, float] | None:
    raw = entity.get("position")
    if not isinstance(raw, Mapping):
        return None
    try:
        return float(raw["x"]), float(raw["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _cardinal_direction(raw: Any) -> int:
    try:
        direction = int(raw or 0) % 16
    except (TypeError, ValueError):
        return 0
    return min(
        _DIRECTION_VECTORS,
        key=lambda value: min(abs(direction - value), 16 - abs(direction - value)),
    )


def _category(name: str) -> str:
    if name in _DRILLS:
        return "extraction"
    if name in _BELTS:
        return "transport"
    if name in _INSERTERS:
        return "transfer"
    if name in _PROCESSORS:
        return "processing"
    if name in _BUFFERS:
        return "buffer"
    if name in _POLES:
        return "power"
    if name in _FLUID_TRANSPORT:
        return "fluid_transport"
    if name in _FLUID_ENDPOINTS:
        return "energy"
    if name == "lab":
        return "research"
    if name == "character":
        return "agent"
    return "other"


def _distance(a: GraphNode, b: GraphNode) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _nearest(
    nodes: Sequence[GraphNode],
    x: float,
    y: float,
    *,
    exclude: str,
    radius: float,
    categories: set[str] | None = None,
) -> GraphNode | None:
    eligible = [
        node
        for node in nodes
        if node.node_id != exclude
        and (categories is None or node.category in categories)
        and math.hypot(node.x - x, node.y - y) <= radius
    ]
    return min(
        eligible,
        key=lambda node: math.hypot(node.x - x, node.y - y),
        default=None,
    )


def _reachable(
    adjacency: Mapping[str, set[str]],
    source: str,
    targets: set[str],
) -> bool:
    if source in targets:
        return True
    queue: deque[str] = deque([source])
    seen = {source}
    while queue:
        current = queue.popleft()
        for nxt in adjacency.get(current, set()):
            if nxt in targets:
                return True
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return False


def build_factory_graph(
    entities: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    nodes: list[GraphNode] = []
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for index, entity in enumerate(entities):
        if not isinstance(entity, Mapping):
            continue
        pos = _position(entity)
        if pos is None:
            continue
        unit = entity.get("unit_number", entity.get("entity_number"))
        node_id = f"u{unit}" if isinstance(unit, (int, float)) else f"i{index}"
        name = str(entity.get("name", "entity"))
        node = GraphNode(
            node_id=node_id,
            name=name,
            category=_category(name),
            x=pos[0],
            y=pos[1],
            status=str(entity.get("status") or "unknown").strip('"'),
        )
        nodes.append(node)
        raw_by_id[node_id] = entity

    edges: set[GraphEdge] = set()
    # Directional conveyor adjacency.
    belt_nodes = [node for node in nodes if node.category == "transport"]
    for source in belt_nodes:
        raw = raw_by_id[source.node_id]
        direction = _cardinal_direction(raw.get("direction"))
        dx, dy = _DIRECTION_VECTORS[direction]
        target = _nearest(
            belt_nodes,
            source.x + dx,
            source.y + dy,
            exclude=source.node_id,
            radius=0.8,
        )
        if target is not None:
            edges.add(GraphEdge(source.node_id, target.node_id, "belt_flow"))

    # Inserters define explicit pickup -> transfer -> drop relationships.
    for inserter in (node for node in nodes if node.category == "transfer"):
        raw = raw_by_id[inserter.node_id]
        direction = _cardinal_direction(raw.get("direction"))
        dx, dy = _DIRECTION_VECTORS[direction]
        reach = 2.0 if inserter.name == "long-handed-inserter" else 1.15
        pickup = _nearest(
            nodes,
            inserter.x - dx * reach,
            inserter.y - dy * reach,
            exclude=inserter.node_id,
            radius=1.15,
            categories={"extraction", "transport", "processing", "buffer"},
        )
        drop = _nearest(
            nodes,
            inserter.x + dx * reach,
            inserter.y + dy * reach,
            exclude=inserter.node_id,
            radius=1.15,
            categories={"transport", "processing", "buffer", "research"},
        )
        if pickup is not None:
            edges.add(GraphEdge(pickup.node_id, inserter.node_id, "pickup"))
        if drop is not None:
            edges.add(GraphEdge(inserter.node_id, drop.node_id, "drop"))

    # Mining drills emit into the tile in front of their output direction.
    for drill in (node for node in nodes if node.category == "extraction"):
        raw = raw_by_id[drill.node_id]
        direction = _cardinal_direction(raw.get("direction"))
        dx, dy = _DIRECTION_VECTORS[direction]
        target = _nearest(
            nodes,
            drill.x + dx * 1.5,
            drill.y + dy * 1.5,
            exclude=drill.node_id,
            radius=1.8,
            categories={"transport", "buffer", "processing"},
        )
        if target is not None:
            edges.add(GraphEdge(drill.node_id, target.node_id, "material_output"))

    # Fluid/steam topology. Pipes are undirected transport elements; large
    # energy endpoints use a wider proximity because their connection point
    # can be multiple tiles from the entity center.
    fluid_nodes = [
        node
        for node in nodes
        if node.category == "fluid_transport" or node.name in _FLUID_ENDPOINTS
    ]
    for i, source in enumerate(fluid_nodes):
        for target in fluid_nodes[i + 1 :]:
            if source.category == "fluid_transport" and target.category == "fluid_transport":
                radius = 1.1
            elif source.name == "steam-engine" or target.name == "steam-engine":
                radius = 3.2
            else:
                radius = 2.6
            if _distance(source, target) <= radius:
                edges.add(GraphEdge(source.node_id, target.node_id, "fluid_link"))
                edges.add(GraphEdge(target.node_id, source.node_id, "fluid_link"))

    # Electrical graph is intentionally conservative: pole-to-pole and
    # pole-to-consumer proximity, not an assertion of exact copper-wire state.
    poles = [node for node in nodes if node.category == "power"]
    consumers = [
        node
        for node in nodes
        if node.name in _POWER_CONSUMERS
    ]
    for i, pole in enumerate(poles):
        for other in poles[i + 1 :]:
            if _distance(pole, other) <= 9.0:
                edges.add(GraphEdge(pole.node_id, other.node_id, "power_backbone"))
                edges.add(GraphEdge(other.node_id, pole.node_id, "power_backbone"))
        for consumer in consumers:
            if _distance(pole, consumer) <= 4.0:
                edges.add(GraphEdge(pole.node_id, consumer.node_id, "power_supply"))

    material_relations = {"belt_flow", "pickup", "drop", "material_output"}
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.relation in material_relations:
            adjacency.setdefault(edge.source, set()).add(edge.target)

    fluid_adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if edge.relation == "fluid_link":
            fluid_adjacency.setdefault(edge.source, set()).add(edge.target)
    pump_ids = {node.node_id for node in nodes if node.name == "offshore-pump"}
    engine_ids = {
        node.node_id
        for node in nodes
        if node.name in {"steam-engine", "steam-turbine"}
    }
    steam_path_live = any(
        _reachable(fluid_adjacency, pump, engine_ids)
        for pump in pump_ids
    )

    producer_ids = {node.node_id for node in nodes if node.category == "extraction"}
    processor_ids = {node.node_id for node in nodes if node.category == "processing"}
    buffer_ids = {node.node_id for node in nodes if node.category == "buffer"}
    producers_to_processor = sum(
        _reachable(adjacency, producer, processor_ids)
        for producer in producer_ids
    )
    producers_to_buffer = sum(
        _reachable(adjacency, producer, buffer_ids)
        for producer in producer_ids
    )
    isolated_producers = sum(
        not adjacency.get(producer)
        for producer in producer_ids
    )

    categories: dict[str, int] = {}
    for node in nodes:
        categories[node.category] = categories.get(node.category, 0) + 1

    fuel_starved_entities = sum(
        node.status == "no_fuel"
        for node in nodes
        if node.category in {"extraction", "processing", "transfer", "energy"}
    )
    power_starved_entities = sum(
        node.status
        in {
            "no_power",
            "low_power",
            "not_plugged_in_electric_network",
            "not_connected",
        }
        for node in nodes
        if node.category in {"extraction", "processing", "transfer", "research"}
    )

    return {
        "nodes": [node.to_dict() for node in nodes],
        "edges": [
            edge.to_dict()
            for edge in sorted(edges, key=lambda row: (row.source, row.target, row.relation))
        ],
        "metrics": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "categories": categories,
            "producer_count": len(producer_ids),
            "producers_reaching_processor": producers_to_processor,
            "producers_reaching_buffer": producers_to_buffer,
            "isolated_producers": isolated_producers,
            "material_edge_count": sum(
                edge.relation in material_relations for edge in edges
            ),
            "power_edge_count": sum(
                edge.relation.startswith("power_") for edge in edges
            ),
            "fluid_edge_count": sum(
                edge.relation == "fluid_link" for edge in edges
            ),
            "steam_path_live": steam_path_live,
            "physical_processing_coverage": (
                producers_to_processor / len(producer_ids)
                if producer_ids
                else 0.0
            ),
            "fuel_starved_entities": fuel_starved_entities,
            "power_starved_entities": power_starved_entities,
        },
    }
