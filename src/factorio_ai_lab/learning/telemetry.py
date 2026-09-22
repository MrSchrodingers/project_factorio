from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PRODUCTION_ITEMS = (
    "iron-ore",
    "iron-plate",
    "coal",
    "copper-ore",
    "copper-plate",
    "iron-gear-wheel",
    "copper-cable",
    "electronic-circuit",
    "automation-science-pack",
    "logistic-science-pack",
)
ENTITY_TYPES = (
    "burner-mining-drill",
    "electric-mining-drill",
    "stone-furnace",
    "assembling-machine-1",
    "transport-belt",
    "burner-inserter",
    "inserter",
    "lab",
    "steam-engine",
    "boiler",
    "small-electric-pole",
)
FAULT_STATUSES = (
    "no_fuel",
    "no_power",
    "no_ingredients",
    "full_output",
    "waiting_for_space_in_destination",
)
FEATURE_NAMES = (
    *(f"prod:{name}" for name in PRODUCTION_ITEMS),
    *(f"cons:{name}" for name in ("iron-ore", "coal", "copper-ore")),
    *(f"entity:{name}" for name in ENTITY_TYPES),
    *(f"status:{name}" for name in FAULT_STATUSES),
    "status:working",
    "progress",
    "capabilities",
    "dependency_debt",
    "coal_reserve",
    "coal_safety_stock",
    "external_dependencies",
)

CONTROL_NAMES = (
    "action:idle",
    "action:wait",
    "action:move",
    "action:logistics",
    "action:craft",
    "action:build",
    "action:research",
    "action:other",
    "action:elapsed_s",
)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _rate(
    production: Mapping[str, Any],
    item: str,
    key: str,
) -> float:
    series = production.get("series", {})
    if not isinstance(series, Mapping):
        return 0.0
    row = series.get(item, {})
    if not isinstance(row, Mapping):
        return 0.0
    raw = row.get(key, 0.0)
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def compact_telemetry_sample(
    *,
    timestamp: float,
    world: Mapping[str, Any],
    research: Mapping[str, Any],
    progression: Mapping[str, Any],
    production: Mapping[str, Any],
    action_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entities = world.get("entities", [])
    entity_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            entity_counter[str(entity.get("name", "unknown"))] += 1
            status_counter[str(entity.get("status", "unknown"))] += 1

    metrics = research.get("metrics", {})
    metrics = metrics if isinstance(metrics, Mapping) else {}
    accounting = research.get("resource_accounting", {})
    accounting = accounting if isinstance(accounting, Mapping) else {}
    exogenous = accounting.get("exogenous_inputs", {})
    exogenous = exogenous if isinstance(exogenous, Mapping) else {}
    coal_accounting = exogenous.get("coal", {})
    coal_accounting = coal_accounting if isinstance(coal_accounting, Mapping) else {}

    external_dependencies = 0
    for row in exogenous.values():
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status", "external"))
        if status not in {"retired", "internal", "self_sufficient"}:
            external_dependencies += 1

    achieved = progression.get("achieved", [])
    debt = progression.get("dependency_debt", [])
    arena = research.get("arena", {})
    arena = arena if isinstance(arena, Mapping) else {}

    features: dict[str, float] = {}
    for item in PRODUCTION_ITEMS:
        features[f"prod:{item}"] = _rate(production, item, "produced_rate")
    for item in ("iron-ore", "coal", "copper-ore"):
        features[f"cons:{item}"] = _rate(production, item, "consumed_rate")
    for entity in ENTITY_TYPES:
        features[f"entity:{entity}"] = float(entity_counter[entity])
    for status in FAULT_STATUSES:
        features[f"status:{status}"] = float(status_counter[status])
    features["status:working"] = float(status_counter["working"])
    features["progress"] = float(research.get("progress", 0.0) or 0.0)
    features["capabilities"] = float(len(achieved) if isinstance(achieved, list) else 0)
    features["dependency_debt"] = float(len(debt) if isinstance(debt, list) else 0)
    features["coal_reserve"] = float(
        metrics.get(
            "survival_coal_reserve",
            metrics.get(
                "coal_endogenous_stockpile",
                coal_accounting.get("endogenous_stockpile", 0.0),
            ),
        )
        or 0.0
    )
    features["coal_safety_stock"] = float(
        metrics.get(
            "coal_safety_stock_target",
            coal_accounting.get("safety_stock_target", 0.0),
        )
        or 0.0
    )
    features["external_dependencies"] = float(external_dependencies)

    critical_fault = (
        features["status:no_fuel"] > 0
        or features["status:no_power"] > 0
        or features["dependency_debt"] > 0
    )

    action = (
        action_context
        if isinstance(action_context, Mapping)
        else {}
    )
    action_payload = {
        "action_id": action.get("action_id"),
        "action_kind": str(action.get("action_kind") or "idle"),
        "state": str(action.get("state") or "idle"),
        "code_sha256": action.get("code_sha256"),
        "started_at": action.get("started_at"),
        "elapsed_s": float(action.get("elapsed_s", 0.0) or 0.0),
    }

    return {
        "timestamp": float(timestamp),
        "run_id": research.get("run_id"),
        "arena": arena.get("mode"),
        "stage": research.get("stage"),
        "tick": world.get("tick"),
        "connected": bool(world.get("connected", False)),
        "features": features,
        "action": action_payload,
        "critical_fault": bool(critical_fault),
    }


def feature_vector(sample: Mapping[str, Any]) -> list[float]:
    raw = sample.get("features", {})
    raw = raw if isinstance(raw, Mapping) else {}
    vector: list[float] = []
    for name in FEATURE_NAMES:
        value = raw.get(name, 0.0)
        value = float(value) if isinstance(value, (int, float)) else 0.0
        vector.append(value if math.isfinite(value) else 0.0)
    return vector


def control_vector(sample: Mapping[str, Any]) -> list[float]:
    action = sample.get("action", {})
    action = action if isinstance(action, Mapping) else {}
    kind = str(action.get("action_kind") or "idle")
    known = {"idle", "wait", "move", "logistics", "craft", "build", "research", "other"}
    if kind not in known:
        kind = "other"
    elapsed = action.get("elapsed_s", 0.0)
    elapsed = float(elapsed) if isinstance(elapsed, (int, float)) else 0.0
    elapsed = max(0.0, min(elapsed, 600.0)) / 600.0
    values = {
        f"action:{name}": float(kind == name)
        for name in sorted(known)
    }
    values["action:elapsed_s"] = elapsed
    return [values.get(name, 0.0) for name in CONTROL_NAMES]
