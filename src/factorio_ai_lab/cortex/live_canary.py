"""Read-only instrumentation helpers for controlled live Cortex canaries."""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from typing import Any

from factorio_ai_lab.cortex.delivery_actuator_dependency import (
    complete_delivery_actuator_dependency,
)
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.learning.factory_graph import MATERIAL_RELATIONS, build_factory_graph
from factorio_ai_lab.planning.placement import ResourceSurvey
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

CONFIRMATORY_SEEDS = frozenset(range(20261101, 20261111))


def validate_canary_seed(seed: int) -> None:
    if seed in CONFIRMATORY_SEEDS:
        raise ValueError(
            f"seed {seed} is reserved for confirmatory evaluation and cannot "
            "be used by a Cortex live canary"
        )


def delivery_power_capability(
    graph_metrics: dict[str, Any],
    *,
    fixture_power_operation: bool | None,
) -> dict[str, Any]:
    raw = graph_metrics.get("power_edge_count")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return {
            "available": None,
            "status": "missing",
            "evidence": {
                "path": "factory_graph.metrics.power_edge_count",
                "value": raw,
                "fixture_power_operation": fixture_power_operation,
            },
        }
    value = int(raw)
    if value == 0 and fixture_power_operation is False:
        return {
            "available": False,
            "status": "derived_unavailable",
            "evidence": {
                "path": "factory_graph.metrics.power_edge_count",
                "value": value,
                "fixture_power_operation": fixture_power_operation,
            },
        }
    return {
        "available": None,
        "status": (
            "network_exists_actuator_position_unmeasured"
            if value > 0
            else "power_capability_unmeasured"
        ),
        "evidence": {
            "path": "factory_graph.metrics.power_edge_count",
            "value": value,
            "fixture_power_operation": fixture_power_operation,
        },
    }


def complete_delivery_for_live_canary(
    prepared: Any,
    *,
    graph_metrics: dict[str, Any],
    catalog: RuntimeFactorioCatalog,
    inventory: dict[str, float],
    horizon_s: float,
):
    power = delivery_power_capability(
        graph_metrics,
        fixture_power_operation=False,
    )
    actuator = complete_delivery_actuator_dependency(
        prepared,
        catalog=catalog,
        inventory=inventory,
        electric_power_available=power["available"],
        horizon_s=horizon_s,
    )
    return power, actuator


def patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    return (
        (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0,
        (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0,
    )


def world_rows(namespace: Any, *, resources: bool) -> list[dict[str, Any]]:
    rows = namespace._save_entity_state(
        distance=500,
        player_entities=True,
        resource_entities=resources,
        items_on_ground=False,
        encode=False,
        compress=False,
    )
    return [row for row in rows if isinstance(row, dict)]


def available_inventory(rows: list[dict[str, Any]]) -> dict[str, float]:
    counts: Counter[str] = Counter()
    character_seen = False
    for row in rows:
        if str(row.get("name") or "") != "character":
            continue
        character_seen = True
        inventory = row.get("inventory")
        if not isinstance(inventory, dict):
            continue
        for name, value in inventory.items():
            if (
                isinstance(name, str)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and float(value) > 0
            ):
                counts[name] += float(value)
    if character_seen:
        counts["character"] = max(1.0, float(counts.get("character", 0.0)))
    return {name: float(count) for name, count in sorted(counts.items())}


def resource_survey_from_overview(payload: dict[str, Any]) -> ResourceSurvey:
    points = [
        {
            "name": str(point["name"]),
            "type": "resource",
            "position": {
                "x": float(point["x"]),
                "y": float(point["y"]),
            },
        }
        for point in payload.get("points", [])
        if isinstance(point, dict)
        and point.get("name")
        and isinstance(point.get("x"), (int, float))
        and isinstance(point.get("y"), (int, float))
    ]
    center = payload.get("center")
    radius = payload.get("radius")
    surveyed = None
    if isinstance(center, dict) and isinstance(radius, (int, float)):
        x = center.get("x")
        y = center.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            r = float(radius)
            surveyed = (
                float(x) - r,
                float(y) - r,
                float(x) + r,
                float(y) + r,
            )
    return ResourceSurvey.from_entities(points, surveyed=surveyed)


def structural_targets(graph: dict[str, Any]) -> list[str]:
    nodes = {
        str(row.get("id")): row
        for row in graph.get("nodes", [])
        if isinstance(row, dict) and row.get("id")
    }
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        if (
            isinstance(edge, dict)
            and edge.get("relation") in MATERIAL_RELATIONS
        ):
            adjacency[str(edge["source"])].add(str(edge["target"]))
    processors = {
        node_id
        for node_id, row in nodes.items()
        if row.get("category") == "processing"
    }

    def downstream(source: str) -> set[str]:
        seen = {source}
        queue = deque([source])
        reached: set[str] = set()
        while queue:
            current = queue.popleft()
            for target in adjacency.get(current, ()):
                if target in seen:
                    continue
                seen.add(target)
                reached.add(target)
                queue.append(target)
        return reached

    return [
        node_id
        for node_id, row in sorted(nodes.items())
        if row.get("category") == "extraction"
        and (reached := downstream(node_id))
        and not (reached & processors)
    ]


def entity_position(row: dict[str, Any]) -> tuple[float, float] | None:
    position = row.get("position")
    if not isinstance(position, dict):
        return None
    x = position.get("x")
    y = position.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def processor_row(
    prepared: Any,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    processor = str(prepared.preflight.get("processor") or "")
    placement = prepared.preflight.get("placement")
    if not isinstance(placement, dict):
        return None
    position = placement.get("position")
    if not isinstance(position, dict):
        return None
    x = position.get("x")
    y = position.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    target = (float(x), float(y))
    candidates = []
    for row in rows:
        if str(row.get("name") or "") != processor:
            continue
        pos = entity_position(row)
        if pos is None:
            continue
        distance = math.hypot(pos[0] - target[0], pos[1] - target[1])
        candidates.append((distance, row))
    if not candidates:
        return None
    distance, row = min(candidates, key=lambda item: item[0])
    return row if distance <= 1.0 else None


def craft_output_count(row: dict[str, Any] | None, product: str) -> float:
    if row is None:
        return 0.0
    output = row.get("craft_output")
    if not isinstance(output, list):
        return 0.0
    total = 0.0
    for stack in output:
        if (
            not isinstance(stack, dict)
            or str(stack.get("name") or "") != product
        ):
            continue
        count = stack.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            total += float(count)
    return total


def build_measurement_probe(observer: FactorioObserver):
    def measure(prepared: Any) -> dict[str, Any]:
        snapshot = observer.snapshot()
        if snapshot.get("connected") is not True:
            raise RuntimeError(
                "canonical world snapshot unavailable: "
                + str(snapshot.get("error") or "connected=false")
            )
        rows = [
            row
            for row in snapshot.get("entities", [])
            if isinstance(row, dict)
        ]
        graph = build_factory_graph(rows)
        metrics = graph.get("metrics", {})
        processor = processor_row(prepared, rows)
        exists = processor is not None
        product = str(prepared.preflight.get("product") or "")
        return {
            "producers_reaching_processor": metrics.get(
                "producers_reaching_processor"
            ),
            "physical_processing_coverage": metrics.get(
                "physical_processing_coverage"
            ),
            "processor_exists": exists,
            "processor_status": (
                None if processor is None else processor.get("status")
            ),
            "processor_output": craft_output_count(processor, product),
        }

    return measure
